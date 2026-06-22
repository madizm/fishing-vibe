#!/usr/bin/env python3
"""Extract visible Douyin video comments and comment times for one video URL.

This is a low-volume test helper. It opens a Douyin video via OpenCLI browser,
then calls collect_douyin_fishing_spots.extract_video_comments() to return the
currently visible comments/replies with their visible time text.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from collect_douyin_fishing_spots import ROOT, extract_video_comments, run


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("url", help="Douyin video URL")
    ap.add_argument("--session", default="douyin-comment-time")
    ap.add_argument("--scrolls", type=int, default=0, help="How many page scrolls to load more comments")
    ap.add_argument("--wait", type=float, default=2.0, help="Seconds to wait after open/each scroll")
    ap.add_argument("--max-comments", type=int, default=100)
    ap.add_argument("--output", default="", help="Optional JSON output path")
    args = ap.parse_args()

    run(["opencli", "browser", args.session, "open", args.url], timeout=120)
    if args.wait:
        time.sleep(args.wait)

    comments = extract_video_comments(
        args.session,
        scrolls=args.scrolls,
        wait_seconds=args.wait,
        max_comments=args.max_comments,
    )
    result = {
        "url": args.url,
        "comment_count": len(comments),
        "comments": comments,
    }
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        output = Path(args.output)
        if not output.is_absolute():
            output = ROOT / output
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
