"""Tests for the pure extraction module — the intake test surface."""

import json
from datetime import datetime
from pathlib import Path

import pytest

from spot_intake.extract import (
    classify_place_name,
    refine_precision,
    aggregate_comment_keywords,
    aggregate_quality_scores,
    clean_text_for_llm,
    dedupe_places,
    extract_comment_place_names,
    extract_comment_spot_clues_from_comments,
    extract_fish_species,
    format_comments_for_llm,
    is_comment_candidate,
    normalize_comment_keyword,
    normalize_comment_keyword_category,
    normalize_fish_species,
    normalize_quality_score,
    parse_douyin_comment_time,
)

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 7, 18, 12, 0, 0)


# --- parse_douyin_comment_time --------------------------------------------

@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("刚刚", "2026-07-18 12:00:00"),
        ("5分钟前", "2026-07-18 11:55:00"),
        ("3小时前", "2026-07-18 09:00:00"),
        ("2天前", "2026-07-16 12:00:00"),
        ("1周前", "2026-07-11 12:00:00"),
        ("2月前", "2026-05-18 12:00:00"),
        ("1年前", "2025-07-18 12:00:00"),
        ("今天", "2026-07-18 12:00:00"),
        ("今天 08:30", "2026-07-18 08:30:00"),
        ("昨天", "2026-07-17 12:00:00"),
        ("昨天 23:15", "2026-07-17 23:15:00"),
        ("2026-07-01", "2026-07-01 00:00:00"),
        ("2026-07-01 09:05", "2026-07-01 09:05:00"),
        ("07-01", "2026-07-01 00:00:00"),
        ("12-31 10:00", "2025-12-31 10:00:00"),  # future MM-DD rolls back a year
        ("9月前·湖北", "2025-10-18 12:00:00"),  # IP suffix stripped
        ("", ""),
        ("not a date", ""),
    ],
)
def test_parse_douyin_comment_time(value, expected):
    assert parse_douyin_comment_time(value, now=NOW) == expected


def test_parse_comment_time_month_shift_clamps_day():
    now = datetime(2026, 3, 31, 10, 0, 0)
    assert parse_douyin_comment_time("1月前", now=now) == "2026-02-28 10:00:00"


# --- dedupe_places ---------------------------------------------------------

def test_dedupe_places_strips_prefixes_and_generic_words():
    assert dedupe_places(["武汉东湖", "湖北省梁子湖", "野钓", "附近", "东"]) == ["东湖", "梁子湖"]


def test_dedupe_places_prefers_more_specific_names():
    assert dedupe_places(["东湖", "东湖绿道", "汤逊湖"]) == ["东湖绿道", "汤逊湖"]


# --- fish species ----------------------------------------------------------

def test_normalize_fish_species_exact_alias():
    assert normalize_fish_species(["工程鲫", "大板鲫"]) == ["鲫鱼"]


def test_normalize_fish_species_substring_alias():
    assert normalize_fish_species(["钓到一条大翘嘴"]) == ["翘嘴"]


def test_normalize_fish_species_keeps_unknown_short_names():
    assert normalize_fish_species(["未知鱼", "这是一个很长的不知道什么鱼的名字"]) == ["未知鱼"]


def test_normalize_fish_species_dedupes_preserving_order():
    assert normalize_fish_species(["鲫鱼", "黄尾", "鲫鱼"]) == ["鲫鱼", "黄尾鲴"]


def test_extract_fish_species_from_text():
    assert extract_fish_species("今天在东湖钓到武昌鱼和两条胖头鱼，翘壳也有口") == ["鳊鱼", "翘嘴", "鲢鳙"]


# --- comment candidates / place names ---------------------------------------

@pytest.mark.parametrize(
    ("line", "ok"),
    [
        ("东湖绿道那里可以钓", True),
        ("全部评论", False),
        ("12345", False),
        ("12:30", False),
        ("3天前", False),
        ("2小时前·湖北", False),
        ("哈", False),
        ("今天天气真好啊", False),  # no place hint
        ("这个位置怎么样", True),
    ],
)
def test_is_comment_candidate(line, ok):
    assert is_comment_candidate(line) is ok


def test_extract_comment_place_names_basic():
    assert extract_comment_place_names("去武汉东湖钓过，梁子湖也不错", "武汉") == ["东湖", "梁子湖"]


def test_extract_comment_place_names_diaodian_suffix_requires_hint():
    # "黄尾钓点" style: stripped suffix must still contain a place hint
    places = extract_comment_place_names("月湖钓点不错", "武汉")
    assert "月湖" in places
    assert extract_comment_place_names("这个地方钓点不错", "武汉") == []


def test_extract_comment_place_names_drops_city_and_generic():
    assert extract_comment_place_names("武汉哪里", "武汉") == []


# --- clean_text_for_llm -----------------------------------------------------

def test_clean_text_for_llm_strips_markdown_and_noise():
    raw = (
        "![cover](https://example.com/x.jpg)\n"
        "[野芷湖钓鱼](https://www.douyin.com/video/1)\n"
        "全部评论\n"
        "12345\n"
        "1.5x\n"
        "今天野芷湖黄尾口很好 #钓鱼\n"
        "推荐视频\n"
        "这条不该出现\n"
    )
    cleaned = clean_text_for_llm(raw)
    assert "野芷湖钓鱼" in cleaned
    assert "今天野芷湖黄尾口很好" in cleaned
    assert "全部评论" not in cleaned
    assert "12345" not in cleaned
    assert "1.5x" not in cleaned
    assert "这条不该出现" not in cleaned  # stops at 推荐视频


def test_clean_text_for_llm_dedupes_lines():
    assert clean_text_for_llm("钓鱼 钓鱼\n钓鱼 钓鱼") == "钓鱼 钓鱼"


# --- format_comments_for_llm ------------------------------------------------

def test_format_comments_for_llm_indexes_and_falls_back():
    comments = [
        {"author": "", "text": "第一条", "comment_time_standard": "2026-07-01 00:00:00"},
        {"author": "老王", "text": "  多   空格  ", "comment_time_raw": "3天前"},
        {"author": "跳过", "text": "   "},
    ]
    out = format_comments_for_llm(comments)
    lines = out.splitlines()
    assert lines[0].startswith("[1] 匿名 2026-07-01 00:00:00: 第一条")
    assert lines[1].startswith("[2] 老王 3天前: 多 空格")
    assert len(lines) == 2


def test_format_comments_for_llm_respects_max_chars():
    comments = [{"author": "a", "text": "x" * 100} for _ in range(5)]
    out = format_comments_for_llm(comments, max_chars=130)
    assert len(out.splitlines()) == 1


# --- keyword normalization / aggregation ------------------------------------

@pytest.mark.parametrize(
    ("raw", "expected"),
    [("fish_condition", "fish_condition"), ("鱼情", "fish_condition"), ("禁钓", "restriction"), ("不存在", "")],
)
def test_normalize_comment_keyword_category(raw, expected):
    assert normalize_comment_keyword_category(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("有口", "有口"), (" 有 口 ", "有口"), ("钓点", ""), ("这里", ""), ("x" * 17, "")],
)
def test_normalize_comment_keyword(raw, expected):
    assert normalize_comment_keyword(raw) == expected


def test_aggregate_comment_keywords_groups_sorts_and_averages():
    keywords = [
        {"keyword": "有口", "category": "fish_condition", "confidence": 0.8, "comment_id": 1},
        {"keyword": "有口", "category": "fish_condition", "confidence": 0.6, "comment_id": 2},
        {"keyword": "有口", "category": "fish_condition", "confidence": 0.6, "comment_id": 2},  # dup comment
        {"keyword": "好停车", "category": "access", "confidence": 0.9, "comment_id": 3},
        {"keyword": "", "category": "fish", "confidence": 0.9, "comment_id": 4},  # dropped
    ]
    agg = aggregate_comment_keywords(keywords)
    assert agg[0] == {"keyword": "有口", "category": "fish_condition", "count": 3, "avg_confidence": pytest.approx(0.6667, abs=1e-4), "comment_ids": [1, 2]}
    assert agg[1]["keyword"] == "好停车"
    assert len(agg) == 2


# --- quality scores ---------------------------------------------------------

def test_normalize_quality_score_clamps():
    assert normalize_quality_score(1) == 0.0
    assert normalize_quality_score(3) == 0.5
    assert normalize_quality_score(5) == 1.0
    assert normalize_quality_score(99) == 1.0


def test_aggregate_quality_scores_empty():
    assert aggregate_quality_scores([]) == {"quality_score": None, "confidence": 0.0, "detail": ""}


def test_aggregate_quality_scores_weights_by_confidence():
    groups = [
        {"group_index": 1, "comment_ids": [1], "score_1_5": 4, "normalized_score": 0.75, "confidence": 0.9, "summary": "好"},
        {"group_index": 2, "comment_ids": [2], "score_1_5": 2, "normalized_score": 0.25, "confidence": 0.1, "summary": "差"},
    ]
    result = aggregate_quality_scores(groups)
    assert result["quality_score"] == pytest.approx(0.7, abs=1e-4)
    assert result["confidence"] == pytest.approx(0.5, abs=1e-4)
    assert "第1组" in result["detail"]


def test_aggregate_quality_scores_filters_by_comment_ids():
    groups = [
        {"group_index": 1, "comment_ids": [1], "score_1_5": 4, "normalized_score": 0.75, "confidence": 0.9, "summary": ""},
        {"group_index": 2, "comment_ids": [2], "score_1_5": 2, "normalized_score": 0.25, "confidence": 0.9, "summary": ""},
    ]
    assert aggregate_quality_scores(groups, comment_ids=[1])["quality_score"] == 0.75
    assert aggregate_quality_scores(groups, comment_ids=[999])["quality_score"] is None


# --- real comment fixtures --------------------------------------------------

def test_comment_spot_clues_from_saved_fixture():
    fixture = ROOT / "data" / "douyin_video_comments_7541589186801831228.json"
    comments = json.loads(fixture.read_text(encoding="utf-8"))["comments"]
    clues = extract_comment_spot_clues_from_comments(comments, "武汉")
    # every clue must carry candidates and point back at a real comment index
    for clue in clues:
        assert clue["place_candidates"]
        assert 1 <= clue["comment_index"] <= len(comments)
        assert clue["text"] == comments[clue["comment_index"] - 1]["text"]
    # rule-based clues are a subset relationship with the candidate filter:
    # no clue may come from a line that fails is_comment_candidate
    assert all(is_comment_candidate(c["text"]) for c in clues)


# --- 精度分级 (precision) --------------------------------------------------------

def test_classify_rejects_anchorless_generics_and_main_stems():
    assert classify_place_name("凼子") == "reject"
    assert classify_place_name("河边") == "reject"
    assert classify_place_name("长江") == "reject"
    assert classify_place_name("汉江") == "reject"


def test_classify_rejects_admin_names_but_keeps_scenic_exceptions():
    assert classify_place_name("武昌区") == "reject"
    assert classify_place_name("洪山区") == "reject"
    assert classify_place_name("新洲县") == "reject"
    assert classify_place_name("吹笛景区") == "point"  # 景区 is a place, not a district
    assert classify_place_name("东湖风景区") == "point"


def test_classify_segments_tributaries_and_village_granularity():
    assert classify_place_name("府河") == "segment"  # bare tributary has value
    assert classify_place_name("东荆河") == "segment"
    assert classify_place_name("滠水") == "segment"
    assert classify_place_name("联丰村") == "segment"
    assert classify_place_name("和平街道") == "segment"
    assert classify_place_name("蔡甸沌口片区") == "segment"


def test_classify_points_for_anchors_and_compact_water():
    assert classify_place_name("海绵科普展馆") == "point"
    assert classify_place_name("府河大桥") == "point"  # modified, not a bare river name
    assert classify_place_name("月湖") == "point"  # compact lake: geocoder point is meaningful
    assert classify_place_name("野芷湖公园") == "point"


def test_refine_precision_rejects_district_aliases_and_levels():
    assert refine_precision("point", {"geocode_level": "POI", "query_name": "汉南区"}) == "reject"
    assert refine_precision("point", {"geocode_level": "区县", "query_name": "武昌区"}) == "reject"
    assert refine_precision("segment", {"geocode_level": "城市", "query_name": "武汉"}) == "reject"


def test_refine_precision_segments_village_levels_and_passes_through():
    assert refine_precision("point", {"geocode_level": "村庄", "query_name": "联丰村"}) == "segment"
    assert refine_precision("point", {"geocode_level": "乡镇", "query_name": "某镇"}) == "segment"
    assert refine_precision("point", {"geocode_level": "POI", "query_name": "海绵科普展馆"}) == "point"
    assert refine_precision("segment", {"geocode_level": "POI", "query_name": "府河"}) == "segment"
    assert refine_precision("reject", {"geocode_level": "POI", "query_name": "凼子"}) == "reject"
