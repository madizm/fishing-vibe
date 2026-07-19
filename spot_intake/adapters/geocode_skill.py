"""Geocoder adapter: shells out to the geocode skill (Baidu provider, WGS84 output).

This is the only geocode call pattern in the pipeline — place name in,
WGS84 point (plus geocode metadata) out, or None when unresolvable.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from spot_intake.proc import run


class GeocodeSkill:
    def __init__(
        self,
        script_path: Path,
        cwd: Path | None = None,
        provider: str = "baidu",
        autocorrect_provider: str = "baidu",
        min_interval: float = 0.0,
    ) -> None:
        self.script_path = script_path
        self.cwd = cwd
        self.provider = provider
        self.autocorrect_provider = autocorrect_provider
        self.min_interval = min_interval  # seconds between API calls (tianditu 429s on bursts)
        self._last_call = 0.0

    def geocode(self, place: str, city: str = "武汉") -> dict | None:
        query = place if place.startswith(city) else f"{city}{place}"
        if self.provider == "tianditu":
            result = self._geocode_tianditu(query, city)
            if result is not None:
                return result
            print(f"[geocode] tianditu miss/weak for {query!r}, falling back to baidu", file=sys.stderr, flush=True)
        return self._geocode_baidu(query, city)

    # -- providers ---------------------------------------------------------------
    def _run_geocode(self, args: list[str]) -> dict:
        if self.min_interval > 0:
            wait = self.min_interval - (time.monotonic() - self._last_call)
            if wait > 0:
                time.sleep(wait)
        self._last_call = time.monotonic()
        out = run([sys.executable, str(self.script_path), *args], timeout=60, cwd=self.cwd)
        return json.loads(out)

    def _geocode_baidu(self, query: str, city: str) -> dict | None:
        data = self._run_geocode([
            "-p", "baidu", "geocode", "--to", "wgs84",
            "--autocorrect", "--autocorrect-provider", self.autocorrect_provider,
            "--region", city, query,
        ])
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

    def _geocode_tianditu(self, query: str, city: str) -> dict | None:
        """天地图主 geocode（geocoder → search2 纠错）。弱结果且纠错未命中时
        返回 None，由调用方走百度兜底。坐标 CGCS2000≈WGS84，无需转换。"""
        data = self._run_geocode([
            "-p", "tianditu", "geocode", "--to", "wgs84",
            "--autocorrect", "--region", city, query,
        ])
        autocorrect = data.get("_autocorrect") or {}
        if "result" in data:  # search2 纠错命中（百度形状的结果 envelope）
            result = data["result"]
            loc = result["location"]
            score = int(result.get("confidence", 0))
            level = result.get("level", "")
            name = result.get("name") or query
            address = result.get("address", "")
            area = result.get("area", "")
        else:
            raw_loc = data.get("location") or {}
            if "lon" not in raw_loc or "lat" not in raw_loc:
                return None
            loc = {"lng": raw_loc["lon"], "lat": raw_loc["lat"]}
            try:
                score = int(float(raw_loc.get("score") or 0))
            except (TypeError, ValueError):
                score = 0
            level = str(raw_loc.get("level", ""))
            name = query
            address = ""
            area = ""
        if autocorrect.get("applied"):
            corrected = str(autocorrect.get("corrected_query") or name)
            print(f"[geocode] tianditu autocorrect: {query} -> {corrected}", file=sys.stderr, flush=True)
            name = corrected
        elif score < 80:
            return None  # 弱结果且无纠错 → 百度兜底
        return {
            "query_name": name,
            "longitude": float(loc["lng"]),
            "latitude": float(loc["lat"]),
            "geocode_score": score,
            "geocode_level": level,
            "geocode_autocorrected": bool(autocorrect.get("applied")),
            "geocode_original_query": autocorrect.get("original_query", query),
            "geocode_corrected_query": name if autocorrect.get("applied") else "",
            "geocode_address": address,
            "geocode_area": area,
        }
