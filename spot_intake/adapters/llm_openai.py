"""LLM adapters: an OpenAI-compatible HTTP adapter and a null adapter.

The seam is at the domain-extraction level (places / fish / keywords / quality),
not the HTTP level — callers never see raw model output, and NullLlm gives
rule-only operation (the CLI's --no-llm path) without flag threading.
"""

from __future__ import annotations

import json
import re
import sys
import urllib.error
import urllib.request

from spot_intake.extract import (
    dedupe_places,
    format_comments_for_llm,
    normalize_comment_keyword,
    normalize_comment_keyword_category,
    normalize_fish_species,
    normalize_quality_score,
)
from spot_intake.vocabulary import COMMENT_KEYWORD_CATEGORIES, FISH_PATTERNS


def log_llm_debug(message: str, enabled: bool = True) -> None:
    if enabled:
        print(f"[llm] {message}", file=sys.stderr, flush=True)


class OpenaiLlm:
    """Llm adapter over an OpenAI-compatible /chat/completions endpoint."""

    def __init__(self, url: str, debug: bool = True) -> None:
        self.url = url
        self.debug = debug

    # -- raw call --------------------------------------------------------------

    def _chat_content(
        self,
        prompt: str,
        log_prefix: str = "llm",
        system_prompt: str = "你是信息抽取器，只输出合法 JSON，不要解释。",
    ) -> str:
        """One chat completion call; returns the raw message content or "" on failure."""
        payload = {
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            "stream": False,
            "temperature": 0,
        }
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            self.url,
            data=body,
            headers={"Content-Type": "application/json", "Accept": "application/json", "User-Agent": "Mozilla/5.0"},
            method="POST",
        )
        debug = self.debug
        log_llm_debug(f"{log_prefix} request url={self.url} prompt_chars={len(prompt)} body_bytes={len(body)}", debug)
        log_llm_debug(f"{log_prefix} input_begin\n{prompt[:6000]}\ninput_end", debug)
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                raw = resp.read().decode("utf-8", "replace")
                log_llm_debug(f"{log_prefix} response status={resp.status} bytes={len(raw.encode('utf-8'))}", debug)
            data = json.loads(raw)
            choice = data["choices"][0]
            content = choice["message"]["content"].strip()
            log_llm_debug(
                f"{log_prefix} model={data.get('model', '')} finish_reason={choice.get('finish_reason', '')} output_chars={len(content)}",
                debug,
            )
            return content
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:500]
            log_llm_debug(f"{log_prefix} http_error status={exc.code} detail={detail!r}", debug)
            return ""
        except (urllib.error.URLError, TimeoutError, KeyError, IndexError, json.JSONDecodeError) as exc:
            log_llm_debug(f"{log_prefix} error type={type(exc).__name__} detail={exc}", debug)
            return ""

    def _chat_json_array(
        self,
        prompt: str,
        log_prefix: str = "llm",
        system_prompt: str = "你是信息抽取器，只输出合法 JSON，不要解释。",
    ) -> list[object]:
        content = self._chat_content(prompt, log_prefix, system_prompt)
        if not content:
            return []
        # Some models may wrap JSON in markdown or add prose; salvage the first JSON array.
        match = re.search(r"\[[\s\S]*\]", content)
        if not match:
            log_llm_debug(f"{log_prefix} no_json_array content_preview={content[:200]!r}", self.debug)
            return []
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            log_llm_debug(f"{log_prefix} json_parse_error detail={exc} content_preview={content[:200]!r}", self.debug)
            return []
        if not isinstance(parsed, list):
            log_llm_debug(f"{log_prefix} unexpected_json_type type={type(parsed).__name__}", self.debug)
            return []
        return parsed

    def _chat_json_object(
        self,
        prompt: str,
        log_prefix: str = "llm",
        system_prompt: str = "你是信息抽取器，只输出合法 JSON，不要解释。",
    ) -> dict:
        content = self._chat_content(prompt, log_prefix, system_prompt)
        if not content:
            return {}
        match = re.search(r"\{[\s\S]*\}", content)
        if not match:
            log_llm_debug(f"{log_prefix} no_json_object content_preview={content[:200]!r}", self.debug)
            return {}
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            log_llm_debug(f"{log_prefix} json_parse_error detail={exc} content_preview={content[:200]!r}", self.debug)
            return {}
        if not isinstance(parsed, dict):
            log_llm_debug(f"{log_prefix} unexpected_json_type type={type(parsed).__name__}", self.debug)
            return {}
        return parsed

    # -- Llm protocol ------------------------------------------------------------

    def extract_places(self, text: str, city: str) -> list[str]:
        prompt = f"""从下面抖音钓鱼视频文本中提取实际地名/钓点候选。
要求：
- 只返回 JSON 数组，例如 [\"野芷湖公园\",\"东荆河\"]
- 优先提取河流、湖泊、水库、公园、村/桥/闸/江滩等可地理编码的地点
- 钓点不在上述类别时，可提取文本明确给出的导航地标（如展馆、泵站、码头、停车场、水库管理所）
- 不要返回泛词（钓点、野钓、附近）、人名、鱼种、装备、城市名本身
- 若无明确地点返回 []
- 城市上下文：{city}

文本：
{text[:6000]}"""
        parsed = self._chat_json_array(prompt, "place", "你是地名抽取器，只输出合法 JSON，不要解释。")
        places = dedupe_places([p for p in parsed if isinstance(p, str)])
        log_llm_debug(f"place places={places}", self.debug)
        return places

    def extract_fish_species(self, text: str) -> list[str]:
        known = "、".join(FISH_PATTERNS.keys())
        prompt = f"""从下面抖音钓鱼视频文本中提取明确出现的鱼种。
要求：
- 只返回 JSON 数组，例如 [\"黄尾鲴\",\"鲫鱼\"]
- 将俗称归一化为常见鱼名；已知候选包括：{known}
- 只有文本明确提到才返回；不要凭地点、饵料、钓法推测
- 不要返回地名、装备、饵料、斤数、钓点、野钓、空军等非鱼种词
- 若无明确鱼种返回 []

文本：
{text[:6000]}"""
        parsed = self._chat_json_array(prompt, "fish", "你是鱼种抽取器，只输出合法 JSON，不要解释。")
        raw: list[str] = []
        for item in parsed:
            if isinstance(item, str):
                raw.append(item)
            elif isinstance(item, dict):
                value = item.get("name") or item.get("species") or item.get("fish") or item.get("鱼种")
                if isinstance(value, str):
                    raw.append(value)
        species = normalize_fish_species(raw)
        log_llm_debug(f"fish species={species}", self.debug)
        return species

    def extract_transcript_places(self, transcript: str, city: str) -> list[str]:
        prompt = f"""从下面抖音钓鱼视频的语音转写文本（ASR 结果）中提取实际地名/钓点候选。
要求：
- 只返回 JSON 数组，例如 [\"野芷湖公园\",\"东荆河\"]
- 优先提取河流、湖泊、水库、公园、村/桥/闸/江滩等可地理编码的地点
- 钓点不在上述类别时，可提取文本明确给出的导航地标（如展馆、泵站、码头、停车场、水库管理所）
- 不要返回泛词（钓点、野钓、附近）、人名、鱼种、装备、城市名本身
- ASR 可能有谐音错字：若某地名疑似 {city} 某水系/地名的谐音，结合钓鱼语境纠正后再输出（如“富河”→“府河”）；不确定时保留原文
- 若无明确地点返回 []
- 城市上下文：{city}

转写文本：
{transcript[:6000]}"""
        parsed = self._chat_json_array(prompt, "transcript-place", "你是地名抽取器，只输出合法 JSON，不要解释。")
        places = dedupe_places([p for p in parsed if isinstance(p, str)])
        log_llm_debug(f"transcript-place places={places}", self.debug)
        return places

    def extract_transcript_fish_species(self, transcript: str) -> list[str]:
        # Fish names normalize against the vocabulary lexicon either way, so the
        # page-text prompt doubles as the transcript prompt.
        return self.extract_fish_species(transcript)

    def summarize_transcript(self, transcript: str) -> dict:
        prompt = f"""下面是抖音钓鱼视频的语音转写文本（ASR 结果，可能含错字）。请输出一个 JSON 对象：
{{"summary": "80字以内的中文摘要，面向钓友，说明地点/鱼种/钓法/渔获", "extras": {{"钓法/饵料": "…", "渔获": "…", "出钓时间": "…", "其他": "…"}}}}
要求：
- 只输出合法 JSON，不要解释
- extras 只收录文本明确提到的信息，没有的键直接省略，不要编造
- 文本没有实质内容时返回 {{"summary": "", "extras": {{}}}}

转写文本：
{transcript[:6000]}"""
        parsed = self._chat_json_object(prompt, "transcript-summary", "你是钓鱼视频摘要器，只输出合法 JSON，不要解释。")
        summary = str(parsed.get("summary", "") or "")
        extras = parsed.get("extras")
        result = {"summary": summary, "extras": extras if isinstance(extras, dict) else {}}
        log_llm_debug(f"transcript-summary chars={len(summary)} extras_keys={list(result['extras'])}", self.debug)
        return result

    def extract_comment_places(self, comments: list[dict], city: str) -> list[dict]:
        if not comments:
            return []
        prompt = f"""从下面抖音钓鱼视频评论中提取评论明确提到的实际钓点/地名。
要求：
- 只返回 JSON 数组，每项格式：{{"place_name":"东湖","comment_indexes":[7],"evidence":"东湖有个地方特别多","confidence":0.8}}
- 地名必须来自评论文本，不要根据视频标题或常识补全
- 优先提取河流、湖泊、水库、公园、桥、闸、江滩、村、湾、港等可地理编码地点
- 钓点不在上述类别时，可提取文本明确给出的导航地标（如展馆、泵站、码头、停车场、水库管理所）
- 如果评论把地名和鱼种/鱼情连在一起，也要拆出地名，例如“月湖大翘嘴”应返回“月湖”
- 不要返回泛词（这里、那里、钓点、位置、免费停车场）、人名、鱼种、装备、城市名本身
- comment_indexes 使用评论前的方括号编号
- 若无明确地点返回 []
- 城市上下文：{city}

评论：
{format_comments_for_llm(comments)}"""
        parsed = self._chat_json_array(prompt, "comment-place", "你是评论地名抽取器，只输出合法 JSON，不要解释。")
        clues: list[dict] = []
        seen: set[str] = set()
        for item in parsed:
            if not isinstance(item, dict):
                continue
            place = item.get("place_name") or item.get("place") or item.get("地点") or item.get("钓点")
            if not isinstance(place, str):
                continue
            places = dedupe_places([place])
            if not places:
                continue
            place = places[0]
            if place in seen:
                continue
            seen.add(place)
            indexes = item.get("comment_indexes") or item.get("comment_ids") or item.get("indexes") or []
            if not isinstance(indexes, list):
                indexes = []
            clean_indexes: list[int] = []
            for value in indexes:
                try:
                    idx = int(value)
                except (TypeError, ValueError):
                    continue
                if 1 <= idx <= len(comments) and idx not in clean_indexes:
                    clean_indexes.append(idx)
            try:
                confidence = float(item.get("confidence", 0.75))
            except (TypeError, ValueError):
                confidence = 0.75
            evidence = str(item.get("evidence") or item.get("source_text") or "").strip()
            if not evidence and clean_indexes:
                evidence = "；".join(str(comments[i - 1].get("text", "")) for i in clean_indexes[:3])
            clues.append({
                "place_name": place,
                "comment_indexes": clean_indexes,
                "comment_ids": [comments[i - 1].get("comment_id") for i in clean_indexes if comments[i - 1].get("comment_id")],
                "evidence": evidence,
                "confidence": max(0.0, min(confidence, 1.0)),
            })
        log_llm_debug(f"comment-place clues={clues}", self.debug)
        return clues

    def extract_comment_keywords(self, comments: list[dict], city: str, group_size: int = 20) -> list[dict]:
        """Extract concise categorized keywords from comments using batched LLM calls."""
        if not comments or group_size <= 0:
            return []
        category_text = "\n".join(f"- {key}: {label}" for key, label in COMMENT_KEYWORD_CATEGORIES.items())
        keywords: list[dict] = []
        seen: set[tuple[int, str, str]] = set()
        for start in range(0, len(comments), group_size):
            group = comments[start : start + group_size]
            group_index = start // group_size + 1
            prompt = f"""从下面抖音钓鱼视频评论中抽取简洁关键词，并按类别结构化。
要求：
- 只返回 JSON 数组；每项格式：{{"comment_index":7,"keywords":[{{"keyword":"有口","category":"fish_condition","confidence":0.8,"evidence":"今天有口"}}]}}
- keyword 必须短小，优先 2-6 个汉字；不要输出完整句子
- 只抽评论明确表达的信息，不要依据疑问句或祈使句
- 每条评论最多 5 个关键词；无有效信息的评论不要返回
- category 只能使用下列英文枚举之一：
{category_text}
- 常见归一化示例：有口/口好/连竿 -> 有口；没口/空军 -> 没口或空军；不让钓/赶人/保安 -> 禁钓或保安赶人；好停车/免费停车 -> 停车方便
- 地名、鱼种若明确出现也要抽取；城市名本身（如 {city}）不要作为关键词

评论（方括号为全局评论编号）：
{format_comments_for_llm(group, max_chars=10000, start_index=start + 1)}"""
            parsed = self._chat_json_array(
                prompt,
                f"comment-keyword-{group_index}",
                "你是钓鱼评论关键词抽取器，只输出合法 JSON，不要解释。",
            )
            for item in parsed:
                if not isinstance(item, dict):
                    continue
                try:
                    comment_index = int(item.get("comment_index") or item.get("index") or item.get("comment_id") or 0)
                except (TypeError, ValueError):
                    continue
                if not (1 <= comment_index <= len(comments)):
                    continue
                raw_keywords = item.get("keywords") or item.get("关键词") or []
                if isinstance(raw_keywords, dict):
                    raw_keywords = [raw_keywords]
                if isinstance(raw_keywords, str):
                    raw_keywords = [{"keyword": raw_keywords}]
                if not isinstance(raw_keywords, list):
                    continue
                for raw_kw in raw_keywords[:8]:
                    if isinstance(raw_kw, str):
                        raw_kw = {"keyword": raw_kw}
                    if not isinstance(raw_kw, dict):
                        continue
                    keyword = normalize_comment_keyword(raw_kw.get("keyword") or raw_kw.get("word") or raw_kw.get("name") or raw_kw.get("关键词"))
                    category = normalize_comment_keyword_category(raw_kw.get("category") or raw_kw.get("type") or raw_kw.get("类别"))
                    if not keyword or not category:
                        continue
                    try:
                        confidence = float(raw_kw.get("confidence", 0.75))
                    except (TypeError, ValueError):
                        confidence = 0.75
                    key = (comment_index, keyword, category)
                    if key in seen:
                        continue
                    seen.add(key)
                    comment = comments[comment_index - 1]
                    evidence = str(raw_kw.get("evidence") or raw_kw.get("source_text") or comment.get("text", "")).strip()
                    keywords.append({
                        "comment_index": comment_index,
                        "comment_id": comment.get("comment_id"),
                        "keyword": keyword,
                        "category": category,
                        "confidence": max(0.0, min(confidence, 1.0)),
                        "evidence": evidence[:200],
                    })
        log_llm_debug(f"comment-keyword keywords={keywords}", self.debug)
        return keywords

    def score_comment_quality(self, comments: list[dict], group_size: int = 5) -> list[dict]:
        """Score fishing-spot quality from comment groups; one LLM call per group."""
        if not comments or group_size <= 0:
            return []
        scores: list[dict] = []
        for start in range(0, len(comments), group_size):
            group = comments[start : start + group_size]
            group_index = start // group_size + 1
            group_text = format_comments_for_llm(group, start_index=start + 1)
            prompt = f"""请根据下面这一组抖音钓鱼视频评论，给评论反映的“钓点质量”打分。
评分标准：1=很差/禁钓/无鱼/不建议，2=偏差，3=一般或信息不足，4=较好，5=很好/鱼情好/交通停车方便/可钓性强。
要求：
- 只返回 JSON 数组，且只有 1 项：例如 [{{"score_1_5":4,"confidence":0.7,"summary":"鱼情还行且可钓","evidence":"已验证，可以钓鱼"}}]
- score_1_5 必须是 1 到 5 的原始评分；程序会归一化到 0 到 1 后写入钓点评分
- 只能依据评论内容，不要根据视频标题或常识推测
- 如果这一组没有任何钓点质量信息，score_1_5 返回 3，confidence 不高于 0.3，并说明“信息不足”
- evidence 摘录关键评论，summary 简短中文概括

评论组（全局评论编号）：
{group_text}"""
            parsed = self._chat_json_array(prompt, f"comment-quality-{group_index}", "你是钓点评价分析器，只输出合法 JSON，不要解释。")
            item = parsed[0] if parsed and isinstance(parsed[0], dict) else {}
            try:
                raw_score = float(item.get("score_1_5", item.get("score", 3)))
            except (TypeError, ValueError):
                raw_score = 3.0
            raw_score = max(1.0, min(raw_score, 5.0))
            try:
                confidence = float(item.get("confidence", 0.3))
            except (TypeError, ValueError):
                confidence = 0.3
            scores.append({
                "group_index": group_index,
                "comment_ids": [c.get("comment_id") for c in group if c.get("comment_id")],
                "score_1_5": raw_score,
                "normalized_score": normalize_quality_score(raw_score),
                "confidence": max(0.0, min(confidence, 1.0)),
                "summary": str(item.get("summary") or "").strip(),
                "evidence": str(item.get("evidence") or "").strip(),
            })
        log_llm_debug(f"comment-quality scores={scores}", self.debug)
        return scores


class NullLlm:
    """Rule-only operation: every LLM extraction returns empty, so the pipeline
    runs on rule-based fallbacks (the CLI's --no-llm path)."""

    def extract_places(self, text: str, city: str) -> list[str]:
        return []

    def extract_fish_species(self, text: str) -> list[str]:
        return []

    def extract_comment_places(self, comments: list[dict], city: str) -> list[dict]:
        return []

    def extract_comment_keywords(self, comments: list[dict], city: str, group_size: int = 20) -> list[dict]:
        return []

    def score_comment_quality(self, comments: list[dict], group_size: int = 5) -> list[dict]:
        return []

    def extract_transcript_places(self, transcript: str, city: str) -> list[str]:
        return []

    def extract_transcript_fish_species(self, transcript: str) -> list[str]:
        return []

    def summarize_transcript(self, transcript: str) -> dict:
        return {"summary": "", "extras": {}}
