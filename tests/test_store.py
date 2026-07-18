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
    assert {"videos", "fishing_spots", "video_comments", "comment_keywords", "video_transcripts"} <= tables


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


TRANSCRIPT = {
    "status": "ok",
    "transcript_text": "今天在府河用蚯蚓上了三条翘嘴",
    "audio_path": "downloads/999.m4a",
    "srt_path": "downloads/999.mimo.srt",
    "model": "mimo-v2.5-asr",
    "error": "",
    "raw_response_path": "downloads/999.mimo.response.json",
    "summary": "钓友在府河用蚯蚓作钓，收获三条翘嘴。",
    "extras_json": '{"钓法/饵料": "蚯蚓"}',
}


def test_transcript_upsert_and_readback(tmp_path):
    with SqliteSpotStore(tmp_path / "t.sqlite") as store:
        video_id = store.upsert_video("武汉钓鱼", VIDEO)
        store.upsert_transcript(video_id, TRANSCRIPT)
        row = store.transcript_for_video(video_id)
        assert row["status"] == "ok"
        assert row["transcript_text"] == TRANSCRIPT["transcript_text"]
        assert row["summary"] == TRANSCRIPT["summary"]
        assert row["extras_json"] == TRANSCRIPT["extras_json"]
        assert row["model"] == "mimo-v2.5-asr"
        assert row["transcribed_at"]


def test_transcript_upsert_replaces_existing_row(tmp_path):
    with SqliteSpotStore(tmp_path / "t.sqlite") as store:
        video_id = store.upsert_video("武汉钓鱼", VIDEO)
        store.upsert_transcript(video_id, {**TRANSCRIPT, "status": "error", "error": "timeout"})
        store.upsert_transcript(video_id, TRANSCRIPT)
        rows = store.conn.execute("SELECT * FROM video_transcripts WHERE video_id=?", (video_id,)).fetchall()
        assert len(rows) == 1  # one transcript per video
        assert store.transcript_for_video(video_id)["status"] == "ok"


def test_transcript_cascade_deletes_with_video(tmp_path):
    with SqliteSpotStore(tmp_path / "t.sqlite") as store:
        store.conn.execute("PRAGMA foreign_keys=ON")
        video_id = store.upsert_video("武汉钓鱼", VIDEO)
        store.upsert_transcript(video_id, TRANSCRIPT)
        store.conn.execute("DELETE FROM videos WHERE id=?", (video_id,))
        assert store.transcript_for_video(video_id) is None


def test_videos_pending_transcription_targets_missing_and_error_only(tmp_path):
    with SqliteSpotStore(tmp_path / "t.sqlite") as store:
        v1 = store.upsert_video("武汉钓鱼", VIDEO)
        v2 = store.upsert_video("武汉钓鱼", {**VIDEO, "url": "https://www.douyin.com/video/1000"})
        v3 = store.upsert_video("武汉钓鱼", {**VIDEO, "url": "https://www.douyin.com/video/1001"})
        v4 = store.upsert_video("武汉钓鱼", {**VIDEO, "url": "https://www.douyin.com/video/1002"})
        store.upsert_transcript(v2, {**TRANSCRIPT, "status": "ok"})
        store.upsert_transcript(v3, {**TRANSCRIPT, "status": "no_speech", "transcript_text": ""})
        store.upsert_transcript(v4, {**TRANSCRIPT, "status": "error", "error": "boom"})

        pending = store.videos_pending_transcription()
        assert [v["id"] for v in pending] == [v1, v4]  # no row + error; ok/no_speech skipped (resume)
        assert pending[0]["url"] == VIDEO["url"]
        assert pending[0]["keyword"] == "武汉钓鱼"

        limited = store.videos_pending_transcription(limit=1)
        assert len(limited) == 1


def test_load_dotenv_reads_crlf_and_never_overrides(monkeypatch, tmp_path):
    from spot_intake.config import load_dotenv

    (tmp_path / ".env").write_bytes(b"MIMO_API_KEY=sk-test\r\n# comment\r\nexport OTHER=\"q v\"\r\n")
    monkeypatch.setenv("MIMO_API_KEY", "sk-real")
    monkeypatch.delenv("OTHER", raising=False)
    monkeypatch.chdir(tmp_path)

    import os

    assert load_dotenv() == tmp_path / ".env"
    assert os.environ["MIMO_API_KEY"] == "sk-real"  # existing env wins
    assert os.environ["OTHER"] == "q v"  # quotes and \r stripped
