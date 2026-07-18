#!/usr/bin/env python3
"""Batch collector CLI for Douyin fishing spot videos — thin shell.

All behavior lives in the spot_intake package: this script only parses flags,
wires the production adapters (opencli browser, OpenAI-compatible LLM, geocode
skill, SQLite), and prints the resulting JSON.

Pipeline:
1. opencli douyin search <keyword> (or a single --url)
2. opencli browser open/extract each video URL
3. extract title, publish time, place candidates, fish species
4. baidu geocode
5. insert into SQLite
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))  # allow running the script directly without installing the package

from spot_intake import Config, Intake, IntakeOptions
from spot_intake.adapters import GeocodeSkill, NullLlm, OpenaiLlm, OpencliBrowser, OpencliDouyinSearch, SqliteSpotStore
from spot_intake.fixtures import load_comments_fixture, load_extracted_fixture, load_search_item_fixture


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keyword", default="武汉钓鱼", help="Search keyword; also stored with --url imports unless overridden")
    ap.add_argument("--limit", type=int, default=1, help="Search result limit; ignored when --url is provided")
    ap.add_argument("--url", default="", help="Process a single Douyin video URL directly instead of searching by keyword")
    ap.add_argument("--city", default="武汉")
    ap.add_argument("--session", default="douyin-fishing-batch")
    ap.add_argument("--llm-url", default=None, help="OpenAI-compatible /v1/chat/completions endpoint (default: env or config)")
    ap.add_argument("--no-llm", action="store_true", help="Disable LLM place/fish extraction and use rule fallbacks only")
    ap.add_argument("--quiet-llm", action="store_true", help="Disable LLM debug logs")
    ap.add_argument("--delay-min", type=float, default=8.0, help="Minimum sleep seconds between video detail requests")
    ap.add_argument("--delay-max", type=float, default=20.0, help="Maximum sleep seconds between video detail requests")
    ap.add_argument("--max-video-places", type=int, default=3, help="Maximum video-text place candidates to geocode/save per video; 0 means all")
    ap.add_argument("--include-comments", dest="include_comments", action="store_true", default=True, help="Extract/save visible comments, run LLM comment spot extraction, and score comment quality (default)")
    ap.add_argument("--no-include-comments", dest="include_comments", action="store_false", help="Skip comment extraction and analysis")
    ap.add_argument("--comment-scrolls", type=int, default=0, help="How many times to scroll before reading comments")
    ap.add_argument("--comment-wait", type=float, default=2.0, help="Seconds to wait after each comment scroll")
    ap.add_argument("--comment-max", type=int, default=100, help="Maximum visible comments to extract/save per video")
    ap.add_argument("--comment-quality-group-size", type=int, default=5, help="LLM quality scoring group size for comments")
    ap.add_argument("--comment-keyword-group-size", type=int, default=20, help="LLM keyword extraction group size for comments")
    ap.add_argument("--extract-only", action="store_true", help="Only parse extracted video/comments and print JSON; no DB writes or geocoding")
    ap.add_argument("--extracted-json", default="", help="Saved opencli browser extract JSON; implies --extract-only")
    ap.add_argument("--extracted-text", default="", help="Saved raw extracted page text; implies --extract-only")
    ap.add_argument("--search-item-json", default="", help="Saved douyin search result/item JSON used as title/author/url context")
    ap.add_argument("--comments-json", default="", help="Saved comments JSON fixture; object with comments[] or a list")
    ap.add_argument("--comments-out", default="", help="Open --url, extract visible comments, save them as a fixture JSON, then exit")
    args = ap.parse_args()

    if args.extracted_json or args.extracted_text or args.comments_json:
        args.extract_only = True
    return args


def build_intake(args: argparse.Namespace, config: Config, *, with_store: bool) -> Intake:
    options = IntakeOptions(
        city=args.city,
        keyword=args.keyword,
        max_video_places=args.max_video_places,
        include_comments=args.include_comments,
        comment_scrolls=args.comment_scrolls,
        comment_wait=args.comment_wait,
        comment_max=args.comment_max,
        comment_quality_group_size=args.comment_quality_group_size,
        comment_keyword_group_size=args.comment_keyword_group_size,
        delay_min=args.delay_min,
        delay_max=args.delay_max,
    )
    llm = NullLlm() if args.no_llm else OpenaiLlm(args.llm_url or config.llm_url, debug=not args.quiet_llm)
    store = SqliteSpotStore(config.db_path) if with_store else _UnneededStore()
    return Intake(
        browser=OpencliBrowser(args.session, cwd=config.root),
        llm=llm,
        geocoder=GeocodeSkill(config.geocode_script, cwd=config.root),
        store=store,
        searcher=OpencliDouyinSearch(cwd=config.root),
        options=options,
    )


class _UnneededStore:
    """Extract-only runs never touch the store seam."""

    def __getattr__(self, name: str):
        raise RuntimeError(f"extract-only must not touch the store ({name})")


def run_extract_only(args: argparse.Namespace, config: Config) -> dict:
    direct_url = args.url.strip()
    search_item = load_search_item_fixture(args.search_item_json, url=direct_url)
    if args.extracted_json or args.extracted_text:
        extracted = load_extracted_fixture(args.extracted_json, args.extracted_text)
    elif args.comments_json and not direct_url:
        extracted = {"title": "", "content": ""}
    else:
        if not direct_url:
            raise ValueError("--extract-only requires --url unless a fixture path is provided")
        extracted = None  # live page via the browser seam
    comments = load_comments_fixture(args.comments_json) if args.comments_json else None
    intake = build_intake(args, config, with_store=False)
    return intake.extract(direct_url, search_item=search_item, extracted=extracted, comments=comments)


def dump_comments(args: argparse.Namespace, config: Config) -> None:
    """Replacement for the deleted extract_douyin_video_comments.py wrapper."""
    url = args.url.strip()
    if not url:
        raise ValueError("--comments-out requires --url")
    with OpencliBrowser(args.session, cwd=config.root) as browser:
        browser.open(url)
        comments = browser.extract_video_comments(scrolls=args.comment_scrolls, wait_seconds=args.comment_wait, max_comments=args.comment_max)
    out = Path(args.comments_out)
    out.write_text(json.dumps({"url": url, "comment_count": len(comments), "comments": comments}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[done] wrote {len(comments)} comments to {out}", flush=True)


def main() -> None:
    args = parse_args()
    config = Config.from_env()

    if args.comments_out:
        dump_comments(args, config)
        return

    if args.extract_only:
        report = run_extract_only(args, config)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return

    intake = build_intake(args, config, with_store=True)
    direct_url = args.url.strip()
    reports = [intake.collect_video(direct_url)] if direct_url else intake.collect_keyword(args.keyword, args.limit)
    results = [spot for report in reports for spot in report.spots]
    comment_results = [report.comment_result for report in reports if report.comment_result]
    print(json.dumps({"inserted_spots": len(results), "results": results, "comment_results": comment_results, "db": str(config.db_path)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
