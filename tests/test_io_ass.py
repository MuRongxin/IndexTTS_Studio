"""测试 ASS 字幕导出"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from index_tts_gui.core.io_ass import entries_to_ass, _seconds_to_ass_time
from index_tts_gui.core.subtitle import SubtitleEntry


def test_seconds_to_ass_time():
    assert _seconds_to_ass_time(3661.125) == "1:01:01.12"
    assert _seconds_to_ass_time(0.0) == "0:00:00.00"


def test_entries_to_ass(tmp_path):
    entries = [
        SubtitleEntry(1, 1.0, 3.5, "第一句"),
        SubtitleEntry(2, 3.5, 6.0, "第二句\n换行"),
    ]
    path = str(tmp_path / "test.ass")
    entries_to_ass(entries, path)

    assert os.path.exists(path)
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    assert "[Script Info]" in content
    assert "[V4+ Styles]" in content
    assert "[Events]" in content
    assert "Dialogue: 0,0:00:01.00,0:00:03.50,Default,,0,0,0,,{\\fad(100,200)}第一句" in content
    assert "&HFF0000&" in content  # 蓝色描边 BBGGRR
    assert "{\\fad(100,200)}第二句\\N换行" in content
