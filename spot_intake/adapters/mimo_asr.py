"""Mimo ASR adapter for the Transcriber seam.

Model quirks live here and nowhere else: m4a/mp4 containers are converted to
16kHz mono MP3 before upload (the API rejects audio/mp4), and empty or
filler-only transcripts are reported as status='no_speech' so the pipeline
never retries videos that simply have no spoken content (ADR 0001).
"""

from __future__ import annotations

import base64
import json
import mimetypes
import re
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

API_URL = "https://api.xiaomimimo.com/v1/chat/completions"
DEFAULT_MODEL = "mimo-v2.5-asr"
ALLOWED_MIMES = {"audio/wav", "audio/mpeg", "audio/mp3"}

# Filler interjections; a transcript consisting of only these carries no information.
_FILLER_CHARS = set("嗯啊哦呃唉哎哼哈呜喂诶噢喔")


def fmt_ts(seconds: float) -> str:
    ms = round(float(seconds) * 1000)
    h, rem = divmod(ms, 3600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def pick_text(obj: Any) -> str:
    """Best-effort text extraction across likely chat response shapes."""
    try:
        content = obj["choices"][0]["message"]["content"]
    except Exception:
        content = obj

    if isinstance(content, str):
        stripped = content.strip()
        if stripped.startswith("{") or stripped.startswith("["):
            try:
                return pick_text(json.loads(stripped))
            except Exception:
                pass
        return stripped

    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                for key in ("text", "transcript", "content"):
                    if isinstance(item.get(key), str):
                        parts.append(item[key])
        return "\n".join(p.strip() for p in parts if p and p.strip())

    if isinstance(content, dict):
        for key in ("text", "transcript", "result", "content"):
            if isinstance(content.get(key), str):
                return content[key].strip()
        segs = content.get("segments")
        if isinstance(segs, list):
            return "\n".join(str(s.get("text", "")).strip() for s in segs if isinstance(s, dict) and s.get("text"))

    return json.dumps(content, ensure_ascii=False, indent=2)


def find_segments(obj: Any) -> list[dict[str, Any]]:
    """Best-effort segment extraction."""
    candidates = [obj]
    try:
        candidates.append(obj["choices"][0]["message"]["content"])
    except Exception:
        pass

    out: list[dict[str, Any]] = []
    for c in candidates:
        if isinstance(c, str):
            try:
                c = json.loads(c)
            except Exception:
                continue
        if isinstance(c, dict):
            segs = c.get("segments") or c.get("asr_segments")
            if isinstance(segs, list):
                out = [s for s in segs if isinstance(s, dict)]
                break
    return out


def write_srt(segments: list[dict[str, Any]], path: Path) -> Path | None:
    """Render ASR segments as SRT; returns None when nothing renderable."""
    lines: list[str] = []
    for i, s in enumerate(segments, 1):
        start = s.get("start", s.get("start_time", 0))
        end = s.get("end", s.get("end_time", start))
        seg_text = str(s.get("text", s.get("transcript", ""))).strip()
        if seg_text:
            lines += [str(i), f"{fmt_ts(start)} --> {fmt_ts(end)}", seg_text, ""]
    if not lines:
        return None
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def is_no_speech(text: str) -> bool:
    """A transcript with no spoken content: empty once punctuation/whitespace
    is stripped, or nothing but filler interjections (嗯/啊/…). Pure-BGM videos
    land here and must never be retried."""
    core = re.sub(r"[\s\W_]+", "", text)
    if not core:
        return True
    return all(ch in _FILLER_CHARS for ch in core)


class MimoTranscriber:
    """Transcriber adapter over the Xiaomi Mimo chat-completions ASR API.

    Raises RuntimeError on HTTP/parse failure — the caller records
    status='error' and moves on (transcription is non-fatal, ADR 0001).
    """

    def __init__(self, api_key: str, model: str = DEFAULT_MODEL, api_url: str = API_URL, language: str = "auto", timeout: float = 180) -> None:
        if not api_key:
            raise ValueError("MimoTranscriber requires an API key (MIMO_API_KEY)")
        self.api_key = api_key
        self.model = model
        self.api_url = api_url
        self.language = language
        self.timeout = timeout

    # -- Transcriber protocol ----------------------------------------------------

    def transcribe(self, audio_path: str, out_prefix: str | None = None) -> dict:
        audio = Path(audio_path)
        prefix = Path(out_prefix) if out_prefix else audio.with_suffix(".mimo")
        prefix.parent.mkdir(parents=True, exist_ok=True)

        upload_audio, mime = self._uploadable_audio(audio, prefix)
        raw = self._post(upload_audio, mime)

        raw_path = Path(f"{prefix}.response.json")
        raw_path.write_text(raw, encoding="utf-8")
        obj = json.loads(raw)

        text = pick_text(obj)
        srt_path = write_srt(find_segments(obj), Path(f"{prefix}.srt"))

        status = "no_speech" if is_no_speech(text) else "ok"
        return {
            "status": status,
            "text": text,
            "model": self.model,
            "srt_path": str(srt_path) if srt_path else None,
            "raw_response_path": str(raw_path),
        }

    # -- internals --------------------------------------------------------------

    def _uploadable_audio(self, audio: Path, prefix: Path) -> tuple[Path, str]:
        """Mimo rejects m4a/mp4 containers; convert anything unsupported to MP3."""
        mime = mimetypes.guess_type(audio.name)[0] or "audio/mp4"
        if mime in ALLOWED_MIMES:
            return audio, mime
        converted = Path(f"{prefix}.upload.mp3")
        subprocess.run(
            [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-i", str(audio), "-vn", "-acodec", "libmp3lame",
                "-ar", "16000", "-ac", "1", "-b:a", "64k", str(converted),
            ],
            check=True,
        )
        return converted, "audio/mpeg"

    def _post(self, upload_audio: Path, mime: str) -> str:
        b64 = base64.b64encode(upload_audio.read_bytes()).decode("ascii")
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "input_audio", "input_audio": {"data": f"data:{mime};base64,{b64}"}},
                    ],
                }
            ],
            "asr_options": {"language": self.language},
        }
        req = urllib.request.Request(
            self.api_url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"api-key": self.api_key, "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"mimo ASR HTTP {e.code}: {body[:500]}") from e
