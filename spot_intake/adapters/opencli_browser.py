"""Browser + search adapter backed by the `opencli` CLI (Douyin).

The adapter owns the opencli session lifecycle: use it as a context manager
(`with OpencliBrowser("session-name") as browser:`) so the browser session is
closed on exit. Session names never cross the interface.
"""

from __future__ import annotations

import json
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path

from spot_intake.extract import parse_douyin_comment_time
from spot_intake.proc import run

# Focused DOM extract of only the current video's info card. A whole-page
# markdown extract pulls text from <div data-e2e="related-video"> and the
# auto-next overlay, which misattributes recommended videos to the current one.
_VIDEO_INFO_JS = r"""(() => {
  const normalize = (s) => String(s || '')
    .replace(/[\u200b\ufeff]/g, '')
    .replace(/\s+/g, ' ')
    .trim();
  const lines = [];
  const add = (s) => {
    s = normalize(s);
    if (!s || lines.includes(s)) return;
    if (/^\d+$/.test(s) || ['举报', '分享', '回复', '展开'].includes(s)) return;
    lines.push(s);
  };

  const declarations = Array.from(document.querySelectorAll('[data-e2e="video-detail"] *'))
    .map((el) => normalize(el.innerText || el.textContent || ''))
    .filter((text) => /^作者声明[:：]/.test(text))
    .sort((a, b) => a.length - b.length);
  add(declarations[0] || '');

  const info = document.querySelector('[data-e2e="detail-video-info"]');
  if (info) {
    String(info.innerText || info.textContent || '')
      .split(/\n+/)
      .forEach(add);
  }

  return { title: document.title || '', content: lines.join('\n') };
})()"""

# Anchors on comment metadata time spans (e.g. "6小时前·湖北") because the
# Douyin DOM uses generated class names; parses the nearest comment block.
_COMMENTS_JS = r"""(() => {
  const maxComments = __MAX_COMMENTS__;
  const timeRe = /^(?:刚刚|\d+分钟前|\d+小时前|\d+天前|\d+周前|\d+月前|\d+年前|昨天|今天|\d{1,2}-\d{1,2}|\d{4}-\d{1,2}-\d{1,2})(?:\s+\d{1,2}:\d{2})?(?:·[^\n]+)?$/;
  const noise = new Set(['...', '作者赞过', '置顶', '分享', '回复']);
  const normalize = (s) => String(s || '').replace(/[\u200b\ufeff]/g, '').replace(/\s+/g, ' ').trim();
  const parseTime = (value) => {
    const text = normalize(value);
    const m = text.match(/^(.*?)(?:·(.+))?$/);
    return { raw: text, time: normalize(m && m[1] || text), ip: normalize(m && m[2] || '') };
  };
  const spans = Array.from(document.querySelectorAll('span'))
    .filter((span) => timeRe.test(normalize(span.innerText || span.textContent || '')));
  const records = [];
  const seen = new Set();
  for (const span of spans) {
    const block = span.parentElement && span.parentElement.parentElement;
    if (!block) continue;
    const lines = (block.innerText || '')
      .split(/\n+/)
      .map(normalize)
      .filter(Boolean);
    const timeText = normalize(span.innerText || span.textContent || '');
    const timeIndex = lines.findIndex((line) => line === timeText);
    if (timeIndex <= 0) continue;
    let before = lines.slice(0, timeIndex).filter((line) => !noise.has(line));
    const isAuthor = before.includes('作者');
    before = before.filter((line) => line !== '作者');
    const author = before.shift() || '';
    const text = normalize(before.join(''));
    if (!author || !text || text.length > 500) continue;
    const parsedTime = parseTime(timeText);
    const key = author + '|' + text + '|' + parsedTime.raw;
    if (seen.has(key)) continue;
    seen.add(key);
    records.push({
      author,
      text,
      comment_time: parsedTime.time,
      comment_time_raw: parsedTime.raw,
      ip_location: parsedTime.ip,
      is_author: isAuthor,
    });
    if (records.length >= maxComments) break;
  }
  return records;
})()"""


_MEDIA_URL_JS = "(() => document.querySelector('video source')?.src || document.querySelector('video')?.currentSrc || document.querySelector('video')?.src || '')()"


def _one_line(js: str) -> str:
    # opencli browser eval is more reliable with one-line expressions.
    return " ".join(line.strip() for line in js.splitlines())


class OpencliBrowser:
    """Browser + Searcher adapter via `opencli browser` / `opencli douyin`."""

    def __init__(self, session: str = "douyin-fishing-batch", cwd: Path | None = None) -> None:
        self.session = session
        self.cwd = cwd

    def __enter__(self) -> "OpencliBrowser":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        """Best-effort session cleanup; opencli sessions are named and reusable,
        so a missing close subcommand is not an error."""
        try:
            run(["opencli", "browser", self.session, "close"], timeout=15, cwd=self.cwd)
        except Exception:
            pass

    # -- raw page operations -------------------------------------------------

    def open(self, url: str) -> None:
        run(["opencli", "browser", self.session, "open", url], timeout=120, cwd=self.cwd)

    def eval(self, js: str) -> object:
        out = run(["opencli", "browser", self.session, "eval", js], timeout=90, cwd=self.cwd)
        return json.loads(out)

    def scroll_down(self, amount: int = 1200) -> None:
        run(["opencli", "browser", self.session, "scroll", "down", "--amount", str(amount)], timeout=60, cwd=self.cwd)

    # -- Browser protocol ------------------------------------------------------

    def extract_video(self, url: str) -> dict:
        self.open(url)
        try:
            focused = self.eval(_one_line(_VIDEO_INFO_JS))
            if isinstance(focused, dict) and str(focused.get("content", "")).strip():
                return focused
        except Exception:
            pass
        # Fallback for DOM changes: scope extraction to the info card when possible.
        try:
            out = run(
                ["opencli", "browser", self.session, "extract", "--selector", '[data-e2e="detail-video-info"]', "--chunk-size", "10000"],
                timeout=120,
                cwd=self.cwd,
            )
        except Exception:
            out = run(["opencli", "browser", self.session, "extract", "--chunk-size", "10000"], timeout=120, cwd=self.cwd)
        return json.loads(out)

    def extract_video_comments(self, scrolls: int = 0, wait_seconds: float = 2.0, max_comments: int = 100) -> list[dict]:
        if max_comments < 0:
            raise ValueError("max_comments must be >= 0")
        if max_comments == 0:
            return []
        for _ in range(scrolls):
            self.scroll_down()
            if wait_seconds:
                time.sleep(wait_seconds)
        js = _one_line(_COMMENTS_JS.replace("__MAX_COMMENTS__", str(max_comments)))
        data = self.eval(js)
        comments = data if isinstance(data, list) else []
        normalized: list[dict] = []
        parsed_at = datetime.now()
        for item in comments:
            if not isinstance(item, dict):
                continue
            text = str(item.get("text", "")).strip()
            if not text:
                continue
            comment_time_raw = str(item.get("comment_time_raw", "")).strip()
            comment_time = str(item.get("comment_time", "")).strip()
            normalized.append({
                "author": str(item.get("author", "")).strip(),
                "text": text,
                "comment_time": comment_time,
                "comment_time_raw": comment_time_raw,
                "comment_time_standard": parse_douyin_comment_time(comment_time_raw or comment_time, now=parsed_at),
                "ip_location": str(item.get("ip_location", "")).strip(),
                "is_author": bool(item.get("is_author", False)),
            })
        return normalized

    def download_audio(self, url: str, out_dir: str) -> dict:
        """Extract the current playable media URL from the video page, download
        the MP4, and keep only the losslessly-demuxed .m4a. Signed play URLs
        expire quickly, so the page is (re)opened on every call."""
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        m = re.search(r"/video/(\d+)", url)
        vid = m.group(1) if m else "douyin-video"

        self.open(url)
        run(["opencli", "browser", self.session, "wait", "time", "5"], timeout=60, cwd=self.cwd)
        raw = run(["opencli", "browser", self.session, "eval", _MEDIA_URL_JS], timeout=90, cwd=self.cwd).strip()
        media_url = ""
        if raw:
            first = raw.splitlines()[0].strip()
            try:
                parsed = json.loads(first)
                media_url = parsed if isinstance(parsed, str) else ""
            except Exception:
                media_url = first
        candidates = [] if not media_url or media_url.startswith("blob:") else [media_url]
        if not candidates:
            # MSE playback: the <video> src is a blob handle, not fetchable.
            # Capture the player's own media requests off the network instead.
            candidates = self._capture_media_urls(url)
        if not candidates:
            raise RuntimeError(f"could not extract a playable media URL from {url}")

        m4a = out / f"{vid}.m4a"
        source = self._download_with_audio(candidates, url, out, vid)
        try:
            run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(source), "-vn", "-c:a", "copy", str(m4a)], timeout=300, cwd=self.cwd)
        finally:
            source.unlink(missing_ok=True)  # only the audio track is kept (ADR 0001)
        return {"audio_path": str(m4a), "video_id": vid}

    def _download_with_audio(self, candidates: list[str], referer: str, out: Path, vid: str) -> Path:
        """Download candidates until one actually contains an audio stream.

        Douyin MSE playback uses split streams: one URL is video-only, another
        audio-only (the ideal transcription source). Content-Type cannot tell
        them apart (both video/mp4), so we ffprobe.
        """
        for index, media_url in enumerate(candidates[:4]):
            tmp = out / f"{vid}.candidate{index}"
            probe = ""
            try:
                run(["curl", "-L", "--fail", "--retry", "2", "-A", "Mozilla/5.0", "-e", referer, media_url, "-o", str(tmp)], timeout=600, cwd=self.cwd)
                probe = run(
                    ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=codec_name", "-of", "csv=p=0", str(tmp)],
                    timeout=60,
                    cwd=self.cwd,
                )
                if probe.strip():
                    return tmp
            finally:
                if tmp.exists() and not probe.strip():
                    tmp.unlink()
        raise RuntimeError(f"no candidate media URL had an audio stream ({len(candidates)} tried)")

    def _capture_media_urls(self, url: str, wait_seconds: float = 10.0) -> list[str]:
        """Reload the page under `browser network --follow --all` and return the
        deduped media response URLs the player fetches (signed, curl-able)."""
        proc = subprocess.Popen(
            ["opencli", "browser", self.session, "network", "--follow", "--all", "--since", "120s"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            cwd=self.cwd,
        )
        try:
            time.sleep(1)  # let the follower attach before the reload
            self.open(url)
            time.sleep(wait_seconds)
        finally:
            proc.terminate()
            try:
                out, _ = proc.communicate(timeout=10)
            except Exception:
                proc.kill()
                out, _ = proc.communicate()
        urls: list[str] = []
        for line in out.splitlines():
            try:
                entry = json.loads(line)
            except Exception:
                continue
            media_url = str(entry.get("url", ""))
            ct = str(entry.get("ct", ""))
            if (ct.startswith("video/") or "douyinvod" in media_url) and media_url not in urls:
                urls.append(media_url)
        return urls


class OpencliDouyinSearch:
    """Searcher adapter via `opencli douyin search`."""

    def __init__(self, cwd: Path | None = None) -> None:
        self.cwd = cwd

    def search(self, keyword: str, limit: int) -> list[dict]:
        out = run(["opencli", "douyin", "search", keyword, "--limit", str(limit), "-f", "json"], timeout=180, cwd=self.cwd)
        return json.loads(out)
