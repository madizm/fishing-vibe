#!/usr/bin/env python3
"""Batch collector MVP for Douyin fishing spot videos.

Pipeline:
1. opencli douyin search <keyword>
2. opencli browser open/extract each video URL
3. extract title, publish time, place candidates, fish species
4. baidu geocode
5. insert into SQLite
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import sqlite3
import subprocess
import shutil
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "fishing_spots.sqlite"
GEOCODE_SCRIPT = ROOT / ".agents" / "skills" / "geocode" / "geocode.py"

# Fallback tokens when the local OpenAI-compatible LLM is unavailable.
# The primary extractor below asks the LLM to return place names from title/description/page text.
PLACE_PATTERNS = [
    "野芷湖公园", "野芷湖", "东荆河", "倒水河", "汉江", "长江", "府河", "滠水河",
    "汤逊湖", "梁子湖", "后官湖", "严西湖", "严东湖", "金银湖", "墨水湖", "南湖",
    "蔡甸江滩", "汉口江滩", "武昌江滩", "联丰村", "走马岭水厂",
]
COMMENT_PLACE_HINTS = [
    "江", "河", "湖", "水库", "江滩", "闸", "桥", "泵站", "水厂", "码头", "村", "湾", "港", "沟", "渠", "公园",
]
COMMENT_NOISE = {"全部评论", "留下你的精彩评论吧", "大家都在搜：", "分享", "回复", "作者", "加载中", "关注", "推荐视频"}
LLM_TEXT_NOISE = {
    "读屏标签已关闭", "精选", "推荐", "搜索", "关注", "朋友", "我的", "直播", "放映厅", "短剧", "小游戏",
    "下载抖音精选", "播放", "进入全屏H", "网页全屏Y", "截图", "小窗模式U", "字幕", "不 开启", "不开启",
    "稍后再看L", "倍速", "高清 1080P", "高清 720P", "智能", "清屏", "清屏J", "连播", "自动连播K",
    "听抖音", "重播", "举报", "推荐视频", "点击按住可拖动视频", "3s 后播放", "3s 后播放下一个视频",
    "3s 后循环播放当前视频", "全部评论", "留下你的精彩评论吧",
}
LLM_TEXT_KEEP_HINTS = [
    "#", "钓", "鱼", "江", "河", "湖", "水库", "江滩", "闸", "桥", "泵站", "水厂", "码头", "村", "湾", "港",
    "章节要点", "引言", "鱼情", "钓获", "发布时间", "作者", "粉丝", "获赞",
]

# Fish species aliases commonly appearing in Wuhan fishing videos.
# Keys are canonical names persisted into DB; values are surface forms used by
# rules and by the LLM normalizer below. Keep longer/more specific aliases first
# where ambiguity exists (e.g. 青尾鲴 before 青尾).
FISH_PATTERNS = {
    "黄尾鲴": ["黄尾鲴", "黄尾", "黄片", "黄尾巴"],
    "青尾鲴": ["青尾鲴", "青尾鲴鱼", "青尾", "青尾巴"],
    "鲫鱼": ["工程鲫", "板鲫", "大板鲫", "斤鲫", "土鲫", "野鲫", "鲫鱼"],
    "鲤鱼": ["大鲤鱼", "巨鲤", "拐子", "鲤鱼"],
    "草鱼": ["草鱼", "草混", "草棒"],
    "鳊鱼": ["武昌鱼", "鳊鱼"],
    "翘嘴": ["翘嘴红鲌", "大翘嘴", "翘壳", "翘嘴", "白鱼"],
    "罗非鱼": ["罗非鱼", "非洲鲫", "罗非"],
    "鲢鳙": ["花鲢", "白鲢", "胖头鱼", "大头鱼", "鲢鳙", "鲢鱼", "鳙鱼"],
    "鲮鱼": ["土鲮", "麦鲮", "泰鲮", "小鲮鱼", "鲮鱼"],
    "黑鱼": ["乌鳢", "乌鱼", "财鱼", "黑鱼"],
    "鳜鱼": ["桂鱼", "季花鱼", "鳜鱼"],
    "黄颡鱼": ["黄颡鱼", "黄骨鱼", "昂刺鱼", "黄辣丁", "黄鸭叫", "黄骨", "黄颡"],
    "鲶鱼": ["鲶鱼", "塘鲺", "胡子鲶"],
    "鲈鱼": ["鲈鱼", "海鲈", "七星鲈"],
    "红尾": ["红尾", "红尾鱼"],
    "马口": ["马口", "马口鱼"],
    "白条": ["白条", "餐条", "参条", "蓝刀"],
}
DEFAULT_LLM_URL = os.getenv("OPENAI_BASE_URL", "http://100.90.54.85:8080/v1").rstrip("/") + "/chat/completions"


def run(cmd: list[str], timeout: int = 120) -> str:
    resolved = cmd[:]
    exe = shutil.which(resolved[0])
    if exe:
        resolved[0] = exe
    p = subprocess.run(resolved, cwd=ROOT, text=True, capture_output=True, timeout=timeout)
    if p.returncode != 0:
        raise RuntimeError(f"command failed: {' '.join(cmd)}\nSTDOUT:\n{p.stdout}\nSTDERR:\n{p.stderr}")
    return p.stdout


def init_db(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS videos (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      platform TEXT DEFAULT 'douyin',
      keyword TEXT,
      title TEXT,
      url TEXT UNIQUE,
      author TEXT,
      publish_time TEXT,
      raw_text TEXT,
      collected_at TEXT
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS fishing_spots (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      video_id INTEGER,
      place_name TEXT,
      query_name TEXT,
      longitude REAL,
      latitude REAL,
      fish_species TEXT,
      fish_species_source TEXT,
      fish_confidence REAL,
      geocode_score INTEGER,
      geocode_level TEXT,
      confidence REAL,
      source_type TEXT,
      source_text TEXT,
      created_at TEXT,
      FOREIGN KEY(video_id) REFERENCES videos(id)
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS video_comments (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      video_id INTEGER,
      author TEXT,
      text TEXT,
      comment_time TEXT,
      comment_time_raw TEXT,
      comment_time_standard TEXT,
      ip_location TEXT,
      is_author INTEGER DEFAULT 0,
      raw_json TEXT,
      collected_at TEXT,
      UNIQUE(video_id, author, text, comment_time_raw),
      FOREIGN KEY(video_id) REFERENCES videos(id)
    )""")
    # No separate comment_quality_scores table: normalized comment quality is
    # written directly onto fishing_spots. Drop the legacy derived table if it exists.
    conn.execute("DROP TABLE IF EXISTS comment_quality_scores")
    columns = {row[1] for row in conn.execute("PRAGMA table_info(fishing_spots)")}
    if "fish_species" not in columns:
        conn.execute("ALTER TABLE fishing_spots ADD COLUMN fish_species TEXT")
    if "fish_species_source" not in columns:
        conn.execute("ALTER TABLE fishing_spots ADD COLUMN fish_species_source TEXT")
    if "fish_confidence" not in columns:
        conn.execute("ALTER TABLE fishing_spots ADD COLUMN fish_confidence REAL")
    if "source_type" not in columns:
        conn.execute("ALTER TABLE fishing_spots ADD COLUMN source_type TEXT")
    if "quality_score" not in columns:
        conn.execute("ALTER TABLE fishing_spots ADD COLUMN quality_score REAL")
    if "quality_score_source" not in columns:
        conn.execute("ALTER TABLE fishing_spots ADD COLUMN quality_score_source TEXT")
    if "quality_score_detail" not in columns:
        conn.execute("ALTER TABLE fishing_spots ADD COLUMN quality_score_detail TEXT")
    conn.execute("UPDATE fishing_spots SET source_type='video_text' WHERE source_type IS NULL OR source_type='' ")


def search(keyword: str, limit: int) -> list[dict]:
    out = run(["opencli", "douyin", "search", keyword, "--limit", str(limit), "-f", "json"], timeout=180)
    return json.loads(out)


def extract_video(url: str, session: str) -> dict:
    run(["opencli", "browser", session, "open", url], timeout=120)
    out = run(["opencli", "browser", session, "extract", "--chunk-size", "10000"], timeout=120)
    return json.loads(out)


def browser_eval(session: str, js: str) -> object:
    out = run(["opencli", "browser", session, "eval", js], timeout=90)
    return json.loads(out)


def _days_in_month(year: int, month: int) -> int:
    if month == 12:
        next_month = datetime(year + 1, 1, 1)
    else:
        next_month = datetime(year, month + 1, 1)
    return (next_month - timedelta(days=1)).day


def _shift_months(dt: datetime, months: int) -> datetime:
    month_index = dt.month - 1 + months
    year = dt.year + month_index // 12
    month = month_index % 12 + 1
    day = min(dt.day, _days_in_month(year, month))
    return dt.replace(year=year, month=month, day=day)


def parse_douyin_comment_time(value: str, now: datetime | None = None) -> str:
    """Parse Douyin's approximate comment time into YYYY-MM-DD HH:MM:SS.

    Relative times such as "1周前" and "8月前" are approximate: weeks use
    7-day deltas, while months/years use calendar month shifts with day clamping.
    """
    now = now or datetime.now()
    text = re.sub(r"\s+", " ", str(value or "").strip())
    text = text.split("·", 1)[0].strip()
    if not text:
        return ""

    if text == "刚刚":
        return now.strftime("%Y-%m-%d %H:%M:%S")

    relative_units = [
        (r"^(\d+)分钟前$", lambda n: now - timedelta(minutes=n)),
        (r"^(\d+)小时前$", lambda n: now - timedelta(hours=n)),
        (r"^(\d+)天前$", lambda n: now - timedelta(days=n)),
        (r"^(\d+)周前$", lambda n: now - timedelta(weeks=n)),
        (r"^(\d+)月前$", lambda n: _shift_months(now, -n)),
        (r"^(\d+)年前$", lambda n: _shift_months(now, -12 * n)),
    ]
    for pattern, convert in relative_units:
        m = re.fullmatch(pattern, text)
        if m:
            return convert(int(m.group(1))).strftime("%Y-%m-%d %H:%M:%S")

    m = re.fullmatch(r"^(今天|昨天)(?:\s+(\d{1,2}:\d{2}))?$", text)
    if m:
        base = now.date() - timedelta(days=1 if m.group(1) == "昨天" else 0)
        hh, mm = (m.group(2) or now.strftime("%H:%M")).split(":")
        return datetime(base.year, base.month, base.day, int(hh), int(mm)).strftime("%Y-%m-%d %H:%M:%S")

    m = re.fullmatch(r"^(\d{4})-(\d{1,2})-(\d{1,2})(?:\s+(\d{1,2}:\d{2}))?$", text)
    if m:
        hh, mm = (m.group(4) or "00:00").split(":")
        return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), int(hh), int(mm)).strftime("%Y-%m-%d %H:%M:%S")

    m = re.fullmatch(r"^(\d{1,2})-(\d{1,2})(?:\s+(\d{1,2}:\d{2}))?$", text)
    if m:
        hh, mm = (m.group(3) or "00:00").split(":")
        parsed = datetime(now.year, int(m.group(1)), int(m.group(2)), int(hh), int(mm))
        if parsed > now:
            parsed = parsed.replace(year=parsed.year - 1)
        return parsed.strftime("%Y-%m-%d %H:%M:%S")

    return ""


def extract_video_comments(session: str, scrolls: int = 0, wait_seconds: float = 2.0, max_comments: int = 100) -> list[dict]:
    """Extract visible Douyin video comments with their visible comment time.

    The Douyin DOM uses generated class names, so this method anchors on comment
    metadata time spans (e.g. "6小时前·湖北") and parses the nearest comment block.
    It returns visible top-level comments and visible replies currently loaded in
    the browser session.
    """
    if max_comments < 0:
        raise ValueError("max_comments must be >= 0")
    if max_comments == 0:
        return []
    for _ in range(scrolls):
        run(["opencli", "browser", session, "scroll", "down", "--amount", "1200"], timeout=60)
        if wait_seconds:
            time.sleep(wait_seconds)
    js = f"""(() => {{
      const maxComments = {max_comments};
      const timeRe = /^(?:刚刚|\\d+分钟前|\\d+小时前|\\d+天前|\\d+周前|\\d+月前|\\d+年前|昨天|今天|\\d{{1,2}}-\\d{{1,2}}|\\d{{4}}-\\d{{1,2}}-\\d{{1,2}})(?:\\s+\\d{{1,2}}:\\d{{2}})?(?:·[^\\n]+)?$/;
      const noise = new Set(['...', '作者赞过', '置顶', '分享', '回复']);
      const normalize = (s) => String(s || '').replace(/[\\u200b\\ufeff]/g, '').replace(/\\s+/g, ' ').trim();
      const parseTime = (value) => {{
        const text = normalize(value);
        const m = text.match(/^(.*?)(?:·(.+))?$/);
        return {{ raw: text, time: normalize(m && m[1] || text), ip: normalize(m && m[2] || '') }};
      }};
      const spans = Array.from(document.querySelectorAll('span'))
        .filter((span) => timeRe.test(normalize(span.innerText || span.textContent || '')));
      const records = [];
      const seen = new Set();
      for (const span of spans) {{
        const block = span.parentElement && span.parentElement.parentElement;
        if (!block) continue;
        const lines = (block.innerText || '')
          .split(/\\n+/)
          .map(normalize)
          .filter(Boolean);
        const timeText = normalize(span.innerText || span.textContent || '');
        const timeIndex = lines.findIndex((line) => line === timeText);
        if (timeIndex <= 0) continue;
        let before = lines.slice(0, timeIndex).filter((line) => !noise.has(line));
        const isAuthor = before.includes('作者');
        before = before.filter((line) => line !== '作者');
        const author = before.shift() || '';
        const text = normalize(before.join(''));
        if (!author || !text || text.length > 500) continue;
        const parsedTime = parseTime(timeText);
        const key = author + '|' + text + '|' + parsedTime.raw;
        if (seen.has(key)) continue;
        seen.add(key);
        records.push({{
          author,
          text,
          comment_time: parsedTime.time,
          comment_time_raw: parsedTime.raw,
          ip_location: parsedTime.ip,
          is_author: isAuthor,
        }});
        if (records.length >= maxComments) break;
      }}
      return records;
    }})()"""
    # opencli browser eval is more reliable with one-line expressions.
    js = " ".join(line.strip() for line in js.splitlines())
    data = browser_eval(session, js)
    comments = data if isinstance(data, list) else []
    normalized: list[dict] = []
    parsed_at = datetime.now()
    for item in comments:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text", "")).strip()
        if not text:
            continue
        comment_time_raw = str(item.get("comment_time_raw", "")).strip()
        comment_time = str(item.get("comment_time", "")).strip()
        normalized.append({
            "author": str(item.get("author", "")).strip(),
            "text": text,
            "comment_time": comment_time,
            "comment_time_raw": comment_time_raw,
            "comment_time_standard": parse_douyin_comment_time(comment_time_raw or comment_time, now=parsed_at),
            "ip_location": str(item.get("ip_location", "")).strip(),
            "is_author": bool(item.get("is_author", False)),
        })
    return normalized


def is_comment_candidate(line: str) -> bool:
    if line in COMMENT_NOISE or line == "...":
        return False
    if re.fullmatch(r"\d+", line) or re.fullmatch(r"\d{1,2}:\d{2}", line):
        return False
    if re.search(r"\d+天前|小时前|分钟前|·湖北|·武汉", line):
        return False
    if len(line) < 2 or len(line) > 100:
        return False
    return any(hint in line for hint in COMMENT_PLACE_HINTS) or "钓点" in line or "钓位" in line or "哪里" in line or "位置" in line


def extract_comment_place_names(line: str, city: str) -> list[str]:
    candidates: list[str] = []
    for pattern in [
        r"([\u4e00-\u9fa5]{2,12}(?:江滩|水库|水厂|泵站|公园|闸|桥|码头|江|河|湖|村|湾|港|沟|渠))",
        r"([\u4e00-\u9fa5]{2,8}钓点)",
        r"([\u4e00-\u9fa5]{2,8}钓位)",
    ]:
        for m in re.finditer(pattern, line):
            name = m.group(1).strip(" ，,。.!！?？")
            name = re.sub(r"^(湖北省|武汉市|武汉|湖北|去|到|在)", "", name)
            name = re.sub(r"(黄尾钓点|钓点|钓位)$", "", name)
            if name and len(name) >= 2 and name not in candidates and name not in {city, "同款", "哪里"}:
                candidates.append(name)
    return _dedupe_places(candidates)


def extract_comment_spot_clues(session: str, city: str, scrolls: int = 0, wait_seconds: float = 2.0) -> list[dict]:
    comments = extract_video_comments(session, scrolls=scrolls, wait_seconds=wait_seconds, max_comments=200)
    clues: list[dict] = []
    for comment in comments:
        line = comment["text"]
        if not is_comment_candidate(line):
            continue
        places = extract_comment_place_names(line, city)
        if places:
            clues.append({
                "text": line,
                "author": comment.get("author", ""),
                "comment_time": comment.get("comment_time", ""),
                "comment_time_raw": comment.get("comment_time_raw", ""),
                "comment_time_standard": comment.get("comment_time_standard", ""),
                "ip_location": comment.get("ip_location", ""),
                "place_candidates": places,
            })
    return clues


def clean_text_for_llm(text: str, max_lines: int = 120) -> str:
    """Strip Douyin page chrome/markdown noise before sending text to the LLM."""
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)
    text = re.sub(r"\[([^\]]{0,80})\]\([^)]*\)", r"\1", text)
    text = re.sub(r"https?://\S+|//www\.douyin\.com/\S+|data:image/\S+", "", text)
    lines: list[str] = []
    seen: set[str] = set()
    for raw_line in re.split(r"\n+", text):
        line = raw_line.strip(" \t-[]()（）")
        line = re.sub(r"\s+", " ", line).strip()
        line_plain = re.sub(r"^#+\s*", "", line).strip()
        if line_plain == "推荐视频" or line_plain.startswith("合集"):
            break
        if not line or line in seen:
            continue
        seen.add(line)
        if line_plain in LLM_TEXT_NOISE:
            continue
        if re.fullmatch(r"\d+", line) or re.fullmatch(r"\d{1,2}:\d{2}(?:\s*/\s*\d{1,2}:\d{2})?(?:\s*直播)?", line):
            continue
        if re.fullmatch(r"\d+(?:\.\d+)?x", line):
            continue
        if len(line) > 220 and not any(hint in line for hint in LLM_TEXT_KEEP_HINTS):
            continue
        if not any(hint in line for hint in LLM_TEXT_KEEP_HINTS) and len(line) <= 12:
            continue
        lines.append(line)
        if len(lines) >= max_lines:
            break
    return "\n".join(lines)


def _dedupe_places(places: list[str]) -> list[str]:
    cleaned: list[str] = []
    for place in places:
        place = re.sub(r"^(湖北省|武汉市|武汉|湖北)", "", str(place).strip(" ，,。:：；;、\"'[]{}()（）"))
        if len(place) < 2 or place in {"钓鱼", "野钓", "武汉", "湖北", "附近", "这里", "那里"}:
            continue
        if place not in cleaned:
            cleaned.append(place)
    # Prefer more specific names: remove shorter names contained in a longer candidate.
    return [p for p in cleaned if not any(p != q and p in q for q in cleaned)]


def log_llm_debug(message: str, enabled: bool = True) -> None:
    if enabled:
        print(f"[llm] {message}", file=sys.stderr, flush=True)


def _chat_json_array_with_llm(
    prompt: str,
    llm_url: str = DEFAULT_LLM_URL,
    debug: bool = True,
    log_prefix: str = "llm",
    system_prompt: str = "你是信息抽取器，只输出合法 JSON，不要解释。",
) -> list[object]:
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
        llm_url,
        data=body,
        headers={"Content-Type": "application/json", "Accept": "application/json", "User-Agent": "Mozilla/5.0"},
        method="POST",
    )
    log_llm_debug(f"{log_prefix} request url={llm_url} prompt_chars={len(prompt)} body_bytes={len(body)}", debug)
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
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:500]
        log_llm_debug(f"{log_prefix} http_error status={exc.code} detail={detail!r}", debug)
        return []
    except (urllib.error.URLError, TimeoutError, KeyError, IndexError, json.JSONDecodeError) as exc:
        log_llm_debug(f"{log_prefix} error type={type(exc).__name__} detail={exc}", debug)
        return []

    # Some models may wrap JSON in markdown or add prose; salvage the first JSON array.
    match = re.search(r"\[[\s\S]*\]", content)
    if not match:
        log_llm_debug(f"{log_prefix} no_json_array content_preview={content[:200]!r}", debug)
        return []
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        log_llm_debug(f"{log_prefix} json_parse_error detail={exc} content_preview={content[:200]!r}", debug)
        return []
    if not isinstance(parsed, list):
        log_llm_debug(f"{log_prefix} unexpected_json_type type={type(parsed).__name__}", debug)
        return []
    return parsed


def extract_places_with_llm(text: str, city: str, llm_url: str = DEFAULT_LLM_URL, debug: bool = True) -> list[str]:
    prompt = f"""从下面抖音钓鱼视频文本中提取实际地名/钓点候选。
要求：
- 只返回 JSON 数组，例如 [\"野芷湖公园\",\"东荆河\"]
- 优先提取河流、湖泊、水库、公园、村/桥/闸/江滩等可地理编码的地点
- 不要返回泛词（钓点、野钓、附近）、人名、鱼种、装备、城市名本身
- 若无明确地点返回 []
- 城市上下文：{city}

文本：
{text[:6000]}"""
    parsed = _chat_json_array_with_llm(prompt, llm_url, debug, "place", "你是地名抽取器，只输出合法 JSON，不要解释。")
    places = _dedupe_places([p for p in parsed if isinstance(p, str)])
    log_llm_debug(f"place places={places}", debug)
    return places


def format_comments_for_llm(comments: list[dict], max_chars: int = 8000, start_index: int = 1) -> str:
    lines: list[str] = []
    total = 0
    for index, comment in enumerate(comments, start=start_index):
        text = re.sub(r"\s+", " ", str(comment.get("text", "")).strip())
        if not text:
            continue
        author = str(comment.get("author", "")).strip() or "匿名"
        comment_time = comment.get("comment_time_standard") or comment.get("comment_time_raw") or comment.get("comment_time") or ""
        line = f"[{index}] {author} {comment_time}: {text}"
        if total + len(line) > max_chars:
            break
        lines.append(line)
        total += len(line)
    return "\n".join(lines)


def extract_comment_places_with_llm(comments: list[dict], city: str, llm_url: str = DEFAULT_LLM_URL, debug: bool = True) -> list[dict]:
    if not comments:
        return []
    prompt = f"""从下面抖音钓鱼视频评论中提取评论明确提到的实际钓点/地名。
要求：
- 只返回 JSON 数组，每项格式：{{"place_name":"东湖","comment_indexes":[7],"evidence":"东湖有个地方特别多","confidence":0.8}}
- 地名必须来自评论文本，不要根据视频标题或常识补全
- 优先提取河流、湖泊、水库、公园、桥、闸、江滩、村、湾、港等可地理编码地点
- 如果评论把地名和鱼种/鱼情连在一起，也要拆出地名，例如“月湖大翘嘴”应返回“月湖”
- 不要返回泛词（这里、那里、钓点、位置、免费停车场）、人名、鱼种、装备、城市名本身
- comment_indexes 使用评论前的方括号编号
- 若无明确地点返回 []
- 城市上下文：{city}

评论：
{format_comments_for_llm(comments)}"""
    parsed = _chat_json_array_with_llm(prompt, llm_url, debug, "comment-place", "你是评论地名抽取器，只输出合法 JSON，不要解释。")
    clues: list[dict] = []
    seen: set[str] = set()
    for item in parsed:
        if not isinstance(item, dict):
            continue
        place = item.get("place_name") or item.get("place") or item.get("地点") or item.get("钓点")
        if not isinstance(place, str):
            continue
        places = _dedupe_places([place])
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
    log_llm_debug(f"comment-place clues={clues}", debug)
    return clues


def score_comment_quality_groups_with_llm(
    comments: list[dict],
    group_size: int = 5,
    llm_url: str = DEFAULT_LLM_URL,
    debug: bool = True,
) -> list[dict]:
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
        parsed = _chat_json_array_with_llm(prompt, llm_url, debug, f"comment-quality-{group_index}", "你是钓点评价分析器，只输出合法 JSON，不要解释。")
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
    log_llm_debug(f"comment-quality scores={scores}", debug)
    return scores


def normalize_quality_score(score_1_5: float) -> float:
    """Normalize a 1-5 fishing-spot quality score to 0-1."""
    return max(0.0, min((float(score_1_5) - 1.0) / 4.0, 1.0))


def aggregate_quality_scores(scores: list[dict], comment_ids: list[int] | None = None) -> dict:
    """Weighted average of normalized comment quality scores.

    If comment_ids are provided, only groups containing those comments are used.
    Confidence is used as the weight and the result is already normalized to 0-1.
    """
    selected: list[dict] = []
    wanted = {int(x) for x in (comment_ids or []) if x}
    for item in scores:
        ids = {int(x) for x in item.get("comment_ids", []) if x}
        if wanted and not (wanted & ids):
            continue
        selected.append(item)
    if not selected:
        return {"quality_score": None, "confidence": 0.0, "detail": ""}
    weighted_total = 0.0
    weight_total = 0.0
    details: list[str] = []
    for item in selected:
        confidence = max(float(item.get("confidence", 0.0)), 0.05)
        normalized = float(item.get("normalized_score", normalize_quality_score(item.get("score_1_5", item.get("score", 3)))))
        weighted_total += normalized * confidence
        weight_total += confidence
        details.append(
            f"第{item.get('group_index')}组: raw={item.get('score_1_5', item.get('score'))}, norm={normalized:.2f}, conf={item.get('confidence')}, {item.get('summary', '')}"
        )
    return {
        "quality_score": round(weighted_total / weight_total, 4) if weight_total else None,
        "confidence": round(min(weight_total / len(selected), 1.0), 4) if selected else 0.0,
        "detail": "；".join(details),
    }


def normalize_fish_species(values: list[str]) -> list[str]:
    species: list[str] = []
    for value in values:
        name = str(value).strip(" ，,。:：；;、\"'[]{}()（）")
        if not name:
            continue
        matched = ""
        for canonical, aliases in FISH_PATTERNS.items():
            if name == canonical or name in aliases:
                matched = canonical
                break
        if not matched:
            for canonical, aliases in FISH_PATTERNS.items():
                if any(len(alias) >= 2 and alias in name for alias in aliases):
                    matched = canonical
                    break
        if matched and matched not in species:
            species.append(matched)
        elif not matched and len(name) <= 6 and name not in species:
            species.append(name)
    return species


def extract_fish_species(text: str) -> list[str]:
    found: list[str] = []
    for canonical, aliases in FISH_PATTERNS.items():
        if canonical in text or any(alias in text for alias in aliases):
            found.append(canonical)
    return normalize_fish_species(found)


def extract_fish_species_with_llm(text: str, llm_url: str = DEFAULT_LLM_URL, debug: bool = True) -> list[str]:
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
    parsed = _chat_json_array_with_llm(prompt, llm_url, debug, "fish", "你是鱼种抽取器，只输出合法 JSON，不要解释。")
    raw: list[str] = []
    for item in parsed:
        if isinstance(item, str):
            raw.append(item)
        elif isinstance(item, dict):
            value = item.get("name") or item.get("species") or item.get("fish") or item.get("鱼种")
            if isinstance(value, str):
                raw.append(value)
    species = normalize_fish_species(raw)
    log_llm_debug(f"fish species={species}", debug)
    return species


def parse_video(search_item: dict, extracted: dict, city: str = "武汉", llm_url: str = DEFAULT_LLM_URL, use_llm: bool = True, llm_debug: bool = True) -> dict:
    content = extracted.get("content", "")
    title = extracted.get("title", "")
    if title.endswith(" - 抖音"):
        title = title[:-5]
    title = title or search_item.get("desc", "")
    m = re.search(r"发布时间：([0-9]{4}-[0-9]{2}-[0-9]{2}\s+[0-9]{2}:[0-9]{2})", content)
    publish_time = m.group(1) if m else ""
    cleaned_content = clean_text_for_llm(content)
    haystack = f"{title}\n{search_item.get('desc','')}\n{cleaned_content[:5000]}"
    candidates = extract_places_with_llm(haystack, city, llm_url, debug=llm_debug) if use_llm else []
    fallback_candidates = [place for place in PLACE_PATTERNS if place in haystack]
    candidates = _dedupe_places([*candidates, *fallback_candidates])
    rule_fish_species = extract_fish_species(haystack)
    llm_fish_species = extract_fish_species_with_llm(haystack, llm_url, debug=llm_debug) if use_llm else []
    fish_species = normalize_fish_species([*rule_fish_species, *llm_fish_species])
    fish_sources = []
    if rule_fish_species:
        fish_sources.append("rule:FISH_PATTERNS")
    if llm_fish_species:
        fish_sources.append("llm:title+desc+page_text")
    fish_source = "+".join(fish_sources)
    fish_confidence = 0.95 if llm_fish_species else (0.85 if rule_fish_species else 0.0)
    return {
        "title": title,
        "author": search_item.get("author", ""),
        "url": search_item.get("url", ""),
        "publish_time": publish_time,
        "raw_text": cleaned_content[:2000],
        "place_candidates": candidates,
        "fish_species": fish_species,
        "fish_species_source": fish_source,
        "fish_confidence": fish_confidence,
    }


def sleep_between_items(index: int, total: int, delay_min: float, delay_max: float) -> None:
    if index >= total - 1:
        return
    delay = random.uniform(delay_min, delay_max)
    print(f"[throttle] sleep {delay:.1f}s before next video...", flush=True)
    time.sleep(delay)


def geocode(place: str, city: str = "武汉") -> dict | None:
    query = place if place.startswith(city) else f"{city}{place}"
    out = run(["python", str(GEOCODE_SCRIPT), "-p", "baidu", "geocode", "--to", "wgs84", query], timeout=60)
    data = json.loads(out)
    # 百度地图返回 status 为整数 0
    if data.get("status") != 0 or "result" not in data:
        return None
    result = data["result"]
    loc = result["location"]
    return {
        "query_name": query,
        "longitude": float(loc["lng"]),
        "latitude": float(loc["lat"]),
        "geocode_score": int(result.get("confidence", 0)),
        "geocode_level": result.get("level", ""),
    }


def video_exists(conn: sqlite3.Connection, url: str) -> bool:
    return conn.execute("SELECT 1 FROM videos WHERE url=? LIMIT 1", (url,)).fetchone() is not None


def upsert_video(conn: sqlite3.Connection, keyword: str, video: dict) -> int:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        """INSERT OR IGNORE INTO videos(platform, keyword, title, url, author, publish_time, raw_text, collected_at)
           VALUES('douyin',?,?,?,?,?,?,?)""",
        (keyword, video["title"], video["url"], video["author"], video["publish_time"], video["raw_text"], now),
    )
    row = conn.execute("SELECT id FROM videos WHERE url=?", (video["url"],)).fetchone()
    if not row:
        raise RuntimeError(f"failed to upsert video: {video['url']}")
    return int(row[0])


def existing_spot_names(conn: sqlite3.Connection, video_id: int) -> set[str]:
    return {str(row[0]) for row in conn.execute("SELECT place_name FROM fishing_spots WHERE video_id=?", (video_id,)) if row[0]}


def insert_video_comments(conn: sqlite3.Connection, video_id: int, comments: list[dict]) -> list[dict]:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    saved: list[dict] = []
    for comment in comments:
        conn.execute(
            """INSERT OR IGNORE INTO video_comments(video_id, author, text, comment_time, comment_time_raw, comment_time_standard, ip_location, is_author, raw_json, collected_at)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (
                video_id,
                comment.get("author", ""),
                comment.get("text", ""),
                comment.get("comment_time", ""),
                comment.get("comment_time_raw", ""),
                comment.get("comment_time_standard", ""),
                comment.get("ip_location", ""),
                1 if comment.get("is_author") else 0,
                json.dumps(comment, ensure_ascii=False),
                now,
            ),
        )
        row = conn.execute(
            """SELECT id FROM video_comments
               WHERE video_id=? AND author=? AND text=? AND comment_time_raw=?""",
            (video_id, comment.get("author", ""), comment.get("text", ""), comment.get("comment_time_raw", "")),
        ).fetchone()
        saved_comment = dict(comment)
        if row:
            saved_comment["comment_id"] = int(row[0])
        saved.append(saved_comment)
    return saved


def apply_comment_quality_to_spots(conn: sqlite3.Connection, video_id: int, quality: dict) -> None:
    """Write normalized comment quality score directly onto fishing_spots."""
    quality_score = quality.get("quality_score")
    if quality_score is None:
        return
    conn.execute(
        """UPDATE fishing_spots
           SET quality_score=?, quality_score_source=?, quality_score_detail=?
           WHERE video_id=?""",
        (quality_score, "comment_llm", quality.get("detail", ""), video_id),
    )


def insert_record(conn: sqlite3.Connection, keyword: str, video: dict, spot: dict) -> None:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    video_id = upsert_video(conn, keyword, video)
    conn.execute(
        """INSERT INTO fishing_spots(video_id, place_name, query_name, longitude, latitude, fish_species, fish_species_source, fish_confidence, geocode_score, geocode_level, confidence, source_type, source_text, quality_score, quality_score_source, quality_score_detail, created_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            video_id,
            spot["place_name"],
            spot["query_name"],
            spot["longitude"],
            spot["latitude"],
            json.dumps(video.get("fish_species", []), ensure_ascii=False),
            video.get("fish_species_source", ""),
            video.get("fish_confidence", 0.0),
            spot["geocode_score"],
            spot["geocode_level"],
            spot["confidence"],
            spot.get("source_type", "video_text"),
            spot.get("source_text", video["raw_text"][:500]),
            spot.get("quality_score"),
            spot.get("quality_score_source", ""),
            spot.get("quality_score_detail", ""),
            now,
        ),
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keyword", default="武汉钓鱼", help="Search keyword; also stored with --url imports unless overridden")
    ap.add_argument("--limit", type=int, default=1, help="Search result limit; ignored when --url is provided")
    ap.add_argument("--url", default="", help="Process a single Douyin video URL directly instead of searching by keyword")
    ap.add_argument("--city", default="武汉")
    ap.add_argument("--session", default="douyin-fishing-batch")
    ap.add_argument("--llm-url", default=DEFAULT_LLM_URL, help="OpenAI-compatible /v1/chat/completions endpoint")
    ap.add_argument("--no-llm", action="store_true", help="Disable LLM place/fish extraction and use rule fallbacks only")
    ap.add_argument("--quiet-llm", action="store_true", help="Disable LLM debug logs")
    ap.add_argument("--delay-min", type=float, default=8.0, help="Minimum sleep seconds between video detail requests")
    ap.add_argument("--delay-max", type=float, default=20.0, help="Maximum sleep seconds between video detail requests")
    ap.add_argument("--max-video-places", type=int, default=3, help="Maximum video-text place candidates to geocode/save per video; 0 means all")
    ap.add_argument("--include-comments", action="store_true", help="Extract/save visible comments, run LLM comment spot extraction, and score comment quality")
    ap.add_argument("--comment-scrolls", type=int, default=0, help="How many times to scroll before reading comments")
    ap.add_argument("--comment-wait", type=float, default=2.0, help="Seconds to wait after each comment scroll")
    ap.add_argument("--comment-max", type=int, default=100, help="Maximum visible comments to extract/save per video")
    ap.add_argument("--comment-quality-group-size", type=int, default=5, help="LLM quality scoring group size for comments")
    args = ap.parse_args()

    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH, isolation_level=None)
    init_db(conn)

    if args.delay_min < 0 or args.delay_max < args.delay_min:
        raise ValueError("--delay-max must be >= --delay-min and delays must be non-negative")
    if args.max_video_places < 0:
        raise ValueError("--max-video-places must be >= 0")
    if args.comment_max < 0:
        raise ValueError("--comment-max must be >= 0")
    if args.comment_quality_group_size <= 0:
        raise ValueError("--comment-quality-group-size must be > 0")

    spot_results = []
    comment_results = []
    direct_url = args.url.strip()
    items = [{"url": direct_url, "desc": "", "author": ""}] if direct_url else search(args.keyword, args.limit)
    for index, item in enumerate(items):
        url = item.get("url", "")
        if not url:
            print(f"[skip] missing url for item index={index}", file=sys.stderr, flush=True)
            sleep_between_items(index, len(items), args.delay_min, args.delay_max)
            continue
        already_exists = video_exists(conn, url)
        if already_exists and not args.include_comments:
            print(f"[skip] already in db: {url}", file=sys.stderr, flush=True)
            sleep_between_items(index, len(items), args.delay_min, args.delay_max)
            continue
        if already_exists:
            print(f"[info] already in db, refreshing comments only: {url}", file=sys.stderr, flush=True)

        extracted = extract_video(url, args.session)
        video = parse_video(item, extracted, city=args.city, llm_url=args.llm_url, use_llm=not args.no_llm, llm_debug=not args.quiet_llm)
        video_id = upsert_video(conn, args.keyword, video)
        if not video.get("title"):
            row = conn.execute("SELECT title, author, publish_time, raw_text FROM videos WHERE id=?", (video_id,)).fetchone()
            if row:
                video["title"] = row[0] or video.get("title", "")
                video["author"] = row[1] or video.get("author", "")
                video["publish_time"] = row[2] or video.get("publish_time", "")
                video["raw_text"] = row[3] or video.get("raw_text", "")

        inserted_places: set[str] = existing_spot_names(conn, video_id)
        if not already_exists:
            video_places = video["place_candidates"] if args.max_video_places == 0 else video["place_candidates"][: args.max_video_places]
            for place in video_places:
                geo = geocode(place, args.city)
                if not geo:
                    continue
                spot = {
                    "place_name": place,
                    "confidence": 0.9 if geo["geocode_score"] >= 90 else 0.7,
                    "source_type": "video_text",
                    "source_text": video["raw_text"][:500],
                    **geo,
                }
                insert_record(conn, args.keyword, video, spot)
                inserted_places.add(place)
                spot_results.append({"video": video["url"], "title": video["title"], "fish_species": video.get("fish_species", []), **spot})

        if args.include_comments:
            try:
                comments = extract_video_comments(
                    args.session,
                    scrolls=args.comment_scrolls,
                    wait_seconds=args.comment_wait,
                    max_comments=args.comment_max,
                )
                comments = insert_video_comments(conn, video_id, comments)
                if args.no_llm:
                    comment_clues = []
                    quality_groups = []
                    video_quality = {"quality_score": None, "confidence": 0.0, "detail": ""}
                else:
                    comment_clues = extract_comment_places_with_llm(
                        comments,
                        args.city,
                        llm_url=args.llm_url,
                        debug=not args.quiet_llm,
                    )
                    quality_groups = score_comment_quality_groups_with_llm(
                        comments,
                        group_size=args.comment_quality_group_size,
                        llm_url=args.llm_url,
                        debug=not args.quiet_llm,
                    )
                    video_quality = aggregate_quality_scores(quality_groups)
                    apply_comment_quality_to_spots(conn, video_id, video_quality)
            except Exception as exc:
                print(f"[warn] comment extraction/LLM analysis failed for {url}: {exc}", file=sys.stderr, flush=True)
                comments = []
                comment_clues = []
                quality_groups = []
                video_quality = {"quality_score": None, "confidence": 0.0, "detail": ""}
            for clue in comment_clues:
                place = clue.get("place_name", "")
                if not place or place in inserted_places:
                    continue
                geo = geocode(place, args.city)
                if not geo or geo["geocode_score"] < 80:
                    continue
                source_text = str(clue.get("evidence") or "").strip()
                if clue.get("comment_ids"):
                    source_text = f"{source_text}（comment_ids={json.dumps(clue['comment_ids'], ensure_ascii=False)}）"
                place_quality = aggregate_quality_scores(quality_groups, clue.get("comment_ids") or [])
                if place_quality.get("quality_score") is None:
                    place_quality = video_quality
                spot = {
                    "place_name": place,
                    "confidence": 0.65 if geo["geocode_score"] >= 90 else 0.5,
                    "source_type": "comment_llm",
                    "source_text": source_text,
                    "quality_score": place_quality.get("quality_score"),
                    "quality_score_source": "comment_llm" if place_quality.get("quality_score") is not None else "",
                    "quality_score_detail": place_quality.get("detail", ""),
                    **geo,
                }
                insert_record(conn, args.keyword, video, spot)
                inserted_places.add(place)
                spot_results.append({"video": video["url"], "title": video["title"], "fish_species": video.get("fish_species", []), **spot})
            comment_results.append({
                "video": video["url"],
                "title": video["title"],
                "saved_comments": len(comments),
                "comment_place_clues": comment_clues,
                "spot_quality_score": video_quality.get("quality_score"),
                "spot_quality_detail": video_quality.get("detail", ""),
                "comment_quality_groups": quality_groups,
            })
        sleep_between_items(index, len(items), args.delay_min, args.delay_max)
    print(json.dumps({"inserted_spots": len(spot_results), "results": spot_results, "comment_results": comment_results, "db": str(DB_PATH)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
