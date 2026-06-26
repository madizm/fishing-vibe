#!/usr/bin/env python3
"""Rerun Douyin collection for videos whose fishing spots lack quality_score.

The collector can refresh an existing video by direct URL (`--url ...`). This
helper finds videos linked to fishing_spots rows with NULL quality_score,
deduplicates by video URL, then invokes collect_douyin_fishing_spots.py once per
URL so comment extraction / LLM quality scoring can fill the missing scores.
"""

from __future__ import annotations

import argparse
import os
import random
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "fishing_spots.sqlite"
COLLECT_SCRIPT = ROOT / "scripts" / "collect_douyin_fishing_spots.py"
DEFAULT_LLM_URL = os.getenv("OPENAI_BASE_URL", "http://100.90.54.85:8080/v1").rstrip("/") + "/chat/completions"


def load_urls(db_path: Path, limit: int = 0) -> list[dict]:
    """Return distinct video URLs with at least one NULL quality_score spot."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        query = """
            SELECT
              v.id AS video_id,
              v.url AS url,
              COALESCE(v.keyword, '') AS keyword,
              COALESCE(v.title, '') AS title,
              COUNT(s.id) AS missing_spot_count
            FROM fishing_spots s
            JOIN videos v ON v.id = s.video_id
            WHERE s.quality_score IS NULL
              AND TRIM(COALESCE(v.url, '')) != ''
            GROUP BY v.id, v.url, v.keyword, v.title
            ORDER BY MAX(s.id) DESC
        """
        if limit > 0:
            query += " LIMIT ?"
            rows = conn.execute(query, (limit,)).fetchall()
        else:
            rows = conn.execute(query).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def build_collect_cmd(args: argparse.Namespace, item: dict) -> list[str]:
    cmd = [
        sys.executable,
        str(COLLECT_SCRIPT),
        "--url",
        item["url"],
        "--keyword",
        args.keyword or item.get("keyword") or "武汉钓鱼",
        "--city",
        args.city,
        "--session",
        args.session,
        "--llm-url",
        args.llm_url,
        "--comment-scrolls",
        str(args.comment_scrolls),
        "--comment-wait",
        str(args.comment_wait),
        "--comment-max",
        str(args.comment_max),
        "--comment-quality-group-size",
        str(args.comment_quality_group_size),
        "--comment-keyword-group-size",
        str(args.comment_keyword_group_size),
        # The outer helper handles throttling between URLs. The inner collector
        # processes one URL per invocation, so keep its own delay at zero.
        "--delay-min",
        "0",
        "--delay-max",
        "0",
    ]
    if args.no_llm:
        cmd.append("--no-llm")
    if args.quiet_llm:
        cmd.append("--quiet-llm")
    if args.no_include_comments:
        cmd.append("--no-include-comments")
    else:
        cmd.append("--include-comments")
    return cmd


def sleep_between(index: int, total: int, delay_min: float, delay_max: float) -> None:
    if index >= total - 1:
        return
    delay = random.uniform(delay_min, delay_max)
    if delay > 0:
        print(f"[throttle] sleep {delay:.1f}s before next URL...", flush=True)
        time.sleep(delay)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", type=Path, default=DEFAULT_DB, help="SQLite database path")
    ap.add_argument("--limit", type=int, default=0, help="Maximum number of distinct video URLs to process; 0 means all")
    ap.add_argument("--dry-run", action="store_true", help="Only print URLs that would be processed")
    ap.add_argument("--continue-on-error", action="store_true", help="Continue with remaining URLs if one collect run fails")
    ap.add_argument("--keyword", default="", help="Override keyword stored when rerunning collect; defaults to each video's DB keyword")
    ap.add_argument("--city", default="武汉")
    ap.add_argument("--session", default="douyin-missing-quality")
    ap.add_argument("--llm-url", default=DEFAULT_LLM_URL, help="OpenAI-compatible /v1/chat/completions endpoint")
    ap.add_argument("--no-llm", action="store_true", help="Forward --no-llm to collect")
    ap.add_argument("--quiet-llm", action="store_true", help="Forward --quiet-llm to collect")
    ap.add_argument("--no-include-comments", action="store_true", help="Forward --no-include-comments to collect")
    ap.add_argument("--comment-scrolls", type=int, default=0)
    ap.add_argument("--comment-wait", type=float, default=2.0)
    ap.add_argument("--comment-max", type=int, default=100)
    ap.add_argument("--comment-quality-group-size", type=int, default=5)
    ap.add_argument("--comment-keyword-group-size", type=int, default=20)
    ap.add_argument("--delay-min", type=float, default=8.0, help="Minimum sleep seconds between URLs")
    ap.add_argument("--delay-max", type=float, default=20.0, help="Maximum sleep seconds between URLs")
    args = ap.parse_args()

    if not args.db.exists():
        raise FileNotFoundError(args.db)
    if args.limit < 0:
        raise ValueError("--limit must be >= 0")
    if args.delay_min < 0 or args.delay_max < args.delay_min:
        raise ValueError("--delay-max must be >= --delay-min and delays must be non-negative")

    items = load_urls(args.db, args.limit)
    print(f"[info] found {len(items)} video URL(s) with NULL quality_score spots", flush=True)
    if not items:
        return 0

    failures: list[tuple[str, int]] = []
    for index, item in enumerate(items):
        prefix = f"[{index + 1}/{len(items)}]"
        title = item.get("title") or ""
        print(f"{prefix} missing_spots={item['missing_spot_count']} url={item['url']} title={title[:80]}", flush=True)
        cmd = build_collect_cmd(args, item)
        print("[cmd] " + " ".join(cmd), flush=True)
        if args.dry_run:
            continue
        proc = subprocess.run(cmd, cwd=ROOT, text=True)
        if proc.returncode != 0:
            failures.append((item["url"], proc.returncode))
            print(f"[error] collect failed rc={proc.returncode}: {item['url']}", file=sys.stderr, flush=True)
            if not args.continue_on_error:
                return proc.returncode
        sleep_between(index, len(items), args.delay_min, args.delay_max)

    if failures:
        print(f"[done] completed with {len(failures)} failure(s)", file=sys.stderr, flush=True)
        return 1
    print("[done] completed", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
