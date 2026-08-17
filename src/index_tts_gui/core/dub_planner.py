"""配音时间轴规划 — 音频自然时长下的偏移计算（影响最小原则）"""
from __future__ import annotations

from index_tts_gui.core.subtitle import SubtitleEntry


def plan_dub_timeline(
    entries: list[SubtitleEntry], durations: list[float]
) -> list[SubtitleEntry]:
    """按合成音频的实际时长规划时间轴，只顺延真正被顶到的字幕块。

    规则（从前往后逐段放置）：
    - 首段保持原始开始时间：new_start[0] = start[0]
    - new_end[i] = new_start[i] + durations[i]
    - new_start[i+1] = max(start[i+1], new_end[i])：
      * 前一段结束时还没到后一段的开始时间 → 后一段保持原时间戳，
        溢出由段间空白自然吸收；
      * 前一段超时占用了后一段的位置 → 后一段紧接前一段之后开始，
        只顺延这一段；
      * 后续某段之前重新出现空档时，时间轴自动回到原时间戳。
    - 原字幕本就重叠（start[i+1] < end[i]）：重叠在顺序音轨中无法表达，
      按背靠背拼接（该段必然顺延）。

    该放置对每段都是「不早于原时间戳、不早于前一段结束」的最早可行起点，
    因此被移动的块数最少。

    Args:
        entries: 原始字幕条目（顺序与合成顺序一致）
        durations: 每段合成音频的实际时长（秒），长度须与 entries 一致

    Returns:
        偏移后的 SubtitleEntry 列表（保留原始序号与文本）
    """
    if len(entries) != len(durations):
        raise ValueError(
            f"字幕条目数量（{len(entries)}）与音频时长数量（{len(durations)}）不一致"
        )
    if not entries:
        return []

    new_entries: list[SubtitleEntry] = []
    new_start = entries[0].start_sec
    for i, (entry, duration) in enumerate(zip(entries, durations)):
        new_end = new_start + max(0.0, duration)
        new_entries.append(
            SubtitleEntry(entry.index, new_start, new_end, entry.text)
        )
        if i + 1 < len(entries):
            new_start = max(entries[i + 1].start_sec, new_end)
    return new_entries


def build_track_pauses(new_entries: list[SubtitleEntry]) -> list[float]:
    """把新时间轴转成「每段前的静音时长」，供拼接使用。

    - 第一段的静音 = 其开始时间（保持首段原始 start）
    - 其后每段的静音 = new_start[i] - new_end[i-1]
    - 按规划规则静音恒 >= 0；max(0, ...) 仅作兜底。
    """
    pauses: list[float] = []
    prev_end: float | None = None
    for entry in new_entries:
        if prev_end is None:
            pauses.append(max(0.0, entry.start_sec))
        else:
            pauses.append(max(0.0, entry.start_sec - prev_end))
        prev_end = entry.end_sec
    return pauses
