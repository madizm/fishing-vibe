#!/usr/bin/env python3
"""Export SQLite fishing spots to a browser-friendly GeoJSON-like JSON file."""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from statistics import mean
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

CATEGORY_LABELS = {
    "place": "地点",
    "fish": "鱼种",
    "fish_condition": "鱼情",
    "water_condition": "水况",
    "access": "交通",
    "restriction": "限制",
    "bait_method": "钓法",
    "quality": "评价",
}
CATEGORY_ORDER = ["fish_condition", "quality", "water_condition", "restriction", "access", "bait_method", "fish", "place"]


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


def to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def round_optional(value: float | None, digits: int = 3) -> float | None:
    return round(value, digits) if value is not None else None


def publish_month(value: Any) -> str:
    match = re.match(r"^\d{4}-(\d{2})", str(value or ""))
    return match.group(1) if match else ""


def normalize_entity_name(value: Any) -> str:
    """Normalize a place label for conservative entity grouping.

    We intentionally keep coordinates in the grouping key and only merge rows whose
    normalized place label also matches. Coarse geocoding can make unrelated places
    share a coordinate; that data-quality issue is handled separately later.
    """
    text = str(value or "").strip().lower()
    text = re.sub(r"[\s\-_·•,，。:：;；/\\()（）\[\]【】]+", "", text)
    text = re.sub(r"(钓点|钓位|野钓点)$", "", text)
    return text or "未命名钓点"


def source_sort_key(source: dict[str, Any]) -> tuple[str, float]:
    return (source.get("publish_time") or "", float(source.get("confidence") or 0))


def score_stats(sources: list[dict[str, Any]]) -> dict[str, Any]:
    scored = [s for s in sources if to_float(s.get("quality_score")) is not None]
    if not scored:
        return {"quality_score": None, "score_count": 0, "score_min": None, "score_max": None}

    weighted_total = 0.0
    weight_total = 0.0
    scores: list[float] = []
    for source in scored:
        score = to_float(source.get("quality_score"))
        confidence = to_float(source.get("confidence"))
        if score is None:
            continue
        weight = confidence if confidence is not None and confidence > 0 else 0.5
        weighted_total += score * weight
        weight_total += weight
        scores.append(score)

    quality_score = weighted_total / weight_total if weight_total else mean(scores)
    return {
        "quality_score": round_optional(quality_score),
        "score_count": len(scores),
        "score_min": round_optional(min(scores)),
        "score_max": round_optional(max(scores)),
    }


def confidence_score(sources: list[dict[str, Any]]) -> float | None:
    values = [v for v in (to_float(s.get("confidence")) for s in sources) if v is not None]
    return round_optional(mean(values)) if values else None


def aggregate_keywords(sources: list[dict[str, Any]], limit: int = 12) -> list[str]:
    totals: dict[str, dict[str, Any]] = {}
    for source in sources:
        for item in source.pop("_comment_keywords", []):
            keyword = str(item.get("keyword") or "").strip()
            if not keyword:
                continue
            bucket = totals.setdefault(keyword, {"label": keyword, "count": 0, "confidence": 0.0})
            count = int(item.get("count") or 1)
            bucket["count"] += count
            bucket["confidence"] += float(item.get("avg_confidence") or 0) * count

    ranked = sorted(
        totals.values(),
        key=lambda item: (-item["count"], -(item["confidence"] / item["count"] if item["count"] else 0), item["label"]),
    )
    return [item["label"] for item in ranked[:limit]]


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone()
    return row is not None


def load_comment_keyword_summaries(conn: sqlite3.Connection) -> dict[int, dict[str, Any]]:
    """Build per-video comment summaries from comment_keywords."""
    if not table_exists(conn, "comment_keywords"):
        return {}

    rows = conn.execute(
        """
        SELECT
          video_id,
          keyword,
          category,
          COUNT(*) AS count,
          AVG(COALESCE(confidence, 0)) AS avg_confidence,
          GROUP_CONCAT(DISTINCT evidence) AS evidence
        FROM comment_keywords
        WHERE video_id IS NOT NULL
          AND TRIM(COALESCE(keyword, '')) != ''
          AND TRIM(COALESCE(category, '')) != ''
        GROUP BY video_id, category, keyword
        ORDER BY video_id ASC, count DESC, avg_confidence DESC, category ASC, keyword ASC
        """
    ).fetchall()

    by_video: dict[int, dict[str, Any]] = {}
    for row in rows:
        item = {
            "keyword": row["keyword"],
            "category": row["category"],
            "category_label": CATEGORY_LABELS.get(row["category"], row["category"]),
            "count": row["count"],
            "avg_confidence": round(float(row["avg_confidence"] or 0), 4),
            "evidence": [v for v in str(row["evidence"] or "").split(",") if v.strip()][:3],
        }
        bucket = by_video.setdefault(int(row["video_id"]), {"items": [], "by_category": {}})
        bucket["items"].append(item)
        bucket["by_category"].setdefault(row["category"], []).append(item)

    for bucket in by_video.values():
        parts: list[str] = []
        categories = sorted(
            bucket["by_category"],
            key=lambda c: (CATEGORY_ORDER.index(c) if c in CATEGORY_ORDER else len(CATEGORY_ORDER), c),
        )
        for category in categories:
            items = sorted(bucket["by_category"][category], key=lambda x: (-x["count"], -x["avg_confidence"], x["keyword"]))[:4]
            label = CATEGORY_LABELS.get(category, category)
            keywords = "、".join(item["keyword"] for item in items)
            if keywords:
                parts.append(f"{label}：{keywords}")
        bucket["summary"] = "；".join(parts) if parts else ""
        del bucket["by_category"]

    return by_video


def export(db_path: Path, out_path: Path) -> dict[str, Any]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    comment_keyword_summaries = load_comment_keyword_summaries(conn)
    rows = conn.execute(
        """
        SELECT
          s.id, s.video_id, s.place_name, s.query_name, s.longitude, s.latitude,
          s.confidence, s.source_text,
          s.fish_species,
          s.quality_score,
          v.title, v.url, v.author, v.publish_time
        FROM fishing_spots s
        LEFT JOIN videos v ON v.id = s.video_id
        WHERE s.longitude IS NOT NULL AND s.latitude IS NOT NULL
        ORDER BY COALESCE(s.confidence, 0) DESC, s.id ASC
        """
    ).fetchall()

    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        item = dict(row)
        comment_keywords = comment_keyword_summaries.get(item.get("video_id") or 0, {})
        species = normalize_fish_species(parse_json_list(item.get("fish_species")))
        if not species:
            species = infer_fish_species(item.get("title") or "", item.get("source_text") or "")

        lon = float(item["longitude"])
        lat = float(item["latitude"])
        display_name = item.get("place_name") or item.get("query_name") or "未命名钓点"
        coord_key = f"{lon:.6f},{lat:.6f}"
        key = (coord_key, normalize_entity_name(display_name))
        group = grouped.setdefault(
            key,
            {
                "name": display_name,
                "coordinates": [lon, lat],
                "aliases": [],
                "sources_by_key": {},
                "fish_species": [],
            },
        )

        for alias in [item.get("place_name"), item.get("query_name")]:
            alias = str(alias or "").strip()
            if alias and alias not in group["aliases"] and alias != group["name"]:
                group["aliases"].append(alias)
        for fish in species:
            if fish not in group["fish_species"]:
                group["fish_species"].append(fish)

        source_key = f"video:{item['video_id']}" if item.get("video_id") else f"spot:{item['id']}"
        candidate = {
            "id": item["id"],
            "video_id": item.get("video_id"),
            "title": item.get("title") or display_name,
            "author": item.get("author") or "",
            "url": item.get("url") or "",
            "publish_time": item.get("publish_time") or "",
            "publish_month": publish_month(item.get("publish_time")),
            "quality_score": round_optional(to_float(item.get("quality_score"))),
            "confidence": round_optional(to_float(item.get("confidence"))),
            "fish_species": species,
            "_comment_keywords": comment_keywords.get("items", []),
        }
        existing = group["sources_by_key"].get(source_key)
        if existing is None or source_sort_key(candidate) > source_sort_key(existing):
            group["sources_by_key"][source_key] = candidate

    features: list[dict[str, Any]] = []
    for index, group in enumerate(grouped.values(), 1):
        sources = sorted(group["sources_by_key"].values(), key=source_sort_key, reverse=True)
        monthly_scores: dict[str, dict[str, Any]] = {}
        for month in sorted({s.get("publish_month") for s in sources if s.get("publish_month")}):
            month_sources = [s for s in sources if s.get("publish_month") == month]
            monthly_scores[month] = {
                "source_count": len(month_sources),
                **score_stats(month_sources),
            }

        stats = score_stats(sources)
        properties = {
            "id": f"spot-{index:04d}",
            "place_name": group["name"],
            "aliases": group["aliases"][:6],
            "source_count": len(sources),
            "confidence": confidence_score(sources),
            **stats,
            "fish_species": group["fish_species"],
            "keywords": aggregate_keywords(sources),
            "monthly_scores": monthly_scores,
            "sources": [
                {key: value for key, value in source.items() if not key.startswith("_") and value not in (None, "", [])}
                for source in sources
            ],
        }
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": group["coordinates"]},
                "properties": properties,
            }
        )

    features.sort(
        key=lambda feature: (
            -(feature["properties"].get("quality_score") or -1),
            -feature["properties"].get("source_count", 0),
            feature["properties"].get("place_name", ""),
        )
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
