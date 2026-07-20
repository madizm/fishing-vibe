"""PostGIS store integration tests against an isolated Docker test database."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import psycopg
import pytest
from psycopg import sql

from spot_intake import Config
from spot_intake.adapters.postgis_store import PostgisSpotStore, init_db
from scripts.migrate_sqlite_to_postgis import migrate

TEST_DATABASE_URL = os.getenv(
    "FISHING_VIBE_TEST_DATABASE_URL",
    "postgresql://fishing_vibe:fishing_vibe@localhost:5432/fishing_vibe_test",
)
ADMIN_DATABASE_URL = os.getenv(
    "FISHING_VIBE_ADMIN_DATABASE_URL",
    "postgresql://fishing_vibe:fishing_vibe@localhost:5432/postgres",
)

VIDEO = {
    "title": "东湖野钓", "url": "https://www.douyin.com/video/999", "author": "作者",
    "publish_time": "2026-07-01 10:00", "raw_text": "正文", "fish_species": ["黄尾鲴"],
    "fish_species_source": "rule:FISH_PATTERNS", "fish_confidence": 0.85,
}
SPOT = {
    "place_name": "东湖", "query_name": "武汉东湖", "longitude": 114.0, "latitude": 30.0,
    "geocode_score": 95, "geocode_level": "湖泊", "confidence": 0.9,
}
TRANSCRIPT = {
    "status": "ok", "transcript_text": "今天在府河用蚯蚓上了三条翘嘴", "audio_path": "downloads/999.m4a",
    "srt_path": "downloads/999.mimo.srt", "model": "mimo-v2.5-asr", "error": "",
    "raw_response_path": "downloads/999.mimo.response.json", "summary": "钓友在府河用蚯蚓作钓，收获三条翘嘴。",
    "extras_json": '{"钓法/饵料": "蚯蚓"}',
}


@pytest.fixture(scope="session")
def test_database_url() -> str:
    try:
        admin = psycopg.connect(ADMIN_DATABASE_URL, autocommit=True)
    except psycopg.OperationalError as exc:
        pytest.skip(f"PostGIS test server unavailable; run `docker compose up -d postgis`: {exc}")
    with admin:
        exists = admin.execute("SELECT 1 FROM pg_database WHERE datname='fishing_vibe_test'").fetchone()
        if not exists:
            admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier("fishing_vibe_test")))
    with psycopg.connect(TEST_DATABASE_URL, autocommit=True) as conn:
        init_db(conn)
    return TEST_DATABASE_URL


@pytest.fixture(autouse=True)
def clean_database(test_database_url: str) -> None:
    with psycopg.connect(test_database_url, autocommit=True) as conn:
        conn.execute("TRUNCATE comment_keywords, video_transcripts, video_comments, fishing_spots, videos RESTART IDENTITY CASCADE")


def test_init_db_is_idempotent_and_creates_all_tables(test_database_url):
    with psycopg.connect(test_database_url, autocommit=True) as conn:
        init_db(conn)
        init_db(conn)
        tables = {r[0] for r in conn.execute("SELECT tablename FROM pg_tables WHERE schemaname='public'")}
        assert {"videos", "fishing_spots", "video_comments", "comment_keywords", "video_transcripts"} <= tables
        assert conn.execute("SELECT postgis_version()").fetchone()[0]


def test_store_roundtrip(test_database_url):
    with PostgisSpotStore(test_database_url) as store:
        assert not store.video_exists(VIDEO["url"])
        video_id = store.upsert_video("武汉钓鱼", VIDEO)
        assert store.video_exists(VIDEO["url"])
        assert store.upsert_video("武汉钓鱼", VIDEO) == video_id
        saved = store.insert_video_comments(video_id, [{
            "author": "a", "text": "月湖不错", "comment_time": "3天前", "comment_time_raw": "3天前·湖北",
            "comment_time_standard": "2026-07-15 10:00:00", "ip_location": "湖北", "is_author": False,
        }])
        assert saved[0]["comment_id"]
        # Existing duplicate rows in the migrated DB are preserved, while new writes remain idempotent.
        assert store.insert_video_comments(video_id, [saved[0]])[0]["comment_id"] == saved[0]["comment_id"]
        kw = store.insert_comment_keywords(video_id, [
            {"comment_id": saved[0]["comment_id"], "keyword": "有口", "category": "fish_condition", "confidence": 0.8, "evidence": "有口"},
            {"comment_id": saved[0]["comment_id"], "keyword": "钓点", "category": "place"},
        ])
        assert len(kw) == 1
        store.insert_record("武汉钓鱼", VIDEO, SPOT)
        assert store.existing_spot_names(video_id) == {"东湖"}
        lon, lat, srid = store.conn.execute("SELECT ST_X(location), ST_Y(location), ST_SRID(location) FROM fishing_spots").fetchone()
        assert (lon, lat, srid) == (114.0, 30.0, 4326)
        store.apply_comment_quality_to_spots(video_id, {"quality_score": 0.75, "detail": "第1组"})
        assert store.conn.execute("SELECT quality_score, quality_score_source FROM fishing_spots WHERE video_id=%s", (video_id,)).fetchone() == (0.75, "comment_llm")
        store.apply_comment_quality_to_spots(video_id, {"quality_score": None})
        assert store.conn.execute("SELECT quality_score FROM fishing_spots WHERE video_id=%s", (video_id,)).fetchone()[0] == 0.75


def test_video_metadata_fallback(test_database_url):
    with PostgisSpotStore(test_database_url) as store:
        video_id = store.upsert_video("武汉钓鱼", VIDEO)
        assert store.video_metadata(video_id)["title"] == "东湖野钓"
        assert store.video_metadata(99999) == {}


def test_config_defaults(monkeypatch, tmp_path):
    for var in ("FISHING_VIBE_DATABASE_URL", "GEOCODE_SCRIPT", "FISHING_VIBE_LLM_URL", "OPENAI_BASE_URL"):
        monkeypatch.delenv(var, raising=False)
    config = Config.from_env(root=tmp_path)
    assert config.database_url == "postgresql://fishing_vibe:fishing_vibe@localhost:5432/fishing_vibe"
    assert config.geocode_script == tmp_path / ".agents" / "skills" / "geocode" / "geocode.py"
    assert config.llm_url.endswith("/chat/completions")


def test_config_env_overrides(monkeypatch, tmp_path):
    monkeypatch.setenv("FISHING_VIBE_DATABASE_URL", "postgresql://u:p@db:5432/custom")
    monkeypatch.setenv("FISHING_VIBE_LLM_URL", "http://example.com/v1/chat/completions")
    config = Config.from_env(root=tmp_path)
    assert config.database_url == "postgresql://u:p@db:5432/custom"
    assert config.llm_url == "http://example.com/v1/chat/completions"


def test_transcript_upsert_and_readback(test_database_url):
    with PostgisSpotStore(test_database_url) as store:
        video_id = store.upsert_video("武汉钓鱼", VIDEO)
        store.upsert_transcript(video_id, TRANSCRIPT)
        row = store.transcript_for_video(video_id)
        assert row["status"] == "ok"
        assert row["transcript_text"] == TRANSCRIPT["transcript_text"]
        assert row["summary"] == TRANSCRIPT["summary"]
        assert row["extras_json"] == TRANSCRIPT["extras_json"]
        assert row["transcribed_at"]


def test_transcript_upsert_replaces_existing_row(test_database_url):
    with PostgisSpotStore(test_database_url) as store:
        video_id = store.upsert_video("武汉钓鱼", VIDEO)
        store.upsert_transcript(video_id, {**TRANSCRIPT, "status": "error", "error": "timeout"})
        store.upsert_transcript(video_id, TRANSCRIPT)
        assert store.conn.execute("SELECT COUNT(*) FROM video_transcripts WHERE video_id=%s", (video_id,)).fetchone()[0] == 1
        assert store.transcript_for_video(video_id)["status"] == "ok"


def test_transcript_cascade_deletes_with_video(test_database_url):
    with PostgisSpotStore(test_database_url) as store:
        video_id = store.upsert_video("武汉钓鱼", VIDEO)
        store.upsert_transcript(video_id, TRANSCRIPT)
        store.conn.execute("DELETE FROM videos WHERE id=%s", (video_id,))
        assert store.transcript_for_video(video_id) is None


def test_videos_pending_transcription_targets_missing_and_error_only(test_database_url):
    with PostgisSpotStore(test_database_url) as store:
        ids = [store.upsert_video("武汉钓鱼", {**VIDEO, "url": f"https://www.douyin.com/video/{999+i}"}) for i in range(5)]
        store.upsert_transcript(ids[1], {**TRANSCRIPT, "status": "ok"})
        store.upsert_transcript(ids[2], {**TRANSCRIPT, "status": "no_speech", "transcript_text": ""})
        store.upsert_transcript(ids[3], {**TRANSCRIPT, "status": "error", "error": "boom"})
        store.upsert_transcript(ids[4], {**TRANSCRIPT, "status": "unavailable", "transcript_text": ""})
        pending = store.videos_pending_transcription()
        assert [v["id"] for v in pending] == [ids[0], ids[3]]
        assert len(store.videos_pending_transcription(limit=1)) == 1


def test_precision_classified_at_insert_and_backfilled(test_database_url):
    with PostgisSpotStore(test_database_url) as store:
        store.insert_record("武汉钓鱼", VIDEO, SPOT)
        store.insert_record("武汉钓鱼", VIDEO, {**SPOT, "place_name": "府河", "query_name": "武汉府河"})
        store.insert_record("武汉钓鱼", VIDEO, {**SPOT, "place_name": "武昌区", "query_name": "武昌区"})
        assert dict(store.conn.execute("SELECT place_name, precision FROM fishing_spots"))["府河"] == "segment"
        store.conn.execute("UPDATE fishing_spots SET precision=NULL WHERE place_name='府河'")
        init_db(store.conn)
        assert store.conn.execute("SELECT precision FROM fishing_spots WHERE place_name='府河'").fetchone()[0] == "segment"


def test_legacy_sqlite_migration_is_committed_and_preserves_counts(test_database_url):
    sqlite_path = Path(__file__).resolve().parents[1] / "data" / "fishing_spots.sqlite"
    if not sqlite_path.exists():
        pytest.skip("legacy migration fixture is not available")
    with sqlite3.connect(sqlite_path) as source:
        expected = {
            table: source.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("videos", "fishing_spots", "video_comments", "comment_keywords", "video_transcripts")
        }
    assert migrate(sqlite_path, test_database_url) == expected
    # A new connection proves the migration transaction was committed.
    with psycopg.connect(test_database_url) as conn:
        assert conn.execute("SELECT COUNT(*) FROM fishing_spots").fetchone()[0] == expected["fishing_spots"]
        assert conn.execute("SELECT COUNT(*) FROM fishing_spots WHERE ST_SRID(location)=4326").fetchone()[0] == expected["fishing_spots"]
