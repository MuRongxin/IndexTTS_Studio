"""测试配音偏移时间轴规划（影响最小原则）"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from index_tts_gui.core.dub_planner import build_track_pauses, plan_dub_timeline
from index_tts_gui.core.subtitle import SubtitleEntry


def _times(entries):
    return [(e.start_sec, e.end_sec) for e in entries]


def test_no_conflict_no_shift():
    """音频未超时：各段保持原始时间戳，结束时间改为开始+实际时长。"""
    entries = [
        SubtitleEntry(1, 0.0, 2.0, "a"),
        SubtitleEntry(2, 3.0, 5.0, "b"),
    ]
    new = plan_dub_timeline(entries, [1.0, 1.0])
    assert _times(new) == [(0.0, 1.0), (3.0, 4.0)]
    # 文本与序号保留
    assert [e.text for e in new] == ["a", "b"]
    assert [e.index for e in new] == [1, 2]


def test_overflow_absorbed_by_gap():
    """溢出量小于段间空白：空白吸收溢出，后续块完全不移动。"""
    entries = [
        SubtitleEntry(1, 0.0, 2.0, "a"),
        SubtitleEntry(2, 3.0, 5.0, "b"),
    ]
    new = plan_dub_timeline(entries, [2.5, 1.0])
    # 第 1 段 0~2.5 超出原槽 0.5s，但下一段 3.0 才开始，空白吸收
    assert _times(new) == [(0.0, 2.5), (3.0, 4.0)]


def test_overflow_shifts_only_collided_block():
    """超时顶到下一块：仅顺延被顶到的那块，之后有空档立即回到原时间戳。"""
    entries = [
        SubtitleEntry(1, 0.0, 2.0, "a"),
        SubtitleEntry(2, 3.0, 5.0, "b"),
        SubtitleEntry(3, 8.0, 10.0, "c"),
    ]
    durations = [3.5, 1.0, 1.0]
    new = plan_dub_timeline(entries, durations)
    # 第 1 段 0~3.5 顶到第 2 段（原 3.0），第 2 段顺延为 3.5~4.5；
    # 第 2 段结束（4.5）早于第 3 段原开始（8.0），第 3 段保持不动
    assert _times(new) == [(0.0, 3.5), (3.5, 4.5), (8.0, 9.0)]


def test_short_block_recovers_timeline():
    """顺延后的短块吃掉延迟，后续块回到原时间戳。"""
    entries = [
        SubtitleEntry(1, 0.0, 2.0, "a"),
        SubtitleEntry(2, 3.0, 5.0, "b"),
        SubtitleEntry(3, 5.5, 7.0, "c"),
    ]
    durations = [3.0, 0.5, 1.0]
    new = plan_dub_timeline(entries, durations)
    # 第 1 段 0~3.0 顶到第 2 段：3.0~3.5；
    # 第 2 段结束 3.5 早于第 3 段原开始 5.5，第 3 段不动
    assert _times(new) == [(0.0, 3.0), (3.0, 3.5), (5.5, 6.5)]


def test_original_overlap_becomes_back_to_back():
    """原字幕本就重叠：顺序音轨无法表达重叠，按背靠背拼接。"""
    entries = [
        SubtitleEntry(1, 0.0, 5.0, "a"),
        SubtitleEntry(2, 3.0, 8.0, "b"),
    ]
    new = plan_dub_timeline(entries, [7.0, 1.0])
    assert _times(new) == [(0.0, 7.0), (7.0, 8.0)]


def test_first_keeps_original_start():
    """首段保持原始开始时间（即使不是 0）。"""
    entries = [
        SubtitleEntry(1, 3.0, 5.0, "a"),
        SubtitleEntry(2, 6.0, 7.0, "b"),
    ]
    new = plan_dub_timeline(entries, [1.0, 1.0])
    assert new[0].start_sec == 3.0
    assert _times(new) == [(3.0, 4.0), (6.0, 7.0)]


def test_empty_input():
    assert plan_dub_timeline([], []) == []


def test_length_mismatch_raises():
    entries = [SubtitleEntry(1, 0.0, 1.0, "a")]
    with pytest.raises(ValueError, match="不一致"):
        plan_dub_timeline(entries, [])


def test_build_track_pauses():
    new_entries = [
        SubtitleEntry(1, 1.0, 3.0, "a"),
        SubtitleEntry(2, 4.0, 6.0, "b"),
    ]
    assert build_track_pauses(new_entries) == [1.0, 1.0]


def test_build_track_pauses_clamps_negative():
    """规划结果静音恒非负；max(0, ...) 仅兜底，负值钳制为 0。"""
    new_entries = [
        SubtitleEntry(1, 0.0, 5.0, "a"),
        SubtitleEntry(2, 4.0, 6.0, "b"),
    ]
    assert build_track_pauses(new_entries) == [0.0, 0.0]


def test_build_track_pauses_empty():
    assert build_track_pauses([]) == []
