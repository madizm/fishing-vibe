"""The intake module's interface: collect_video / collect_keyword / extract.

All orchestration lives here. I/O only happens through the injected seams
(Browser, Searcher, Llm, Geocoder, SpotStore) — tests cross the same interface
with fakes. IntakeReport doubles as the CLI's output source and the test
assertion surface.
"""

from __future__ import annotations

import json
import random
import re
import sys
import time
from dataclasses import dataclass, field

from spot_intake.extract import (
    aggregate_comment_keywords,
    aggregate_quality_scores,
    classify_place_name,
    clean_text_for_llm,
    dedupe_places,
    extract_comment_spot_clues_from_comments,
    extract_fish_species,
    normalize_fish_species,
    refine_precision,
)
from spot_intake.ports import Browser, Geocoder, Llm, Searcher, SpotStore, Transcriber, VideoUnavailable
from spot_intake.vocabulary import PLACE_PATTERNS


@dataclass(frozen=True)
class IntakeOptions:
    city: str = "武汉"
    keyword: str = "武汉钓鱼"
    max_video_places: int = 3  # 0 means all
    include_comments: bool = True
    include_transcript: bool = True
    downloads_dir: str = "downloads"
    comment_scrolls: int = 0
    comment_wait: float = 2.0
    comment_max: int = 100
    comment_quality_group_size: int = 5
    comment_keyword_group_size: int = 20
    delay_min: float = 8.0
    delay_max: float = 20.0

    def validate(self) -> None:
        if self.delay_min < 0 or self.delay_max < self.delay_min:
            raise ValueError("delay_max must be >= delay_min and delays must be non-negative")
        if self.max_video_places < 0:
            raise ValueError("max_video_places must be >= 0")
        if self.comment_max < 0:
            raise ValueError("comment_max must be >= 0")
        if self.comment_quality_group_size <= 0:
            raise ValueError("comment_quality_group_size must be > 0")
        if self.comment_keyword_group_size <= 0:
            raise ValueError("comment_keyword_group_size must be > 0")


@dataclass
class IntakeReport:
    """What one collect_video call did. Test assertion surface and CLI output source."""

    video: dict
    skipped: str | None = None  # e.g. "already_exists"
    spots_written: int = 0
    spot_names: list[str] = field(default_factory=list)
    comments_stored: int = 0
    keywords_stored: int = 0
    quality_applied: bool = False
    spots: list[dict] = field(default_factory=list)  # full spot records, for CLI output
    comment_result: dict | None = None  # analysis summary, for CLI output
    transcript_status: str | None = None  # "ok" | "no_speech" | "error" | "unavailable" | None (not attempted)


# ---------------------------------------------------------------------------
# Extraction orchestration (pure w.r.t. the Llm seam)
# ---------------------------------------------------------------------------

def parse_video(search_item: dict, extracted: dict, city: str, llm: Llm) -> dict:
    content = str(extracted.get("content", "") or "")
    title = str(extracted.get("title", "") or "")
    if title.endswith(" - 抖音"):
        title = title[:-5]
    title = title or search_item.get("desc", "")
    m = re.search(r"发布时间：([0-9]{4}-[0-9]{2}-[0-9]{2}\s+[0-9]{2}:[0-9]{2})", content)
    publish_time = m.group(1) if m else ""
    cleaned_content = clean_text_for_llm(content)
    haystack = f"{title}\n{search_item.get('desc','')}\n{cleaned_content[:5000]}"
    candidates = llm.extract_places(haystack, city)
    fallback_candidates = [place for place in PLACE_PATTERNS if place in haystack]
    candidates = dedupe_places([*candidates, *fallback_candidates])
    rule_fish_species = extract_fish_species(haystack)
    llm_fish_species = llm.extract_fish_species(haystack)
    fish_species = normalize_fish_species([*rule_fish_species, *llm_fish_species])
    fish_sources = []
    if rule_fish_species:
        fish_sources.append("rule:FISH_PATTERNS")
    if llm_fish_species:
        fish_sources.append("llm:title+desc+page_text")
    fish_source = "+".join(fish_sources)
    fish_confidence = 0.95 if llm_fish_species else (0.85 if rule_fish_species else 0.0)
    return {
        "title": title,
        "author": search_item.get("author", ""),
        "url": search_item.get("url", ""),
        "publish_time": publish_time,
        "raw_text": cleaned_content[:2000],
        "place_candidates": candidates,
        "fish_species": fish_species,
        "fish_species_source": fish_source,
        "fish_confidence": fish_confidence,
    }


def analyze_comments(
    comments: list[dict],
    city: str,
    llm: Llm,
    quality_group_size: int = 5,
    keyword_group_size: int = 20,
) -> dict:
    """Analyze already extracted comments without browser/DB side effects."""
    rule_place_clues = extract_comment_spot_clues_from_comments(comments, city)
    llm_place_clues = llm.extract_comment_places(comments, city)
    llm_keywords = llm.extract_comment_keywords(comments, city, group_size=keyword_group_size)
    quality_groups = llm.score_comment_quality(comments, group_size=quality_group_size)
    quality = aggregate_quality_scores(quality_groups)
    return {
        "comment_count": len(comments),
        "rule_place_clues": rule_place_clues,
        "llm_place_clues": llm_place_clues,
        "llm_keywords": llm_keywords,
        "keyword_summary": aggregate_comment_keywords(llm_keywords),
        "quality_groups": quality_groups,
        "quality": quality,
    }


def build_extraction_report(
    search_item: dict,
    extracted: dict,
    city: str,
    llm: Llm,
    comments: list[dict] | None = None,
    comment_quality_group_size: int = 5,
    comment_keyword_group_size: int = 20,
) -> dict:
    """Deterministic extraction report for tests and dry runs: parsing only,
    no SQLite writes and no geocoding."""
    video = parse_video(search_item, extracted, city, llm)
    report: dict = {"video": video}
    if comments is not None:
        report["comments"] = analyze_comments(
            comments,
            city,
            llm,
            quality_group_size=comment_quality_group_size,
            keyword_group_size=comment_keyword_group_size,
        )
    return report


# ---------------------------------------------------------------------------
# The Intake orchestrator
# ---------------------------------------------------------------------------

class Intake:
    """Deep module: the whole collection pipeline behind collect_video /
    collect_keyword / extract, with I/O at four injected seams."""

    def __init__(
        self,
        *,
        browser: Browser,
        llm: Llm,
        geocoder: Geocoder,
        store: SpotStore,
        searcher: Searcher | None = None,
        transcriber: Transcriber | None = None,
        options: IntakeOptions | None = None,
    ) -> None:
        self.browser = browser
        self.llm = llm
        self.geocoder = geocoder
        self.store = store
        self.searcher = searcher
        self.transcriber = transcriber
        self.options = options or IntakeOptions()
        self.options.validate()

    # -- public interface -----------------------------------------------------

    def collect_video(self, url: str, search_item: dict | None = None, keyword: str | None = None) -> IntakeReport:
        opts = self.options
        keyword = keyword or opts.keyword
        item = search_item or {"url": url, "desc": "", "author": ""}
        already_exists = self.store.video_exists(url)
        if already_exists and not opts.include_comments:
            print(f"[skip] already in db: {url}", file=sys.stderr, flush=True)
            return IntakeReport(video={"url": url, "title": "", "fish_species": []}, skipped="already_exists")
        if already_exists:
            print(f"[info] already in db, refreshing comments only: {url}", file=sys.stderr, flush=True)

        extracted = self.browser.extract_video(url)
        video = parse_video(item, extracted, opts.city, self.llm)
        video_id = self.store.upsert_video(keyword, video)
        if not video.get("title"):
            meta = self.store.video_metadata(video_id)
            if meta:
                video["title"] = meta.get("title") or video.get("title", "")
                video["author"] = meta.get("author") or video.get("author", "")
                video["publish_time"] = meta.get("publish_time") or video.get("publish_time", "")
                video["raw_text"] = meta.get("raw_text") or video.get("raw_text", "")

        report = IntakeReport(video=video)
        inserted_places: set[str] = self.store.existing_spot_names(video_id)
        if not already_exists:
            video_places = video["place_candidates"] if opts.max_video_places == 0 else video["place_candidates"][: opts.max_video_places]
            for place in video_places:
                precision = classify_place_name(place)
                if precision == "reject":
                    print(f"[info] reject non-spot place candidate {place!r} ({url})", file=sys.stderr, flush=True)
                    continue
                try:
                    geo = self.geocoder.geocode(place, opts.city)
                except Exception as exc:
                    print(f"[warn] geocode failed for video-text place {place!r} ({url}): {exc}", file=sys.stderr, flush=True)
                    continue
                if not geo:
                    continue
                precision = refine_precision(precision, geo)
                if precision == "reject":
                    print(f"[info] reject admin-level geocode result for {place!r} ({url})", file=sys.stderr, flush=True)
                    continue
                spot = {
                    "place_name": place,
                    "confidence": 0.9 if geo["geocode_score"] >= 90 else 0.7,
                    "source_type": "video_text",
                    "source_text": video["raw_text"][:500],
                    "precision": precision,
                    **geo,
                }
                self.store.insert_record(keyword, video, spot)
                inserted_places.add(place)
                report.spots.append({"video": video["url"], "title": video["title"], "fish_species": video.get("fish_species", []), **spot})

        pending_comment_spots = None
        if opts.include_comments:
            pending_comment_spots = self._collect_comments(report, video_id, keyword)

        # Transcript stage: after video_text, before comment spots, so the
        # first-come-first-served dedupe order is video_text → transcript →
        # comments (ADR 0001). Skipped for already-collected videos — the
        # backfill path (collect_transcript) owns those.
        if opts.include_transcript and not already_exists:
            if self.transcriber is None:
                print(f"[warn] include_transcript is on but no Transcriber is wired; skipping transcription for {url}", file=sys.stderr, flush=True)
            else:
                report.transcript_status = self._transcribe_and_merge(video, video_id, inserted_places, keyword, report.spots)

        if pending_comment_spots:
            self._insert_comment_spots(report, video_id, inserted_places, keyword, pending_comment_spots)

        report.spots_written = len(report.spots)
        report.spot_names = [s["place_name"] for s in report.spots]
        return report

    def collect_transcript(self, video_id: int, url: str, keyword: str | None = None) -> dict:
        """Backfill path: transcribe one already-collected video and merge
        transcript findings (spots/fish) into its records."""
        if self.transcriber is None:
            raise RuntimeError("collect_transcript requires a Transcriber at the transcription seam")
        opts = self.options
        video = {
            "url": url,
            "title": "",
            "raw_text": "",
            "fish_species": [],
            "fish_species_source": "",
            "fish_confidence": 0.0,
            **self.store.video_metadata(video_id),
        }
        inserted_places = self.store.existing_spot_names(video_id)
        spots: list[dict] = []
        status = self._transcribe_and_merge(video, video_id, inserted_places, keyword or opts.keyword, spots)
        return {"video_id": video_id, "url": url, "status": status, "spots_added": spots}

    def collect_keyword(self, keyword: str, limit: int) -> list[IntakeReport]:
        if self.searcher is None:
            raise RuntimeError("collect_keyword requires a Searcher at the search seam")
        items = self.searcher.search(keyword, limit)
        reports: list[IntakeReport] = []
        for index, item in enumerate(items):
            url = item.get("url", "")
            if not url:
                print(f"[skip] missing url for item index={index}", file=sys.stderr, flush=True)
                self._throttle(index, len(items))
                continue
            reports.append(self.collect_video(url, search_item=item))
            self._throttle(index, len(items))
        return reports

    def extract(
        self,
        url: str = "",
        *,
        search_item: dict | None = None,
        extracted: dict | None = None,
        comments: list[dict] | None = None,
    ) -> dict:
        """Debug/dry-run path: parse only, no DB writes and no geocoding.
        Pass `extracted`/`comments` fixtures to run fully offline. When no
        extracted fixture is given, the page is read live through the browser
        seam; live comments are read only when no fixture of either kind was
        given (mirrors the legacy --extract-only flag semantics).
        """
        opts = self.options
        item = search_item or {"url": url, "desc": "", "author": ""}
        if url and not item.get("url"):
            item["url"] = url
        extracted_from_fixture = extracted is not None
        if extracted is None:
            if not url:
                raise ValueError("extract requires a url unless an extracted fixture is provided")
            extracted = self.browser.extract_video(url)
        if comments is None and opts.include_comments and not extracted_from_fixture and url:
            comments = self.browser.extract_video_comments(
                scrolls=opts.comment_scrolls,
                wait_seconds=opts.comment_wait,
                max_comments=opts.comment_max,
            )
        return build_extraction_report(
            item,
            extracted,
            opts.city,
            self.llm,
            comments=comments,
            comment_quality_group_size=opts.comment_quality_group_size,
            comment_keyword_group_size=opts.comment_keyword_group_size,
        )

    # -- internals --------------------------------------------------------------

    def _transcribe_and_merge(self, video: dict, video_id: int, inserted_places: set[str], keyword: str, spots_out: list[dict]) -> str:
        """Download → transcribe → LLM extract → merge spots/fish. Returns the
        transcript status. Non-fatal by design (ADR 0001): any acquisition/ASR
        failure lands in video_transcripts.status='error' and the rest of the
        pipeline proceeds on the remaining text sources."""
        opts = self.options
        url = video["url"]
        try:
            media = self.browser.download_audio(url, opts.downloads_dir)
            result = self.transcriber.transcribe(media["audio_path"])
        except VideoUnavailable as exc:
            # Terminal (deleted/private): never retried, like no_speech.
            print(f"[info] video unavailable, marking terminal: {url}: {exc}", file=sys.stderr, flush=True)
            self.store.upsert_transcript(video_id, {
                "status": "unavailable", "error": str(exc)[:500], "transcript_text": "",
                "audio_path": "", "srt_path": "", "model": "",
                "raw_response_path": "", "summary": "", "extras_json": "",
            })
            return "unavailable"
        except Exception as exc:
            print(f"[warn] transcription failed for {url}: {exc}", file=sys.stderr, flush=True)
            self.store.upsert_transcript(video_id, {
                "status": "error", "error": str(exc)[:500], "transcript_text": "",
                "audio_path": "", "srt_path": "", "model": "",
                "raw_response_path": "", "summary": "", "extras_json": "",
            })
            return "error"

        text = str(result.get("text", "") or "").strip()
        row = {
            "status": result.get("status", "ok"),
            "transcript_text": text,
            "audio_path": str(media.get("audio_path", "")),
            "srt_path": str(result.get("srt_path") or ""),
            "model": str(result.get("model") or ""),
            "error": "",
            "raw_response_path": str(result.get("raw_response_path") or ""),
            "summary": "",
            "extras_json": "",
        }
        if row["status"] == "no_speech" or not text:
            row["status"] = "no_speech"
            self.store.upsert_transcript(video_id, row)
            return "no_speech"

        try:
            places = dedupe_places(self.llm.extract_transcript_places(text, opts.city))
            fish = normalize_fish_species(self.llm.extract_transcript_fish_species(text))
            insights = self.llm.summarize_transcript(text) or {}
        except Exception as exc:
            # The transcript text itself is safely stored below; extraction can
            # be retried later without re-downloading or re-transcribing.
            print(f"[warn] transcript LLM analysis failed for {url}: {exc}", file=sys.stderr, flush=True)
            places, fish, insights = [], [], {}
        row["summary"] = str(insights.get("summary", "") or "")
        extras = insights.get("extras")
        row["extras_json"] = json.dumps(extras, ensure_ascii=False) if extras else ""
        self.store.upsert_transcript(video_id, row)

        if fish:
            video["fish_species"] = normalize_fish_species([*video.get("fish_species", []), *fish])
            source = video.get("fish_species_source", "")
            video["fish_species_source"] = f"{source}+llm:transcript" if source else "llm:transcript"
            video["fish_confidence"] = max(float(video.get("fish_confidence", 0.0) or 0.0), 0.95)

        added = self._insert_transcript_spots(video, video_id, inserted_places, keyword, places, text[:500])
        spots_out.extend(added)
        return "ok"

    def _insert_transcript_spots(self, video: dict, video_id: int, inserted_places: set[str], keyword: str, places: list[str], source_text: str) -> list[dict]:
        """Geocode transcript place candidates and insert the ones not already
        recorded (per-source cap, first-come-first-served dedupe, ADR 0001).
        Single insertion path shared by collect and re-extract."""
        opts = self.options
        url = video["url"]
        candidates = places if opts.max_video_places == 0 else places[: opts.max_video_places]
        added: list[dict] = []
        for place in candidates:
            if place in inserted_places:
                continue
            precision = classify_place_name(place)
            if precision == "reject":
                print(f"[info] reject non-spot place candidate {place!r} ({url})", file=sys.stderr, flush=True)
                continue
            try:
                geo = self.geocoder.geocode(place, opts.city)
            except Exception as exc:
                print(f"[warn] geocode failed for transcript place {place!r} ({url}): {exc}", file=sys.stderr, flush=True)
                continue
            if not geo:
                continue
            precision = refine_precision(precision, geo)
            if precision == "reject":
                print(f"[info] reject admin-level geocode result for {place!r} ({url})", file=sys.stderr, flush=True)
                continue
            spot = {
                "place_name": place,
                "confidence": 0.9 if geo["geocode_score"] >= 90 else 0.7,
                "source_type": "transcript",
                "source_text": source_text,
                "precision": precision,
                **geo,
            }
            self.store.insert_record(keyword, video, spot)
            inserted_places.add(place)
            added.append({"video": url, "title": video.get("title", ""), "fish_species": video.get("fish_species", []), **spot})
        return added

    def reextract_transcript_spots(self, video_id: int) -> dict:
        """Re-run place extraction from the STORED transcript text (no download,
        no ASR) and append spots not already recorded for this video. Used to
        re-harvest transcripts after prompt/geocoder improvements."""
        opts = self.options
        row = self.store.transcript_for_video(video_id)
        text = str((row or {}).get("transcript_text") or "").strip()
        if not row or row.get("status") != "ok" or not text:
            return {"video_id": video_id, "skipped": "no_usable_transcript", "candidates": [], "spots_added": []}
        video = {
            "url": "",
            "title": "",
            "raw_text": "",
            "fish_species": [],
            "fish_species_source": "",
            "fish_confidence": 0.0,
            **self.store.video_metadata(video_id),
        }
        keyword = video.get("keyword") or opts.keyword
        places = dedupe_places(self.llm.extract_transcript_places(text, opts.city))
        inserted_places = self.store.existing_spot_names(video_id)
        added = self._insert_transcript_spots(video, video_id, inserted_places, keyword, places, text[:500])
        return {"video_id": video_id, "skipped": None, "candidates": places, "spots_added": added}

    def _collect_comments(self, report: IntakeReport, video_id: int, keyword: str) -> dict | None:
        """Read/analyze/store comments. Spot insertion is deferred (returned as
        pending inputs) so transcript spots can claim shared place names first."""
        opts = self.options
        video = report.video
        url = video["url"]
        try:
            comments = self.browser.extract_video_comments(
                scrolls=opts.comment_scrolls,
                wait_seconds=opts.comment_wait,
                max_comments=opts.comment_max,
            )
            comments = self.store.insert_video_comments(video_id, comments)
            analysis = analyze_comments(
                comments,
                opts.city,
                self.llm,
                quality_group_size=opts.comment_quality_group_size,
                keyword_group_size=opts.comment_keyword_group_size,
            )
            rule_comment_clues = analysis["rule_place_clues"]
            comment_clues = analysis["llm_place_clues"]
            comment_keywords = analysis["llm_keywords"]
            quality_groups = analysis["quality_groups"]
            video_quality = analysis["quality"]
            comment_keywords = self.store.insert_comment_keywords(video_id, comment_keywords)
            comment_keyword_summary = aggregate_comment_keywords(comment_keywords)
            self.store.apply_comment_quality_to_spots(video_id, video_quality)
        except Exception as exc:
            print(f"[warn] comment extraction/LLM analysis failed for {url}: {exc}", file=sys.stderr, flush=True)
            comments = []
            rule_comment_clues = []
            comment_clues = []
            comment_keywords = []
            comment_keyword_summary = []
            quality_groups = []
            video_quality = {"quality_score": None, "confidence": 0.0, "detail": ""}

        report.comments_stored = len(comments)
        report.keywords_stored = len(comment_keywords)
        report.quality_applied = video_quality.get("quality_score") is not None
        report.comment_result = {
            "video": video["url"],
            "title": video["title"],
            "saved_comments": len(comments),
            "rule_comment_place_clues": rule_comment_clues,
            "comment_place_clues": comment_clues,
            "comment_keywords": comment_keywords,
            "comment_keyword_summary": comment_keyword_summary,
            "spot_quality_score": video_quality.get("quality_score"),
            "spot_quality_detail": video_quality.get("detail", ""),
            "comment_quality_groups": quality_groups,
        }
        return {"comment_clues": comment_clues, "quality_groups": quality_groups, "video_quality": video_quality}

    def _insert_comment_spots(self, report: IntakeReport, video_id: int, inserted_places: set[str], keyword: str, pending: dict) -> None:
        opts = self.options
        video = report.video
        quality_groups = pending["quality_groups"]
        video_quality = pending["video_quality"]
        for clue in pending["comment_clues"]:
            place = clue.get("place_name", "")
            if not place or place in inserted_places:
                continue
            precision = classify_place_name(place)
            if precision == "reject":
                print(f"[info] reject non-spot comment place {place!r} ({video['url']})", file=sys.stderr, flush=True)
                continue
            try:
                geo = self.geocoder.geocode(place, opts.city)
            except Exception as exc:
                print(f"[warn] geocode failed for comment place {place!r} ({video['url']}): {exc}", file=sys.stderr, flush=True)
                continue
            if not geo or geo["geocode_score"] < 80:
                continue
            precision = refine_precision(precision, geo)
            if precision == "reject":
                print(f"[info] reject admin-level geocode result for {place!r} ({video['url']})", file=sys.stderr, flush=True)
                continue
            source_text = str(clue.get("evidence") or "").strip()
            if clue.get("comment_ids"):
                source_text = f"{source_text}（comment_ids={json.dumps(clue['comment_ids'], ensure_ascii=False)}）"
            place_quality = aggregate_quality_scores(quality_groups, clue.get("comment_ids") or [])
            if place_quality.get("quality_score") is None:
                place_quality = video_quality
            spot = {
                "place_name": place,
                "confidence": 0.65 if geo["geocode_score"] >= 90 else 0.5,
                "source_type": "comment_llm",
                "source_text": source_text,
                "quality_score": place_quality.get("quality_score"),
                "quality_score_source": "comment_llm" if place_quality.get("quality_score") is not None else "",
                "quality_score_detail": place_quality.get("detail", ""),
                "precision": precision,
                **geo,
            }
            self.store.insert_record(keyword, video, spot)
            inserted_places.add(place)
            report.spots.append({"video": video["url"], "title": video["title"], "fish_species": video.get("fish_species", []), **spot})

    def _throttle(self, index: int, total: int) -> None:
        if index >= total - 1:
            return
        delay = random.uniform(self.options.delay_min, self.options.delay_max)
        print(f"[throttle] sleep {delay:.1f}s before next video...", flush=True)
        time.sleep(delay)
