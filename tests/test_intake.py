"""Intake orchestration tests. Fakes sit at the four seams; no browser, no LLM,
no SQLite, no subprocess — the interface is the test surface."""

import json
from pathlib import Path

import pytest

from spot_intake import Intake, IntakeOptions
from spot_intake.adapters.llm_openai import NullLlm
from spot_intake.fixtures import load_comments_fixture

ROOT = Path(__file__).resolve().parents[1]

URL = "https://www.douyin.com/video/123"


class FakeBrowser:
    def __init__(self, video=None, comments=None):
        self.video = video or {"title": "东湖野钓 - 抖音", "content": "发布时间：2026-07-01 10:00\n东湖黄尾鲴开口了"}
        self.comments = comments if comments else [
            {"author": "老王", "text": "月湖钓点不错", "comment_time": "3天前", "comment_time_raw": "3天前·湖北",
             "comment_time_standard": "2026-07-15 10:00:00", "ip_location": "湖北", "is_author": False},
        ]
        self.opened_urls = []

    def extract_video(self, url):
        self.opened_urls.append(url)
        return dict(self.video)

    def extract_video_comments(self, scrolls=0, wait_seconds=2.0, max_comments=100):
        return [dict(c) for c in self.comments]


class FakeSearcher:
    def __init__(self, items):
        self.items = items
        self.calls = []

    def search(self, keyword, limit):
        self.calls.append((keyword, limit))
        return list(self.items)


class FakeLlm:
    def __init__(self, places=None, fish=None, comment_places=None, keywords=None, quality=None):
        self.places = places if places is not None else ["东湖"]
        self.fish = fish if fish is not None else ["黄尾鲴"]
        self.comment_places = comment_places if comment_places is not None else []
        self.keywords = keywords if keywords is not None else [
            {"comment_index": 1, "comment_id": 11, "keyword": "有口", "category": "fish_condition", "confidence": 0.8, "evidence": "有口"},
        ]
        self.quality = quality if quality is not None else [
            {"group_index": 1, "comment_ids": [11], "score_1_5": 4, "normalized_score": 0.75, "confidence": 0.8, "summary": "好", "evidence": ""},
        ]

    def extract_places(self, text, city):
        return list(self.places)

    def extract_fish_species(self, text):
        return list(self.fish)

    def extract_comment_places(self, comments, city):
        return [dict(c) for c in self.comment_places]

    def extract_comment_keywords(self, comments, city, group_size=20):
        return [dict(k) for k in self.keywords]

    def score_comment_quality(self, comments, group_size=5):
        return [dict(g) for g in self.quality]


class FakeGeocoder:
    def __init__(self, score=95):
        self.score = score
        self.calls = []

    def geocode(self, place, city):
        self.calls.append((place, city))
        return {
            "query_name": f"{city}{place}",
            "longitude": 114.0,
            "latitude": 30.0,
            "geocode_score": self.score,
            "geocode_level": "湖泊",
            "geocode_autocorrected": False,
            "geocode_original_query": f"{city}{place}",
            "geocode_corrected_query": "",
            "geocode_address": "addr",
            "geocode_area": "area",
        }


class FakeStore:
    """In-memory SpotStore recording write order."""

    def __init__(self, existing_urls=(), existing_spots=(), metadata=None):
        self.urls = set(existing_urls)
        self.spots_by_video = {1: set(existing_spots)}
        self.metadata = metadata or {}
        self.calls = []
        self._next_video_id = 1

    def video_exists(self, url):
        return url in self.urls

    def upsert_video(self, keyword, video):
        self.calls.append(("upsert_video", video["url"]))
        self.urls.add(video["url"])
        return 1

    def video_metadata(self, video_id):
        return dict(self.metadata)

    def existing_spot_names(self, video_id):
        return set(self.spots_by_video.get(video_id, set()))

    def insert_video_comments(self, video_id, comments):
        self.calls.append(("insert_video_comments", len(comments)))
        return [{**c, "comment_id": 10 + i} for i, c in enumerate(comments, start=1)]

    def insert_comment_keywords(self, video_id, keywords):
        self.calls.append(("insert_comment_keywords", len(keywords)))
        return list(keywords)

    def apply_comment_quality_to_spots(self, video_id, quality):
        self.calls.append(("apply_quality", quality.get("quality_score")))

    def insert_record(self, keyword, video, spot):
        self.calls.append(("insert_record", spot["place_name"], spot["source_type"]))
        self.spots_by_video.setdefault(1, set()).add(spot["place_name"])


def make_intake(*, browser=None, llm=None, geocoder=None, store=None, searcher=None, **option_overrides):
    overrides = {"delay_min": 0, "delay_max": 0, **option_overrides}
    options = IntakeOptions(**overrides)
    return Intake(
        browser=browser or FakeBrowser(),
        llm=llm if llm is not None else FakeLlm(),
        geocoder=geocoder or FakeGeocoder(),
        store=store or FakeStore(),
        searcher=searcher,
        options=options,
    )


# --- collect_video: fresh URL -------------------------------------------------

def test_collect_video_full_pipeline_writes_in_order():
    store = FakeStore()
    geocoder = FakeGeocoder()
    intake = make_intake(store=store, geocoder=geocoder, llm=FakeLlm(comment_places=[
        {"place_name": "月湖", "comment_indexes": [1], "comment_ids": [11], "evidence": "月湖钓点不错", "confidence": 0.8},
    ]))
    report = intake.collect_video(URL)

    assert report.skipped is None
    # video_text spot from the LLM place candidate, comment_llm spot from the comment clue
    assert ("insert_record", "东湖", "video_text") in store.calls
    assert ("insert_record", "月湖", "comment_llm") in store.calls
    assert report.spots_written == 2
    assert report.spot_names == ["东湖", "月湖"]
    assert report.comments_stored == 1
    assert report.keywords_stored == 1
    assert report.quality_applied is True
    # quality write happens after keywords insert
    kinds = [c[0] for c in store.calls]
    assert kinds.index("apply_quality") > kinds.index("insert_comment_keywords")
    assert report.comment_result["spot_quality_score"] == pytest.approx(0.75)


def test_collect_video_without_llm_uses_rule_fallbacks_only():
    store = FakeStore()
    intake = make_intake(store=store, llm=NullLlm())
    report = intake.collect_video(URL)
    # NullLlm finds no places/fish; rule fallback finds 黄尾鲴 in the page text
    assert report.video["fish_species"] == ["黄尾鲴"]
    assert report.video["fish_species_source"] == "rule:FISH_PATTERNS"
    # no LLM places -> nothing geocoded -> no spots; no quality applied
    assert report.spots_written == 0
    assert report.quality_applied is False
    assert ("apply_quality", None) in store.calls


def test_collect_video_skips_existing_url_when_comments_disabled():
    store = FakeStore(existing_urls=(URL,))
    intake = make_intake(store=store, include_comments=False)
    report = intake.collect_video(URL)
    assert report.skipped == "already_exists"
    assert store.calls == []  # no writes at all


def test_collect_video_existing_url_refreshes_comments_only():
    store = FakeStore(existing_urls=(URL,), existing_spots=("东湖",))
    geocoder = FakeGeocoder()
    intake = make_intake(store=store, geocoder=geocoder)
    report = intake.collect_video(URL)
    # video_text spots are not re-inserted, but comments are refreshed
    assert not any(c[0] == "insert_record" and c[2] == "video_text" for c in store.calls)
    assert any(c[0] == "insert_video_comments" for c in store.calls)
    assert report.comments_stored == 1


def test_collect_video_survives_comment_stage_failure():
    class ExplodingBrowser(FakeBrowser):
        def extract_video_comments(self, **kwargs):
            raise RuntimeError("boom")

    store = FakeStore()
    intake = make_intake(browser=ExplodingBrowser(), store=store)
    report = intake.collect_video(URL)
    # video_text spot still written; comment_result records the empty analysis
    assert ("insert_record", "东湖", "video_text") in store.calls
    assert report.comments_stored == 0
    assert report.comment_result["spot_quality_score"] is None


def test_collect_video_respects_max_video_places():
    geocoder = FakeGeocoder()
    intake = make_intake(geocoder=geocoder, llm=FakeLlm(places=["东湖", "月湖", "汤逊湖", "梁子湖"]), max_video_places=2)
    report = intake.collect_video(URL)
    assert [c[0] for c in geocoder.calls] == ["东湖", "月湖"]
    assert report.spot_names == ["东湖", "月湖"]


def test_collect_video_drops_low_confidence_comment_places():
    geocoder = FakeGeocoder(score=70)  # below the 80 threshold for comment clues
    intake = make_intake(geocoder=geocoder, llm=FakeLlm(comment_places=[
        {"place_name": "月湖", "comment_indexes": [1], "comment_ids": [11], "evidence": "", "confidence": 0.8},
    ]))
    report = intake.collect_video(URL)
    assert "月湖" not in report.spot_names


def test_collect_video_fills_title_from_store_metadata():
    store = FakeStore(metadata={"title": "老标题", "author": "老作者", "publish_time": "2026-01-01 00:00", "raw_text": "旧文本"})
    browser = FakeBrowser(video={"title": "", "content": "无标题页面"})
    intake = make_intake(browser=browser, store=store, llm=FakeLlm())
    report = intake.collect_video(URL)
    assert report.video["title"] == "老标题"
    assert report.video["author"] == "老作者"


# --- collect_keyword -------------------------------------------------------------

def test_collect_keyword_loops_search_results_and_skips_empty_urls():
    searcher = FakeSearcher([{"url": URL, "desc": "d", "author": "a"}, {"url": ""}, {"url": URL + "2"}])
    intake = make_intake(searcher=searcher)
    reports = intake.collect_keyword("武汉钓鱼", 3)
    assert searcher.calls == [("武汉钓鱼", 3)]
    assert len(reports) == 2
    assert [r.video["url"] for r in reports] == [URL, URL + "2"]


def test_collect_keyword_requires_searcher():
    intake = make_intake()
    with pytest.raises(RuntimeError, match="Searcher"):
        intake.collect_keyword("武汉钓鱼", 1)


# --- extract (debug path) ---------------------------------------------------------

def test_extract_offline_with_fixtures_uses_no_io():
    browser = FakeBrowser()
    store = FakeStore()
    intake = make_intake(browser=browser, store=store)
    comments = load_comments_fixture(str(ROOT / "data" / "douyin_video_comments_7541589186801831228.json"))
    report = intake.extract(
        URL,
        extracted={"title": "fixture 视频", "content": "发布时间：2026-07-01 10:00\n东湖黄尾"},
        comments=comments,
    )
    assert browser.opened_urls == []  # nothing read live
    assert store.calls == []  # nothing written
    assert report["video"]["title"] == "fixture 视频"
    assert "comments" in report
    assert report["comments"]["comment_count"] == len(comments)


def test_extract_without_url_and_fixture_raises():
    intake = make_intake()
    with pytest.raises(ValueError, match="requires a url"):
        intake.extract("")


# --- options validation --------------------------------------------------------------

def test_options_validation_rejects_bad_values():
    with pytest.raises(ValueError):
        make_intake(delay_min=-1, delay_max=0)
    with pytest.raises(ValueError):
        make_intake(comment_quality_group_size=0)
