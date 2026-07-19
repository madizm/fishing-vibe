"""Geocoder adapter: shells out to the geocode skill (Baidu provider, WGS84 output).

This is the only geocode call pattern in the pipeline — place name in,
WGS84 point (plus geocode metadata) out, or None when unresolvable.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from spot_intake.proc import run


class GeocodeSkill:
    def __init__(self, script_path: Path, cwd: Path | None = None, autocorrect_provider: str = "baidu") -> None:
        self.script_path = script_path
        self.cwd = cwd
        self.autocorrect_provider = autocorrect_provider

    def geocode(self, place: str, city: str = "武汉") -> dict | None:
        query = place if place.startswith(city) else f"{city}{place}"
        out = run(
            [
                sys.executable,
                str(self.script_path),
                "-p",
                "baidu",
                "geocode",
                "--to",
                "wgs84",
                "--autocorrect",
                "--autocorrect-provider",
                self.autocorrect_provider,
                "--region",
                city,
                query,
            ],
            timeout=60,
            cwd=self.cwd,
        )
        data = json.loads(out)
        # 百度地图返回 status 为整数 0
        if data.get("status") != 0 or "result" not in data:
            return None
        result = data["result"]
        loc = result["location"]
        autocorrect = data.get("_autocorrect") or {}
        corrected_query = autocorrect.get("corrected_query") or result.get("name") or query
        if autocorrect.get("applied"):
            print(f"[geocode] autocorrect: {query} -> {corrected_query}", file=sys.stderr, flush=True)
        return {
            "query_name": corrected_query if autocorrect.get("applied") else query,
            "longitude": float(loc["lng"]),
            "latitude": float(loc["lat"]),
            "geocode_score": int(result.get("confidence", 0)),
            "geocode_level": result.get("level", ""),
            "geocode_autocorrected": bool(autocorrect.get("applied")),
            "geocode_original_query": autocorrect.get("original_query", query),
            "geocode_corrected_query": corrected_query if autocorrect.get("applied") else "",
            "geocode_address": result.get("address", ""),
            "geocode_area": result.get("area", ""),
        }
