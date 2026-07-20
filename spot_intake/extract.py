"""Pure extraction and normalization logic for spot intake.

Everything in this module is side-effect free: no browser, no LLM, no database,
no filesystem. This is the test surface of the intake module.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta

from spot_intake.vocabulary import (
    ADMIN_EXCEPTION_SUFFIXES,
    ADMIN_SUFFIXES,
    COMMENT_KEYWORD_CATEGORIES,
    COMMENT_KEYWORD_CATEGORY_ALIASES,
    COMMENT_NOISE,
    COMMENT_PLACE_HINTS,
    FISH_PATTERNS,
    GENERIC_PLACE_BLOCKLIST,
    LINEAR_WATER_SUFFIXES,
    LLM_TEXT_KEEP_HINTS,
    LLM_TEXT_NOISE,
    MAIN_STEM_WATER_BODIES,
    SEGMENT_SUFFIXES,
)


# ---------------------------------------------------------------------------
# Douyin comment time parsing
# ---------------------------------------------------------------------------

def days_in_month(year: int, month: int) -> int:
    if month == 12:
        next_month = datetime(year + 1, 1, 1)
    else:
        next_month = datetime(year, month + 1, 1)
    return (next_month - timedelta(days=1)).day


def shift_months(dt: datetime, months: int) -> datetime:
    month_index = dt.month - 1 + months
    year = dt.year + month_index // 12
    month = month_index % 12 + 1
    day = min(dt.day, days_in_month(year, month))
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
        (r"^(\d+)月前$", lambda n: shift_months(now, -n)),
        (r"^(\d+)年前$", lambda n: shift_months(now, -12 * n)),
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


# ---------------------------------------------------------------------------
# Comment place extraction (rule-based)
# ---------------------------------------------------------------------------

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
        r"([\u4e00-\u9fa5]{1,12}(?:江滩|水库|水厂|泵站|公园|闸|桥|码头|江|河|湖|村|湾|港|沟|渠))",
        r"([\u4e00-\u9fa5]{2,8}钓点)",
        r"([\u4e00-\u9fa5]{2,8}钓位)",
    ]:
        for m in re.finditer(pattern, line):
            name = m.group(1).strip(" ，,。.!！?？")
            name = re.sub(r"^(湖北省|武汉市|武汉|湖北|去|到|在)", "", name)
            had_generic_suffix = bool(re.search(r"(?:黄尾钓点|钓点|钓位)$", name))
            name = re.sub(r"(黄尾钓点|钓点|钓位)$", "", name)
            if had_generic_suffix and not any(hint in name for hint in COMMENT_PLACE_HINTS):
                continue
            if name and len(name) >= 2 and name not in candidates and name not in {city, "同款", "哪里"}:
                candidates.append(name)
    return dedupe_places(candidates)


def extract_comment_spot_clues_from_comments(comments: list[dict], city: str) -> list[dict]:
    """Rule-based comment place clues from already extracted comments.

    Pure helper: feed it saved comment JSON fixtures, no browser needed.
    """
    clues: list[dict] = []
    for index, comment in enumerate(comments, start=1):
        line = str(comment.get("text", "")).strip()
        if not is_comment_candidate(line):
            continue
        places = extract_comment_place_names(line, city)
        if places:
            clues.append({
                "comment_index": index,
                "comment_id": comment.get("comment_id"),
                "text": line,
                "author": comment.get("author", ""),
                "comment_time": comment.get("comment_time", ""),
                "comment_time_raw": comment.get("comment_time_raw", ""),
                "comment_time_standard": comment.get("comment_time_standard", ""),
                "ip_location": comment.get("ip_location", ""),
                "place_candidates": places,
            })
    return clues


# ---------------------------------------------------------------------------
# Text cleaning / LLM prompt formatting
# ---------------------------------------------------------------------------

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


def dedupe_places(places: list[str]) -> list[str]:
    cleaned: list[str] = []
    for place in places:
        place = re.sub(r"^(湖北省|武汉市|武汉|湖北)", "", str(place).strip(" ，,。:：；;、\"'[]{}()（）"))
        if len(place) < 2 or place in {"钓鱼", "野钓", "武汉", "湖北", "附近", "这里", "那里"}:
            continue
        if place not in cleaned:
            cleaned.append(place)
    # Prefer more specific names: remove shorter names contained in a longer candidate.
    return [p for p in cleaned if not any(p != q and p in q for q in cleaned)]


# ---------------------------------------------------------------------------
# 精度分级 (precision): name-only classification + post-geocode refinement.
# Tiers: "point" (navigable anchor) | "segment" (coarse but meaningful area) |
# "reject" (not a 钓点). See CONTEXT.md.
# ---------------------------------------------------------------------------

def _is_admin_name(name: str) -> bool:
    return name.endswith(ADMIN_SUFFIXES) and not name.endswith(ADMIN_EXCEPTION_SUFFIXES)


def classify_place_name(name: str) -> str:
    """Name-only precision classification, applied before geocoding (rejects
    here never cost a geocode call)."""
    n = str(name).strip()
    if not n or n in GENERIC_PLACE_BLOCKLIST:
        return "reject"
    if n in MAIN_STEM_WATER_BODIES:
        return "reject"
    if n.endswith(SEGMENT_SUFFIXES):
        return "segment"  # 片区/社区 etc. end with 区 but are not districts — check first
    if _is_admin_name(n):
        return "reject"
    if n.endswith(LINEAR_WATER_SUFFIXES):
        return "segment"
    return "point"


def refine_precision(precision: str, geocode: dict) -> str:
    """Post-geocode adjustment using the geocoder's level and resolved name.
    Catches district aliases the name-only pass can't know (汉南 -> 汉南区)."""
    if precision == "reject":
        return "reject"
    level = str(geocode.get("geocode_level", ""))
    # "区县"/"城市" level = the geocoder only resolved to admin granularity —
    # the coordinates are the admin centroid, not the place. Always garbage.
    if level in ("区县", "城市"):
        return "reject"
    query_name = str(geocode.get("query_name", ""))
    if _is_admin_name(query_name):
        return "reject"
    if level in ("乡镇", "村庄"):
        return "segment"
    return precision


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


# ---------------------------------------------------------------------------
# Comment keyword normalization / aggregation
# ---------------------------------------------------------------------------

def normalize_comment_keyword_category(value: object) -> str:
    category = str(value or "").strip().lower()
    if category in COMMENT_KEYWORD_CATEGORIES:
        return category
    return COMMENT_KEYWORD_CATEGORY_ALIASES.get(str(value or "").strip(), "")


def normalize_comment_keyword(value: object) -> str:
    keyword = re.sub(r"\s+", "", str(value or "").strip(" ，,。:：；;、\"'[]{}()（）"))
    if not keyword or len(keyword) > 16:
        return ""
    if keyword in {"钓点", "位置", "这里", "那里", "哪里", "评论", "视频", "作者"}:
        return ""
    return keyword


def aggregate_comment_keywords(keywords: list[dict]) -> list[dict]:
    grouped: dict[tuple[str, str], dict] = {}
    for item in keywords:
        keyword = str(item.get("keyword", "")).strip()
        category = str(item.get("category", "")).strip()
        if not keyword or not category:
            continue
        key = (keyword, category)
        bucket = grouped.setdefault(key, {"keyword": keyword, "category": category, "count": 0, "confidence_total": 0.0, "comment_ids": []})
        bucket["count"] += 1
        bucket["confidence_total"] += float(item.get("confidence", 0.0) or 0.0)
        comment_id = item.get("comment_id")
        if comment_id and comment_id not in bucket["comment_ids"]:
            bucket["comment_ids"].append(comment_id)
    result = []
    for bucket in grouped.values():
        count = bucket.pop("count")
        confidence_total = bucket.pop("confidence_total")
        bucket["count"] = count
        bucket["avg_confidence"] = round(confidence_total / count, 4) if count else 0.0
        result.append(bucket)
    return sorted(result, key=lambda x: (-x["count"], x["category"], x["keyword"]))


# ---------------------------------------------------------------------------
# Quality scoring
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Fish species
# ---------------------------------------------------------------------------

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
