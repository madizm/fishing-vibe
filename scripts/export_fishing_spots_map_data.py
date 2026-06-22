#!/usr/bin/env python3
"""Export SQLite fishing spots to a browser-friendly GeoJSON-like JSON file."""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "fishing_spots.sqlite"
DEFAULT_OUT = ROOT / "web" / "fishing-spots.json"

FISH_PATTERNS: dict[str, list[str]] = {
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


def parse_json_list(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, list):
        return [str(v) for v in value if str(v).strip()]
    try:
        parsed = json.loads(str(value))
    except json.JSONDecodeError:
        parsed = [v.strip() for v in re.split(r"[,，、]", str(value))]
    if not isinstance(parsed, list):
        return []
    return [str(v).strip() for v in parsed if str(v).strip()]


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


def infer_fish_species(*texts: str) -> list[str]:
    haystack = "\n".join(t or "" for t in texts)
    found: list[str] = []
    for canonical, aliases in FISH_PATTERNS.items():
        if canonical in haystack or any(alias in haystack for alias in aliases):
            found.append(canonical)
    return normalize_fish_species(found)


def export(db_path: Path, out_path: Path) -> dict[str, Any]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT
          s.id, s.video_id, s.place_name, s.query_name, s.longitude, s.latitude,
          s.geocode_score, s.geocode_level, s.confidence, s.source_text, s.created_at,
          s.fish_species, s.fish_species_source, s.fish_confidence,
          s.quality_score, s.quality_score_source, s.quality_score_detail,
          v.platform, v.keyword, v.title, v.url, v.author, v.publish_time, v.collected_at
        FROM fishing_spots s
        LEFT JOIN videos v ON v.id = s.video_id
        WHERE s.longitude IS NOT NULL AND s.latitude IS NOT NULL
        ORDER BY COALESCE(s.confidence, 0) DESC, s.id ASC
        """
    ).fetchall()

    features: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        species = normalize_fish_species(parse_json_list(item.get("fish_species")))
        source = item.get("fish_species_source") or ""
        fish_confidence = item.get("fish_confidence")
        if not species:
            species = infer_fish_species(item.get("title") or "", item.get("source_text") or "")
            if species:
                source = "title+source_text(inferred_for_map)"
                fish_confidence = fish_confidence or 0.7

        features.append(
            {
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [float(item["longitude"]), float(item["latitude"])],
                },
                "properties": {
                    "id": item["id"],
                    "video_id": item["video_id"],
                    "place_name": item.get("place_name") or "未命名钓点",
                    "query_name": item.get("query_name") or "",
                    "confidence": item.get("confidence"),
                    "geocode_score": item.get("geocode_score"),
                    "geocode_level": item.get("geocode_level") or "",
                    "fish_species": species,
                    "fish_species_source": source,
                    "fish_confidence": fish_confidence,
                    "quality_score": item.get("quality_score"),
                    "quality_score_source": item.get("quality_score_source") or "",
                    "quality_score_detail": item.get("quality_score_detail") or "",
                    "title": item.get("title") or "",
                    "author": item.get("author") or "",
                    "url": item.get("url") or "",
                    "publish_time": item.get("publish_time") or "",
                    "platform": item.get("platform") or "douyin",
                    "keyword": item.get("keyword") or "",
                    "created_at": item.get("created_at") or item.get("collected_at") or "",
                    "source_text": (item.get("source_text") or "")[:500],
                },
            }
        )

    payload = {
        "type": "FeatureCollection",
        "name": "武汉钓鱼钓点",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source": str(db_path.relative_to(ROOT) if db_path.is_relative_to(ROOT) else db_path),
        "count": len(features),
        "features": features,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="SQLite database path")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="Output JSON path")
    args = parser.parse_args()
    payload = export(args.db, args.out)
    print(f"exported {payload['count']} spots -> {args.out}")


if __name__ == "__main__":
    main()
