#!/usr/bin/env python3
"""Batch collector MVP for Douyin fishing spot videos.

Pipeline:
1. opencli douyin search <keyword>
2. opencli browser open/extract each video URL
3. extract title, publish time, place candidates, fish species
4. tianditu geocode
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
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "fishing_spots.sqlite"
GEOCODE_SCRIPT = ROOT / ".agents" / "skills" / "tianditu-geocode" / "tianditu_geocode.py"

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
    columns = {row[1] for row in conn.execute("PRAGMA table_info(fishing_spots)")}
    if "fish_species" not in columns:
        conn.execute("ALTER TABLE fishing_spots ADD COLUMN fish_species TEXT")
    if "fish_species_source" not in columns:
        conn.execute("ALTER TABLE fishing_spots ADD COLUMN fish_species_source TEXT")
    if "fish_confidence" not in columns:
        conn.execute("ALTER TABLE fishing_spots ADD COLUMN fish_confidence REAL")
    if "source_type" not in columns:
        conn.execute("ALTER TABLE fishing_spots ADD COLUMN source_type TEXT")
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
    for _ in range(scrolls):
        run(["opencli", "browser", session, "scroll", "down", "--amount", "1200"], timeout=60)
        if wait_seconds:
            time.sleep(wait_seconds)
    js = "(() => { const text = document.body.innerText || ''; const start = text.indexOf('全部评论'); const endMarks = ['下载客户端，桌面快捷访问', '广告投放', '用户服务协议']; let end = text.length; for (const mark of endMarks) { const idx = text.indexOf(mark, start >= 0 ? start : 0); if (idx > 0) end = Math.min(end, idx); } const slice = start >= 0 ? text.slice(start, end) : ''; return slice.split(/\\n+/).map(s => s.trim()).filter(Boolean); })()"
    data = browser_eval(session, js)
    lines = [str(x) for x in data] if isinstance(data, list) else []
    clues: list[dict] = []
    for line in lines:
        if not is_comment_candidate(line):
            continue
        places = extract_comment_place_names(line, city)
        if places:
            clues.append({"text": line, "place_candidates": places})
    return clues


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
    haystack = f"{title}\n{search_item.get('desc','')}\n{content[:5000]}"
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
        "raw_text": content[:2000],
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
    out = run(["python", str(GEOCODE_SCRIPT), "geocode", query], timeout=60)
    data = json.loads(out)
    if data.get("status") != "0" or "location" not in data:
        return None
    loc = data["location"]
    return {
        "query_name": query,
        "longitude": float(loc["lon"]),
        "latitude": float(loc["lat"]),
        "geocode_score": int(loc.get("score", 0)),
        "geocode_level": loc.get("level", ""),
    }


def video_exists(conn: sqlite3.Connection, url: str) -> bool:
    return conn.execute("SELECT 1 FROM videos WHERE url=? LIMIT 1", (url,)).fetchone() is not None


def insert_record(conn: sqlite3.Connection, keyword: str, video: dict, spot: dict) -> None:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        """INSERT OR IGNORE INTO videos(platform, keyword, title, url, author, publish_time, raw_text, collected_at)
           VALUES('douyin',?,?,?,?,?,?,?)""",
        (keyword, video["title"], video["url"], video["author"], video["publish_time"], video["raw_text"], now),
    )
    video_id = conn.execute("SELECT id FROM videos WHERE url=?", (video["url"],)).fetchone()[0]
    conn.execute(
        """INSERT INTO fishing_spots(video_id, place_name, query_name, longitude, latitude, fish_species, fish_species_source, fish_confidence, geocode_score, geocode_level, confidence, source_type, source_text, created_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
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
            now,
        ),
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keyword", default="武汉钓鱼")
    ap.add_argument("--limit", type=int, default=1)
    ap.add_argument("--city", default="武汉")
    ap.add_argument("--session", default="douyin-fishing-batch")
    ap.add_argument("--llm-url", default=DEFAULT_LLM_URL, help="OpenAI-compatible /v1/chat/completions endpoint")
    ap.add_argument("--no-llm", action="store_true", help="Disable LLM place/fish extraction and use rule fallbacks only")
    ap.add_argument("--quiet-llm", action="store_true", help="Disable LLM debug logs")
    ap.add_argument("--delay-min", type=float, default=8.0, help="Minimum sleep seconds between video detail requests")
    ap.add_argument("--delay-max", type=float, default=20.0, help="Maximum sleep seconds between video detail requests")
    ap.add_argument("--include-comments", action="store_true", help="Extract visible comment-area location clues as lower-confidence spot candidates")
    ap.add_argument("--comment-scrolls", type=int, default=0, help="How many times to scroll before reading comments")
    ap.add_argument("--comment-wait", type=float, default=2.0, help="Seconds to wait after each comment scroll")
    args = ap.parse_args()

    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH, isolation_level=None)
    init_db(conn)

    if args.delay_min < 0 or args.delay_max < args.delay_min:
        raise ValueError("--delay-max must be >= --delay-min and delays must be non-negative")

    results = []
    items = search(args.keyword, args.limit)
    for index, item in enumerate(items):
        url = item.get("url", "")
        if not url:
            print(f"[skip] missing url for item index={index}", file=sys.stderr, flush=True)
            sleep_between_items(index, len(items), args.delay_min, args.delay_max)
            continue
        if video_exists(conn, url):
            print(f"[skip] already in db: {url}", file=sys.stderr, flush=True)
            sleep_between_items(index, len(items), args.delay_min, args.delay_max)
            continue

        extracted = extract_video(url, args.session)
        video = parse_video(item, extracted, city=args.city, llm_url=args.llm_url, use_llm=not args.no_llm, llm_debug=not args.quiet_llm)

        inserted_places: set[str] = set()
        for place in video["place_candidates"][:1]:
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
            results.append({"video": video["url"], "title": video["title"], "fish_species": video.get("fish_species", []), **spot})

        if args.include_comments:
            try:
                comment_clues = extract_comment_spot_clues(args.session, args.city, scrolls=args.comment_scrolls, wait_seconds=args.comment_wait)
            except Exception as exc:
                print(f"[warn] comment extraction failed for {url}: {exc}", file=sys.stderr, flush=True)
                comment_clues = []
            for clue in comment_clues:
                for place in clue["place_candidates"]:
                    if place in inserted_places:
                        continue
                    geo = geocode(place, args.city)
                    if not geo or geo["geocode_score"] < 80:
                        continue
                    spot = {
                        "place_name": place,
                        "confidence": 0.65 if geo["geocode_score"] >= 90 else 0.5,
                        "source_type": "comment",
                        "source_text": clue["text"],
                        **geo,
                    }
                    insert_record(conn, args.keyword, video, spot)
                    inserted_places.add(place)
                    results.append({"video": video["url"], "title": video["title"], "fish_species": video.get("fish_species", []), **spot})
        sleep_between_items(index, len(items), args.delay_min, args.delay_max)
    print(json.dumps({"inserted_spots": len(results), "results": results, "db": str(DB_PATH)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
