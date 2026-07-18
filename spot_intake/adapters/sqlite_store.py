"""SQLite adapter for the SpotStore seam. Owns the schema: any module that
touches data/fishing_spots.sqlite must take the DDL from here (init_db)."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from spot_intake.extract import normalize_comment_keyword, normalize_comment_keyword_category


def init_db(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS videos (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      platform TEXT DEFAULT 'douyin',
      keyword TEXT,
      title TEXT,
      url TEXT UNIQUE,
      author TEXT,
      publish_time TEXT,
      raw_text TEXT,
      collected_at TEXT
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS fishing_spots (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      video_id INTEGER,
      place_name TEXT,
      query_name TEXT,
      longitude REAL,
      latitude REAL,
      fish_species TEXT,
      fish_species_source TEXT,
      fish_confidence REAL,
      geocode_score INTEGER,
      geocode_level TEXT,
      confidence REAL,
      source_type TEXT,
      source_text TEXT,
      created_at TEXT,
      FOREIGN KEY(video_id) REFERENCES videos(id) ON DELETE CASCADE
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS video_comments (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      video_id INTEGER,
      author TEXT,
      text TEXT,
      comment_time TEXT,
      comment_time_raw TEXT,
      comment_time_standard TEXT,
      ip_location TEXT,
      is_author INTEGER DEFAULT 0,
      raw_json TEXT,
      collected_at TEXT,
      UNIQUE(video_id, author, text, comment_time_raw),
      FOREIGN KEY(video_id) REFERENCES videos(id) ON DELETE CASCADE
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS comment_keywords (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      video_id INTEGER,
      comment_id INTEGER,
      keyword TEXT,
      category TEXT,
      confidence REAL,
      evidence TEXT,
      source TEXT,
      created_at TEXT,
      UNIQUE(comment_id, keyword, category),
      FOREIGN KEY(video_id) REFERENCES videos(id) ON DELETE CASCADE,
      FOREIGN KEY(comment_id) REFERENCES video_comments(id) ON DELETE CASCADE
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS video_transcripts (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      video_id INTEGER NOT NULL UNIQUE,
      status TEXT NOT NULL,
      transcript_text TEXT,
      audio_path TEXT,
      srt_path TEXT,
      model TEXT,
      error TEXT,
      raw_response_path TEXT,
      summary TEXT,
      extras_json TEXT,
      transcribed_at TEXT,
      FOREIGN KEY(video_id) REFERENCES videos(id) ON DELETE CASCADE
    )""")
    # No separate comment_quality_scores table: normalized comment quality is
    # written directly onto fishing_spots. Drop the legacy derived table if it exists.
    conn.execute("DROP TABLE IF EXISTS comment_quality_scores")
    columns = {row[1] for row in conn.execute("PRAGMA table_info(fishing_spots)")}
    if "fish_species" not in columns:
        conn.execute("ALTER TABLE fishing_spots ADD COLUMN fish_species TEXT")
    if "fish_species_source" not in columns:
        conn.execute("ALTER TABLE fishing_spots ADD COLUMN fish_species_source TEXT")
    if "fish_confidence" not in columns:
        conn.execute("ALTER TABLE fishing_spots ADD COLUMN fish_confidence REAL")
    if "source_type" not in columns:
        conn.execute("ALTER TABLE fishing_spots ADD COLUMN source_type TEXT")
    if "quality_score" not in columns:
        conn.execute("ALTER TABLE fishing_spots ADD COLUMN quality_score REAL")
    if "quality_score_source" not in columns:
        conn.execute("ALTER TABLE fishing_spots ADD COLUMN quality_score_source TEXT")
    if "quality_score_detail" not in columns:
        conn.execute("ALTER TABLE fishing_spots ADD COLUMN quality_score_detail TEXT")
    conn.execute("UPDATE fishing_spots SET source_type='video_text' WHERE source_type IS NULL OR source_type='' ")


class SqliteSpotStore:
    """SpotStore adapter over SQLite. Owns its connection; use as a context
    manager or call close()."""

    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(exist_ok=True)
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path, isolation_level=None)
        init_db(self.conn)

    def __enter__(self) -> "SqliteSpotStore":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self.conn.close()

    # -- SpotStore protocol ------------------------------------------------------

    def video_exists(self, url: str) -> bool:
        return self.conn.execute("SELECT 1 FROM videos WHERE url=? LIMIT 1", (url,)).fetchone() is not None

    def upsert_video(self, keyword: str, video: dict) -> int:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.conn.execute(
            """INSERT OR IGNORE INTO videos(platform, keyword, title, url, author, publish_time, raw_text, collected_at)
               VALUES('douyin',?,?,?,?,?,?,?)""",
            (keyword, video["title"], video["url"], video["author"], video["publish_time"], video["raw_text"], now),
        )
        row = self.conn.execute("SELECT id FROM videos WHERE url=?", (video["url"],)).fetchone()
        if not row:
            raise RuntimeError(f"failed to upsert video: {video['url']}")
        return int(row[0])

    def video_metadata(self, video_id: int) -> dict:
        row = self.conn.execute("SELECT title, author, publish_time, raw_text FROM videos WHERE id=?", (video_id,)).fetchone()
        if not row:
            return {}
        return {"title": row[0] or "", "author": row[1] or "", "publish_time": row[2] or "", "raw_text": row[3] or ""}

    def existing_spot_names(self, video_id: int) -> set[str]:
        return {str(row[0]) for row in self.conn.execute("SELECT place_name FROM fishing_spots WHERE video_id=?", (video_id,)) if row[0]}

    def insert_video_comments(self, video_id: int, comments: list[dict]) -> list[dict]:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        saved: list[dict] = []
        for comment in comments:
            self.conn.execute(
                """INSERT OR IGNORE INTO video_comments(video_id, author, text, comment_time, comment_time_raw, comment_time_standard, ip_location, is_author, raw_json, collected_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (
                    video_id,
                    comment.get("author", ""),
                    comment.get("text", ""),
                    comment.get("comment_time", ""),
                    comment.get("comment_time_raw", ""),
                    comment.get("comment_time_standard", ""),
                    comment.get("ip_location", ""),
                    1 if comment.get("is_author") else 0,
                    json.dumps(comment, ensure_ascii=False),
                    now,
                ),
            )
            row = self.conn.execute(
                """SELECT id FROM video_comments
                   WHERE video_id=? AND author=? AND text=? AND comment_time_raw=?""",
                (video_id, comment.get("author", ""), comment.get("text", ""), comment.get("comment_time_raw", "")),
            ).fetchone()
            saved_comment = dict(comment)
            if row:
                saved_comment["comment_id"] = int(row[0])
            saved.append(saved_comment)
        return saved

    def insert_comment_keywords(self, video_id: int, keywords: list[dict]) -> list[dict]:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        saved: list[dict] = []
        for item in keywords:
            comment_id = item.get("comment_id")
            keyword = normalize_comment_keyword(item.get("keyword"))
            category = normalize_comment_keyword_category(item.get("category"))
            if not comment_id or not keyword or not category:
                continue
            self.conn.execute(
                """INSERT OR IGNORE INTO comment_keywords(video_id, comment_id, keyword, category, confidence, evidence, source, created_at)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (
                    video_id,
                    comment_id,
                    keyword,
                    category,
                    float(item.get("confidence", 0.0) or 0.0),
                    str(item.get("evidence", ""))[:200],
                    "comment_llm",
                    now,
                ),
            )
            saved_item = dict(item)
            saved_item["keyword"] = keyword
            saved_item["category"] = category
            saved.append(saved_item)
        return saved

    def apply_comment_quality_to_spots(self, video_id: int, quality: dict) -> None:
        """Write normalized comment quality score directly onto fishing_spots."""
        quality_score = quality.get("quality_score")
        if quality_score is None:
            return
        self.conn.execute(
            """UPDATE fishing_spots
               SET quality_score=?, quality_score_source=?, quality_score_detail=?
               WHERE video_id=?""",
            (quality_score, "comment_llm", quality.get("detail", ""), video_id),
        )

    def upsert_transcript(self, video_id: int, transcript: dict) -> None:
        """One transcript per video: re-transcribing replaces the row."""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.conn.execute(
            """INSERT INTO video_transcripts(video_id, status, transcript_text, audio_path, srt_path, model, error, raw_response_path, summary, extras_json, transcribed_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(video_id) DO UPDATE SET
                 status=excluded.status,
                 transcript_text=excluded.transcript_text,
                 audio_path=excluded.audio_path,
                 srt_path=excluded.srt_path,
                 model=excluded.model,
                 error=excluded.error,
                 raw_response_path=excluded.raw_response_path,
                 summary=excluded.summary,
                 extras_json=excluded.extras_json,
                 transcribed_at=excluded.transcribed_at""",
            (
                video_id,
                transcript.get("status", "error"),
                transcript.get("transcript_text", ""),
                transcript.get("audio_path", ""),
                transcript.get("srt_path", ""),
                transcript.get("model", ""),
                transcript.get("error", ""),
                transcript.get("raw_response_path", ""),
                transcript.get("summary", ""),
                transcript.get("extras_json", ""),
                now,
            ),
        )

    def transcript_for_video(self, video_id: int) -> dict | None:
        row = self.conn.execute(
            """SELECT video_id, status, transcript_text, audio_path, srt_path, model, error, raw_response_path, summary, extras_json, transcribed_at
               FROM video_transcripts WHERE video_id=?""",
            (video_id,),
        ).fetchone()
        if not row:
            return None
        keys = ["video_id", "status", "transcript_text", "audio_path", "srt_path", "model", "error", "raw_response_path", "summary", "extras_json", "transcribed_at"]
        return dict(zip(keys, row))

    def videos_pending_transcription(self, limit: int = 0) -> list[dict]:
        """Backfill targets: videos with no transcript row or status='error'.
        'ok'/'no_speech' are never retried (no_speech is a terminal state)."""
        sql = """SELECT v.id, v.url, v.keyword, v.title
                 FROM videos v LEFT JOIN video_transcripts t ON t.video_id = v.id
                 WHERE t.id IS NULL OR t.status = 'error'
                 ORDER BY v.id"""
        params: tuple = ()
        if limit > 0:
            sql += " LIMIT ?"
            params = (limit,)
        return [
            {"id": int(r[0]), "url": r[1] or "", "keyword": r[2] or "", "title": r[3] or ""}
            for r in self.conn.execute(sql, params)
        ]

    def insert_record(self, keyword: str, video: dict, spot: dict) -> None:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        video_id = self.upsert_video(keyword, video)
        self.conn.execute(
            """INSERT INTO fishing_spots(video_id, place_name, query_name, longitude, latitude, fish_species, fish_species_source, fish_confidence, geocode_score, geocode_level, confidence, source_type, source_text, quality_score, quality_score_source, quality_score_detail, created_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                video_id,
                spot["place_name"],
                spot["query_name"],
                spot["longitude"],
                spot["latitude"],
                json.dumps(video.get("fish_species", []), ensure_ascii=False),
                video.get("fish_species_source", ""),
                video.get("fish_confidence", 0.0),
                spot["geocode_score"],
                spot["geocode_level"],
                spot["confidence"],
                spot.get("source_type", "video_text"),
                spot.get("source_text", video["raw_text"][:500]),
                spot.get("quality_score"),
                spot.get("quality_score_source", ""),
                spot.get("quality_score_detail", ""),
                now,
            ),
        )
