#!/usr/bin/env python3
"""Batch collector MVP for Douyin fishing spot videos.

Pipeline:
1. opencli douyin search <keyword>
2. opencli browser open/extract each video URL
3. extract title, publish time, place candidates
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
    "蔡甸江滩", "汉口江滩", "武昌江滩", "联丰村",
]
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
      geocode_score INTEGER,
      geocode_level TEXT,
      confidence REAL,
      source_text TEXT,
      created_at TEXT,
      FOREIGN KEY(video_id) REFERENCES videos(id)
    )""")


def search(keyword: str, limit: int) -> list[dict]:
    out = run(["opencli", "douyin", "search", keyword, "--limit", str(limit), "-f", "json"], timeout=180)
    return json.loads(out)


def extract_video(url: str, session: str) -> dict:
    run(["opencli", "browser", session, "open", url], timeout=120)
    out = run(["opencli", "browser", session, "extract", "--chunk-size", "10000"], timeout=120)
    return json.loads(out)


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
    payload = {
        "messages": [
            {"role": "system", "content": "你是地名抽取器，只输出合法 JSON，不要解释。"},
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
    log_llm_debug(f"request url={llm_url} city={city} input_chars={len(text)} body_bytes={len(body)}", debug)
    log_llm_debug(f"input_begin\n{text[:6000]}\ninput_end", debug)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode("utf-8", "replace")
            log_llm_debug(f"response status={resp.status} bytes={len(raw.encode('utf-8'))}", debug)
        data = json.loads(raw)
        choice = data["choices"][0]
        content = choice["message"]["content"].strip()
        log_llm_debug(
            f"model={data.get('model', '')} finish_reason={choice.get('finish_reason', '')} output_chars={len(content)}",
            debug,
        )
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:500]
        log_llm_debug(f"http_error status={exc.code} detail={detail!r}", debug)
        return []
    except (urllib.error.URLError, TimeoutError, KeyError, IndexError, json.JSONDecodeError) as exc:
        log_llm_debug(f"error type={type(exc).__name__} detail={exc}", debug)
        return []

    # Some models may wrap JSON in markdown or add prose; salvage the first JSON array.
    match = re.search(r"\[[\s\S]*\]", content)
    if not match:
        log_llm_debug(f"no_json_array content_preview={content[:200]!r}", debug)
        return []
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        log_llm_debug(f"json_parse_error detail={exc} content_preview={content[:200]!r}", debug)
        return []
    if not isinstance(parsed, list):
        log_llm_debug(f"unexpected_json_type type={type(parsed).__name__}", debug)
        return []
    places = _dedupe_places([p for p in parsed if isinstance(p, str)])
    log_llm_debug(f"places={places}", debug)
    return places


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
    return {
        "title": title,
        "author": search_item.get("author", ""),
        "url": search_item.get("url", ""),
        "publish_time": publish_time,
        "raw_text": content[:2000],
        "place_candidates": candidates,
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
        """INSERT INTO fishing_spots(video_id, place_name, query_name, longitude, latitude, geocode_score, geocode_level, confidence, source_text, created_at)
           VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (
            video_id,
            spot["place_name"],
            spot["query_name"],
            spot["longitude"],
            spot["latitude"],
            spot["geocode_score"],
            spot["geocode_level"],
            spot["confidence"],
            video["raw_text"][:500],
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
    ap.add_argument("--no-llm", action="store_true", help="Disable LLM place extraction and use fallback PLACE_PATTERNS only")
    ap.add_argument("--quiet-llm", action="store_true", help="Disable LLM debug logs")
    ap.add_argument("--delay-min", type=float, default=8.0, help="Minimum sleep seconds between video detail requests")
    ap.add_argument("--delay-max", type=float, default=20.0, help="Maximum sleep seconds between video detail requests")
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
        for place in video["place_candidates"][:1]:
            geo = geocode(place, args.city)
            if not geo:
                continue
            spot = {"place_name": place, "confidence": 0.9 if geo["geocode_score"] >= 90 else 0.7, **geo}
            insert_record(conn, args.keyword, video, spot)
            results.append({"video": video["url"], "title": video["title"], **spot})
        sleep_between_items(index, len(items), args.delay_min, args.delay_max)
    print(json.dumps({"inserted_spots": len(results), "results": results, "db": str(DB_PATH)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
