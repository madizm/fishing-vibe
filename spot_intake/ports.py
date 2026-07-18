"""Seam definitions for spot intake.

Each Protocol is a seam: production adapters (opencli subprocess, OpenAI-compatible
HTTP, geocode skill, SQLite) satisfy them in production; fakes satisfy them in tests.
Callers and tests cross the same interface.
"""

from __future__ import annotations

from typing import Protocol


class Browser(Protocol):
    """Page-level interaction with a Douyin video page."""

    def extract_video(self, url: str) -> dict:
        """Open a video URL and return {"title": ..., "content": ...} page text."""
        ...

    def extract_video_comments(self, scrolls: int = 0, wait_seconds: float = 2.0, max_comments: int = 100) -> list[dict]:
        """Return visible top-level comments and replies from the current page."""
        ...

    def download_audio(self, url: str, out_dir: str) -> dict:
        """Download the video's audio track into out_dir.

        Returns {"audio_path": str, "video_id": str}. Raises on any failure
        (browser, network, ffmpeg) — audio acquisition has no non-browser
        fallback (ADR 0001), so failures propagate to the caller.
        """
        ...


class Searcher(Protocol):
    """Keyword search over Douyin videos."""

    def search(self, keyword: str, limit: int) -> list[dict]:
        ...


class Llm(Protocol):
    """Domain-level extraction over text/comments. Implementations normalize
    their own output; callers never see raw model responses."""

    def extract_places(self, text: str, city: str) -> list[str]:
        ...

    def extract_fish_species(self, text: str) -> list[str]:
        ...

    def extract_comment_places(self, comments: list[dict], city: str) -> list[dict]:
        ...

    def extract_comment_keywords(self, comments: list[dict], city: str, group_size: int = 20) -> list[dict]:
        ...

    def score_comment_quality(self, comments: list[dict], group_size: int = 5) -> list[dict]:
        ...

    def extract_transcript_places(self, transcript: str, city: str) -> list[str]:
        """Place candidates from an ASR transcript. Implementations use a
        transcript-aware prompt (homophone correction in context, ADR 0001)."""
        ...

    def extract_transcript_fish_species(self, transcript: str) -> list[str]:
        ...

    def summarize_transcript(self, transcript: str) -> dict:
        """Human-readable {"summary": str, "extras": dict}. Never normalized
        into spot data (see 转写摘要 in CONTEXT.md)."""
        ...


class Geocoder(Protocol):
    """Place name -> WGS84 point, or None when unresolvable."""

    def geocode(self, place: str, city: str) -> dict | None:
        ...


class SpotStore(Protocol):
    """Persistence for videos, comments, keywords, and fishing spots."""

    def video_exists(self, url: str) -> bool:
        ...

    def upsert_video(self, keyword: str, video: dict) -> int:
        ...

    def video_metadata(self, video_id: int) -> dict:
        ...

    def existing_spot_names(self, video_id: int) -> set[str]:
        ...

    def insert_video_comments(self, video_id: int, comments: list[dict]) -> list[dict]:
        ...

    def insert_comment_keywords(self, video_id: int, keywords: list[dict]) -> list[dict]:
        ...

    def apply_comment_quality_to_spots(self, video_id: int, quality: dict) -> None:
        ...

    def insert_record(self, keyword: str, video: dict, spot: dict) -> None:
        ...

    def upsert_transcript(self, video_id: int, transcript: dict) -> None:
        """Insert or replace the video's transcript row (one per video).

        Keys: status ('ok'|'no_speech'|'error'), transcript_text, audio_path,
        srt_path, model, error, raw_response_path, summary, extras_json.
        """
        ...


class Transcriber(Protocol):
    """Audio file -> transcript. Model quirks (container conversion, no-speech
    detection) are encapsulated by implementations."""

    def transcribe(self, audio_path: str, out_prefix: str | None = None) -> dict:
        """Transcribe one audio file.

        Returns {"status": "ok"|"no_speech", "text": str, "model": str,
        "srt_path": str|None, "raw_response_path": str|None}.
        Raises on API/IO failure — the caller records status='error'.
        """
        ...
