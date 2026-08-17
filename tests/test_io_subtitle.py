"""测试 SRT / ASS 字幕解析"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from index_tts_gui.core.io_subtitle import (
    parse_ass,
    parse_srt,
    parse_subtitle_file,
)
from index_tts_gui.core.subtitle import SubtitleEntry


SRT_SAMPLE = """\
1
00:00:01,000 --> 00:00:03,500
第一句

2
00:00:03,500 --> 00:00:06,000
第二句第一行
第二句第二行

"""


def test_parse_srt_basic(tmp_path):
    path = tmp_path / "test.srt"
    path.write_text(SRT_SAMPLE, encoding="utf-8")
    entries = parse_subtitle_file(str(path))
    assert len(entries) == 2
    assert entries[0] == SubtitleEntry(1, 1.0, 3.5, "第一句")
    assert entries[1] == SubtitleEntry(2, 3.5, 6.0, "第二句第一行\n第二句第二行")


def test_parse_srt_multiline_text(tmp_path):
    content = (
        "1\n00:00:00,000 --> 00:00:01,000\n第一行\n第二行\n第三行\n\n"
    )
    path = tmp_path / "multi.srt"
    path.write_text(content, encoding="utf-8")
    entries = parse_srt(str(path))
    assert len(entries) == 1
    assert entries[0].text == "第一行\n第二行\n第三行"


def test_parse_srt_skips_empty_text(tmp_path):
    content = (
        "1\n00:00:00,000 --> 00:00:01,000\n\n"
        "2\n00:00:01,000 --> 00:00:02,000\n有效\n\n"
    )
    path = tmp_path / "empty.srt"
    path.write_text(content, encoding="utf-8")
    entries = parse_srt(str(path))
    assert len(entries) == 1
    # 空文本被跳过，序号重排为 1-based
    assert entries[0].index == 1
    assert entries[0].text == "有效"


def test_parse_srt_bad_format_raises(tmp_path):
    content = "1\n这不是时间行\n文本\n"
    path = tmp_path / "bad.srt"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError, match="时间行"):
        parse_srt(str(path))


def test_parse_srt_no_entries_raises(tmp_path):
    path = tmp_path / "empty_file.srt"
    path.write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="没有有效"):
        parse_srt(str(path))


def test_parse_subtitle_file_missing_raises():
    with pytest.raises(ValueError, match="不存在"):
        parse_subtitle_file("/nonexistent/not_here.srt")


def test_parse_subtitle_file_unsupported_ext(tmp_path):
    path = tmp_path / "test.vtt"
    path.write_text("WEBVTT\n", encoding="utf-8")
    with pytest.raises(ValueError, match="不支持"):
        parse_subtitle_file(str(path))


ASS_SAMPLE = """\
[Script Info]
ScriptType: v4.00+

[V4+ Styles]
Format: Name, Fontname, Fontsize
Style: Default,Arial,20

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:01.00,0:00:03.50,Default,,0,0,0,,{\\fad(200,200)}第一句
Comment: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,注释不应出现
Dialogue: 0,0:00:03.50,0:00:06.00,Default,,0,0,0,,第二句\\N换行,含逗号
"""


def test_parse_ass_basic(tmp_path):
    path = tmp_path / "test.ass"
    path.write_text(ASS_SAMPLE, encoding="utf-8")
    entries = parse_subtitle_file(str(path))
    assert len(entries) == 2
    assert entries[0] == SubtitleEntry(1, 1.0, 3.5, "第一句")
    assert entries[1].start_sec == 3.5
    assert entries[1].end_sec == 6.0


def test_parse_ass_strips_override_tags(tmp_path):
    path = tmp_path / "tags.ass"
    content = (
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        "Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,"
        "{\\pos(100,200)\\c&HFFFFFF&}正文{\\fad(100,100)}\n"
    )
    path.write_text(content, encoding="utf-8")
    entries = parse_ass(str(path))
    assert entries[0].text == "正文"


def test_parse_ass_newline_and_comma(tmp_path):
    path = tmp_path / "nl.ass"
    path.write_text(ASS_SAMPLE, encoding="utf-8")
    entries = parse_ass(str(path))
    # \N 转换行，Text 列中的逗号保留
    assert entries[1].text == "第二句\n换行,含逗号"


def test_parse_ass_skips_comments(tmp_path):
    path = tmp_path / "comment.ass"
    path.write_text(ASS_SAMPLE, encoding="utf-8")
    entries = parse_ass(str(path))
    assert all("注释" not in e.text for e in entries)


def test_parse_ass_missing_events_raises(tmp_path):
    path = tmp_path / "noevents.ass"
    path.write_text("[Script Info]\nScriptType: v4.00+\n", encoding="utf-8")
    with pytest.raises(ValueError, match="\\[Events\\]"):
        parse_ass(str(path))


def test_parse_ass_missing_format_raises(tmp_path):
    path = tmp_path / "noformat.ass"
    path.write_text(
        "[Events]\nDialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,文本\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="Format"):
        parse_ass(str(path))
