"""SQLite store integration tests on a real (temporary) database, plus
env-first config tests."""

import sqlite3

from spot_intake import Config
from spot_intake.adapters.sqlite_store import SqliteSpotStore, init_db

VIDEO = {
    "title": "东湖野钓",
    "url": "https://www.douyin.com/video/999",
    "author": "作者",
    "publish_time": "2026-07-01 10:00",
    "raw_text": "正文",
    "fish_species": ["黄尾鲴"],
    "fish_species_source": "rule:FISH_PATTERNS",
    "fish_confidence": 0.85,
}

SPOT = {
    "place_name": "东湖",
    "query_name": "武汉东湖",
    "longitude": 114.0,
    "latitude": 30.0,
    "geocode_score": 95,
    "geocode_level": "湖泊",
    "confidence": 0.9,
}


def test_init_db_is_idempotent_and_creates_all_tables(tmp_path):
    conn = sqlite3.connect(tmp_path / "t.sqlite")
    init_db(conn)
    init_db(conn)  # second run must not fail
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"videos", "fishing_spots", "video_comments", "comment_keywords"} <= tables


def test_store_roundtrip(tmp_path):
    with SqliteSpotStore(tmp_path / "t.sqlite") as store:
        assert not store.video_exists(VIDEO["url"])
        video_id = store.upsert_video("武汉钓鱼", VIDEO)
        assert store.video_exists(VIDEO["url"])
        # upsert is idempotent on url
        assert store.upsert_video("武汉钓鱼", VIDEO) == video_id

        saved = store.insert_video_comments(video_id, [
            {"author": "a", "text": "月湖不错", "comment_time": "3天前", "comment_time_raw": "3天前·湖北",
             "comment_time_standard": "2026-07-15 10:00:00", "ip_location": "湖北", "is_author": False},
        ])
        assert saved[0]["comment_id"]

        kw = store.insert_comment_keywords(video_id, [
            {"comment_id": saved[0]["comment_id"], "keyword": "有口", "category": "fish_condition", "confidence": 0.8, "evidence": "有口"},
            {"comment_id": saved[0]["comment_id"], "keyword": "钓点", "category": "place"},  # junk keyword dropped
        ])
        assert len(kw) == 1

        store.insert_record("武汉钓鱼", VIDEO, SPOT)
        assert store.existing_spot_names(video_id) == {"东湖"}

        store.apply_comment_quality_to_spots(video_id, {"quality_score": 0.75, "detail": "第1组"})
        row = store.conn.execute("SELECT quality_score, quality_score_source FROM fishing_spots WHERE video_id=?", (video_id,)).fetchone()
        assert row == (0.75, "comment_llm")

        # quality None is a no-op
        store.apply_comment_quality_to_spots(video_id, {"quality_score": None})
        assert store.conn.execute("SELECT quality_score FROM fishing_spots WHERE video_id=?", (video_id,)).fetchone()[0] == 0.75


def test_video_metadata_fallback(tmp_path):
    with SqliteSpotStore(tmp_path / "t.sqlite") as store:
        video_id = store.upsert_video("武汉钓鱼", VIDEO)
        meta = store.video_metadata(video_id)
        assert meta["title"] == "东湖野钓"
        assert store.video_metadata(99999) == {}


def test_config_defaults(monkeypatch, tmp_path):
    for var in ("FISHING_VIBE_DB", "GEOCODE_SCRIPT", "FISHING_VIBE_LLM_URL", "OPENAI_BASE_URL"):
        monkeypatch.delenv(var, raising=False)
    config = Config.from_env(root=tmp_path)
    assert config.db_path == tmp_path / "data" / "fishing_spots.sqlite"
    assert config.geocode_script == tmp_path / ".agents" / "skills" / "geocode" / "geocode.py"
    assert config.llm_url.endswith("/chat/completions")


def test_config_env_overrides(monkeypatch, tmp_path):
    monkeypatch.setenv("FISHING_VIBE_DB", str(tmp_path / "custom.sqlite"))
    monkeypatch.setenv("FISHING_VIBE_LLM_URL", "http://example.com/v1/chat/completions")
    config = Config.from_env(root=tmp_path)
    assert config.db_path == tmp_path / "custom.sqlite"
    assert config.llm_url == "http://example.com/v1/chat/completions"
