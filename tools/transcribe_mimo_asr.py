#!/usr/bin/env python3
"""Manual debug CLI for the Mimo ASR adapter.

The implementation lives in spot_intake.adapters.mimo_asr (the Transcriber
seam's production adapter); this shell exists so a single audio file can be
transcribed by hand when investigating bad transcripts.

Reads the API key from MIMO_API_KEY.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from spot_intake.adapters.mimo_asr import DEFAULT_MODEL, MimoTranscriber
from spot_intake.config import load_dotenv


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("audio", help="audio file, e.g. .m4a/.mp3/.wav")
    ap.add_argument("--language", default="auto")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--out-prefix", default=None)
    args = ap.parse_args()

    load_dotenv()
    api_key = os.environ.get("MIMO_API_KEY", "").strip()
    if not api_key:
        print("ERROR: MIMO_API_KEY is not set", file=sys.stderr)
        return 2

    transcriber = MimoTranscriber(api_key=api_key, model=args.model, language=args.language)
    try:
        result = transcriber.transcribe(args.audio, out_prefix=args.out_prefix)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(json.dumps({**result, "text_preview": result["text"][:500]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
