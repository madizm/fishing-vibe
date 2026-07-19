#!/usr/bin/env python3
"""Re-extract fishing spots from STORED transcripts (no download, no ASR).

Re-harvests the 113 'ok' transcripts with the current extraction prompt and
the tianditu-primary geocoder (geocoder -> search2 correction -> baidu
fallback). Append-only: places already recorded for a video are skipped;
existing spots are never modified. --divergence-report additionally
re-geocodes all existing spots read-only and writes a comparison JSON
(case material for issue #2).
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))  # allow running the script directly without installing the package

from spot_intake import Config, Intake, IntakeOptions
from spot_intake.adapters import GeocodeSkill, OpenaiLlm, SqliteSpotStore


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser()
    ap.add_argument("--city", default="武汉")
    ap.add_argument("--limit", type=int, default=0, help="Max videos to process; 0 means all")
    ap.add_argument("--dry-run", action="store_true", help="List target videos and exit")
    ap.add_argument("--max-video-places", type=int, default=3, help="Maximum new spots per video; 0 means all")
    ap.add_argument("--provider", default="tianditu", choices=["tianditu", "baidu"], help="Primary geocoder (default tianditu)")
    ap.add_argument("--corrector", default="baidu", choices=["baidu", "tianditu"], help="Baidu-path POI corrector (default baidu)")
    ap.add_argument("--geocode-interval", type=float, default=1.0, help="Min seconds between geocode calls (tianditu 429s on bursts)")
    ap.add_argument("--llm-url", default=None)
    ap.add_argument("--quiet-llm", action="store_true")
    ap.add_argument("--divergence-report", default="", help="Also re-geocode all existing spots read-only and write the comparison JSON to this path")
    return ap.parse_args()


def distance_m(lon1: float, lat1: float, lon2: float, lat2: float) -> int:
    return round(math.hypot((lon1 - lon2) * 95000, (lat1 - lat2) * 111000))


def build_divergence_report(store: SqliteSpotStore, geocoder: GeocodeSkill, city: str) -> list[dict]:
    rows = store.conn.execute(
        """SELECT s.id, s.video_id, s.place_name, s.longitude, s.latitude, s.source_type, s.geocode_score
           FROM fishing_spots s ORDER BY s.id"""
    ).fetchall()
    report: list[dict] = []
    for spot_id, video_id, place, lon, lat, source_type, old_score in rows:
        entry = {"spot_id": spot_id, "video_id": video_id, "place_name": place, "source_type": source_type,
                 "old": {"longitude": lon, "latitude": lat, "geocode_score": old_score}}
        try:
            geo = geocoder.geocode(place, city)
        except Exception as exc:
            entry["error"] = str(exc)[:200]
            report.append(entry)
            continue
        if not geo:
            entry["new"] = None
            report.append(entry)
            continue
        entry["new"] = {"query_name": geo["query_name"], "longitude": geo["longitude"], "latitude": geo["latitude"],
                        "geocode_score": geo["geocode_score"], "autocorrected": geo["geocode_autocorrected"]}
        entry["distance_m"] = distance_m(lon, lat, geo["longitude"], geo["latitude"])
        report.append(entry)
    return report


def main() -> int:
    args = parse_args()
    config = Config.from_env()

    with SqliteSpotStore(config.db_path) as store:
        targets = store.videos_with_transcript(status="ok", limit=args.limit)
        print(f"[plan] {len(targets)} videos with usable transcripts", flush=True)
        if args.dry_run:
            print(json.dumps(targets, ensure_ascii=False, indent=2))
            return 0

        geocoder = GeocodeSkill(
            config.geocode_script,
            cwd=config.root,
            provider=args.provider,
            autocorrect_provider=args.corrector,
            min_interval=args.geocode_interval,
        )

        if args.divergence_report:
            print(f"[divergence] re-geocoding existing spots read-only...", flush=True)
            report = build_divergence_report(store, geocoder, args.city)
            out = Path(args.divergence_report)
            out.write_text(json.dumps({
                "generated_at": datetime.now().isoformat(timespec="seconds"),
                "provider": args.provider,
                "city": args.city,
                "spot_count": len(report),
                "divergent_over_1km": [e for e in report if e.get("distance_m", 0) > 1000],
                "entries": report,
            }, ensure_ascii=False, indent=2), encoding="utf-8")
            divergent = sum(1 for e in report if e.get("distance_m", 0) > 1000)
            print(f"[divergence] wrote {out} ({divergent} spots diverge >1km)", flush=True)

        llm = OpenaiLlm(args.llm_url or config.llm_url, debug=not args.quiet_llm)
        options = IntakeOptions(city=args.city, max_video_places=args.max_video_places)
        intake = Intake(
            browser=None,  # no download: reads stored transcripts only
            llm=llm,
            geocoder=geocoder,
            store=store,
            options=options,
        )
        results: list[dict] = []
        for index, video in enumerate(targets):
            result = intake.reextract_transcript_spots(video["id"])
            added = len(result["spots_added"])
            names = [s["place_name"] for s in result["spots_added"]]
            print(f"[{index + 1}/{len(targets)}] video {video['id']} candidates={result['candidates']} +{added} {names}", flush=True)
            results.append(result)

    total = sum(len(r["spots_added"]) for r in results)
    print(json.dumps({"processed": len(results), "spots_added": total}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
