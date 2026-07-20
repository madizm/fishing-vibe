#!/usr/bin/env python3
"""Atomically migrate the legacy SQLite database into PostGIS.

The target must be empty unless --replace is supplied. IDs and foreign-key
relationships are preserved; fishing_spots.longitude/latitude become WGS84
Point geometries (SRID 4326).
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

import psycopg
from psycopg import sql

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from spot_intake import Config
from spot_intake.adapters.postgis_store import SCHEMA_SQL

TABLES = ("videos", "fishing_spots", "video_comments", "comment_keywords", "video_transcripts")


def rows(source: sqlite3.Connection, table: str) -> list[dict]:
    return [dict(row) for row in source.execute(f"SELECT * FROM {table} ORDER BY id")]


def migrate(sqlite_path: Path, database_url: str, replace: bool = False) -> dict[str, int]:
    if not sqlite_path.is_file():
        raise FileNotFoundError(sqlite_path)

    source = sqlite3.connect(sqlite_path)
    source.row_factory = sqlite3.Row
    target = psycopg.connect(database_url, autocommit=True)
    try:
        target.execute(SCHEMA_SQL)
        existing = {table: target.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in TABLES}
        if any(existing.values()) and not replace:
            raise RuntimeError(f"target is not empty: {existing}; use --replace to overwrite it")

        data = {table: rows(source, table) for table in TABLES}
        with target.transaction():
            if replace:
                target.execute("TRUNCATE comment_keywords, video_transcripts, video_comments, fishing_spots, videos RESTART IDENTITY CASCADE")

            target.cursor().executemany(
                """INSERT INTO videos(id, platform, keyword, title, url, author, publish_time, raw_text, collected_at)
                   VALUES(%(id)s,%(platform)s,%(keyword)s,%(title)s,%(url)s,%(author)s,%(publish_time)s,%(raw_text)s,%(collected_at)s)""",
                data["videos"],
            )
            target.cursor().executemany(
                """INSERT INTO fishing_spots(
                     id, video_id, place_name, query_name, location, geocode_score, geocode_level,
                     confidence, source_text, created_at, fish_species, fish_species_source,
                     fish_confidence, source_type, quality_score, quality_score_source,
                     quality_score_detail, precision)
                   VALUES(%(id)s,%(video_id)s,%(place_name)s,%(query_name)s,
                     CASE WHEN %(longitude)s IS NULL OR %(latitude)s IS NULL THEN NULL
                          ELSE ST_SetSRID(ST_MakePoint(%(longitude)s,%(latitude)s),4326) END,
                     %(geocode_score)s,%(geocode_level)s,%(confidence)s,%(source_text)s,%(created_at)s,
                     %(fish_species)s,%(fish_species_source)s,%(fish_confidence)s,%(source_type)s,
                     %(quality_score)s,%(quality_score_source)s,%(quality_score_detail)s,%(precision)s)""",
                data["fishing_spots"],
            )
            target.cursor().executemany(
                """INSERT INTO video_comments(
                     id, video_id, author, text, comment_time, comment_time_raw, comment_time_standard,
                     ip_location, is_author, raw_json, collected_at)
                   VALUES(%(id)s,%(video_id)s,%(author)s,%(text)s,%(comment_time)s,%(comment_time_raw)s,
                          %(comment_time_standard)s,%(ip_location)s,%(is_author)s,%(raw_json)s,%(collected_at)s)""",
                [{**row, "is_author": bool(row.get("is_author"))} for row in data["video_comments"]],
            )
            target.cursor().executemany(
                """INSERT INTO comment_keywords(
                     id, video_id, comment_id, keyword, category, confidence, evidence, source, created_at)
                   VALUES(%(id)s,%(video_id)s,%(comment_id)s,%(keyword)s,%(category)s,%(confidence)s,
                          %(evidence)s,%(source)s,%(created_at)s)""",
                data["comment_keywords"],
            )
            target.cursor().executemany(
                """INSERT INTO video_transcripts(
                     id, video_id, status, transcript_text, audio_path, srt_path, model, error,
                     raw_response_path, summary, extras_json, transcribed_at)
                   VALUES(%(id)s,%(video_id)s,%(status)s,%(transcript_text)s,%(audio_path)s,%(srt_path)s,
                          %(model)s,%(error)s,%(raw_response_path)s,%(summary)s,%(extras_json)s,%(transcribed_at)s)""",
                data["video_transcripts"],
            )

            for table in TABLES:
                target.execute(
                    sql.SQL("""SELECT setval(pg_get_serial_sequence(%s, 'id'),
                                              GREATEST(COALESCE((SELECT MAX(id) FROM {}), 1), 1),
                                              EXISTS(SELECT 1 FROM {}))""").format(
                        sql.Identifier(table), sql.Identifier(table)
                    ),
                    (table,),
                )

            migrated = {table: target.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in TABLES}
            expected = {table: len(data[table]) for table in TABLES}
            if migrated != expected:
                raise RuntimeError(f"row-count validation failed: expected={expected}, actual={migrated}")
            bad_geometries = target.execute(
                """SELECT COUNT(*) FROM fishing_spots
                   WHERE location IS NOT NULL AND (ST_SRID(location) <> 4326 OR NOT ST_IsValid(location))"""
            ).fetchone()[0]
            if bad_geometries:
                raise RuntimeError(f"geometry validation failed: {bad_geometries} invalid rows")
        return migrated
    finally:
        source.close()
        target.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sqlite", type=Path, default=ROOT / "data" / "fishing_spots.sqlite")
    parser.add_argument("--database-url", default=None, help="PostgreSQL DSN (default: FISHING_VIBE_DATABASE_URL)")
    parser.add_argument("--replace", action="store_true", help="truncate and replace all target data")
    args = parser.parse_args()
    counts = migrate(args.sqlite, args.database_url or Config.from_env().database_url, args.replace)
    print("migration verified:", ", ".join(f"{table}={count}" for table, count in counts.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
