#!/usr/bin/env python3
"""Backfill transcripts for already-collected videos (ADR 0001).

Targets are videos with no transcript row or status='error' — resume comes
for free since 'ok' and 'no_speech' rows are never selected again. Every
video goes through the same download → transcribe → LLM extract → merge
sub-flow as fresh collection (Intake.collect_transcript).
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))  # allow running the script directly without installing the package

from spot_intake import Config, Intake, IntakeOptions
from spot_intake.adapters import GeocodeSkill, MimoTranscriber, NullLlm, OpenaiLlm, OpencliBrowser, SqliteSpotStore


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser()
    ap.add_argument("--city", default="武汉")
    ap.add_argument("--session", default="douyin-transcript-backfill")
    ap.add_argument("--limit", type=int, default=0, help="Max videos to process this run; 0 means all pending")
    ap.add_argument("--dry-run", action="store_true", help="List pending videos and exit")
    ap.add_argument("--delay-min", type=float, default=8.0, help="Minimum sleep seconds between videos")
    ap.add_argument("--delay-max", type=float, default=20.0, help="Maximum sleep seconds between videos")
    ap.add_argument("--max-video-places", type=int, default=3, help="Maximum transcript place candidates to geocode/save per video; 0 means all")
    ap.add_argument("--llm-url", default=None, help="OpenAI-compatible /v1/chat/completions endpoint (default: env or config)")
    ap.add_argument("--no-llm", action="store_true", help="Store transcripts without LLM extraction")
    ap.add_argument("--quiet-llm", action="store_true", help="Disable LLM debug logs")
    return ap.parse_args()


def main() -> int:
    args = parse_args()
    config = Config.from_env()

    if not config.mimo_api_key and not args.dry_run:
        print("ERROR: MIMO_API_KEY is not set", file=sys.stderr)
        return 2

    with SqliteSpotStore(config.db_path) as store:
        pending = store.videos_pending_transcription(limit=args.limit)
        print(f"[plan] {len(pending)} videos pending transcription", flush=True)
        if args.dry_run:
            print(json.dumps(pending, ensure_ascii=False, indent=2))
            return 0

        llm = NullLlm() if args.no_llm else OpenaiLlm(args.llm_url or config.llm_url, debug=not args.quiet_llm)
        options = IntakeOptions(
            city=args.city,
            max_video_places=args.max_video_places,
            downloads_dir=str(config.downloads_dir),
        )
        with OpencliBrowser(args.session, cwd=config.root) as browser:
            intake = Intake(
                browser=browser,
                llm=llm,
                geocoder=GeocodeSkill(config.geocode_script, cwd=config.root, autocorrect_provider=config.geocode_corrector),
                store=store,
                transcriber=MimoTranscriber(api_key=config.mimo_api_key, model=config.asr_model),
                options=options,
            )
            results: list[dict] = []
            for index, video in enumerate(pending):
                try:
                    result = intake.collect_transcript(video["id"], video["url"], keyword=video["keyword"] or None)
                except Exception as exc:
                    # One bad video must never kill the run: record it as an
                    # error row so a later run retries it.
                    print(f"[{index + 1}/{len(pending)}] {video['url']} -> error (unhandled: {exc})", file=sys.stderr, flush=True)
                    store.upsert_transcript(video["id"], {
                        "status": "error", "error": f"unhandled: {str(exc)[:480]}", "transcript_text": "",
                        "audio_path": "", "srt_path": "", "model": "",
                        "raw_response_path": "", "summary": "", "extras_json": "",
                    })
                    result = {"video_id": video["id"], "url": video["url"], "status": "error", "spots_added": []}
                print(f"[{index + 1}/{len(pending)}] {video['url']} -> {result['status']} (+{len(result['spots_added'])} spots)", flush=True)
                results.append(result)
                if index < len(pending) - 1:
                    delay = random.uniform(args.delay_min, args.delay_max)
                    print(f"[throttle] sleep {delay:.1f}s before next video...", flush=True)
                    time.sleep(delay)

    by_status: dict[str, int] = {}
    for r in results:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
    print(json.dumps({
        "processed": len(results),
        "by_status": by_status,
        "spots_added": sum(len(r["spots_added"]) for r in results),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
