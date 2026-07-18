"""Pure-function tests for the Mimo ASR adapter: response parsing, SRT
rendering, and the no_speech heuristic. No API calls."""

from spot_intake.adapters.mimo_asr import find_segments, is_no_speech, pick_text, write_srt


def test_pick_text_from_plain_string_content():
    obj = {"choices": [{"message": {"content": "今天在府河钓鱼"}}]}
    assert pick_text(obj) == "今天在府河钓鱼"


def test_pick_text_unwraps_json_string_content():
    obj = {"choices": [{"message": {"content": '{"text": "东湖野钓"}'}}]}
    assert pick_text(obj) == "东湖野钓"


def test_pick_text_from_list_content():
    obj = {"choices": [{"message": {"content": [{"text": "第一段"}, {"transcript": "第二段"}]}}]}
    assert pick_text(obj) == "第一段\n第二段"


def test_pick_text_from_segments_when_no_direct_text():
    obj = {"choices": [{"message": {"content": {"segments": [{"text": "钓鱼去"}, {"text": "上鱼了"}]}}}]}
    assert pick_text(obj) == "钓鱼去\n上鱼了"


def test_find_segments_from_stringified_json():
    obj = {"choices": [{"message": {"content": '{"segments": [{"start": 0, "end": 1.5, "text": "钓鱼"}]}'}}]}
    assert find_segments(obj) == [{"start": 0, "end": 1.5, "text": "钓鱼"}]


def test_write_srt_formats_timestamps(tmp_path):
    srt = write_srt([{"start": 0, "end": 61.5, "text": "钓鱼"}], tmp_path / "t.srt")
    assert srt is not None
    assert "00:00:00,000 --> 00:01:01,500" in srt.read_text(encoding="utf-8")


def test_write_srt_returns_none_for_empty_segments(tmp_path):
    assert write_srt([{"start": 0, "end": 1, "text": ""}], tmp_path / "t.srt") is None


def test_is_no_speech_empty_and_punctuation_only():
    assert is_no_speech("")
    assert is_no_speech("  。。。，，！ \n")
    assert is_no_speech("...")


def test_is_no_speech_filler_only():
    assert is_no_speech("嗯嗯嗯")
    assert is_no_speech("啊，哦……呃！")


def test_is_no_speech_real_content():
    assert not is_no_speech("今天在府河用蚯蚓上了三条翘嘴")
    assert not is_no_speech("嗯，今天在府河钓鱼")  # filler + content = content
