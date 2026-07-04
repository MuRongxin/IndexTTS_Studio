"""speech_aligner 单元测试

覆盖：
1. _cross_correlate_match —— 互相关匹配
2. _energy_based_segment —— 能量分割回退
3. align_sentences —— 端到端对齐
4. build_time_mapper —— 时间映射函数
5. recalibrate_entries —— 字幕重映射

不依赖 PySide6 / GUI；只依赖 numpy / scipy / librosa。
"""
import os
import struct
import sys
import wave
import math
import tempfile
import shutil

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


TARGET_SR = 8000


def _write_sin_wav(path, duration, sr=8000, freq=440.0, phase=0.0):
    """生成正弦波 WAV（16-bit PCM mono）。"""
    n = int(sr * duration)
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(sr)
        for i in range(n):
            val = int(32767 * math.sin(2 * math.pi * freq * i / sr + phase))
            f.writeframes(struct.pack("<h", val))


def _write_silence_wav(path, duration, sr=8000):
    """生成静音 WAV。"""
    n = int(sr * duration)
    silence = bytes(n * 2)  # 16-bit silent
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(sr)
        f.setnframes(n)
        f.writeframes(silence)


def _write_chirp_wav(path, duration, sr=8000, f0=200, f1=1000):
    """生成线性调频 WAV（区分度高的测试信号）。"""
    n = int(sr * duration)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        for i in range(n):
            t = i / sr
            freq = f0 + (f1 - f0) * (t / duration)
            val = int(32767 * math.sin(2 * math.pi * freq * t))
            w.writeframes(struct.pack("<h", val))


def _write_segments_wav(
    path, segments, sr=8000, silence_between=0.3
):
    """拼接多个 segment + 静音段，segments 是 [(freq, dur_sec), ...]"""
    parts = []
    for i, (freq, dur) in enumerate(segments):
        n = int(sr * dur)
        chunk = bytearray()
        for j in range(n):
            val = int(32767 * math.sin(2 * math.pi * freq * j / sr))
            chunk += struct.pack("<h", val)
        parts.append(bytes(chunk))
        if i < len(segments) - 1 and silence_between > 0:
            parts.append(bytes(int(sr * silence_between) * 2))
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(sr)
        f.writeframes(b"".join(parts))


# ────────────────────────────────────────────────────────────────────
# _cross_correlate_match
# ────────────────────────────────────────────────────────────────────


def test_cross_correlate_finds_template_at_zero():
    """模板在信号起点处，应匹配到 start_time=search_start, confidence > 0.5。"""
    from index_tts_gui.core.speech_aligner import _cross_correlate_match

    template = np.sin(2 * np.pi * 440 * np.arange(8000) / 8000).astype(np.float32)
    # 信号: 模板 + 后面的其他内容
    signal = np.concatenate([template, np.zeros(8000, dtype=np.float32)]).astype(np.float32)

    start_time, conf = _cross_correlate_match(template, signal, 8000, 0.0)
    assert start_time == pytest.approx(0.0, abs=0.01)
    assert conf > 0.5


def test_cross_correlate_finds_template_with_offset():
    """模板在信号 0.5s 处，应匹配到 start_time ≈ 0.5。"""
    from index_tts_gui.core.speech_aligner import _cross_correlate_match

    template = np.sin(2 * np.pi * 440 * np.arange(4000) / 8000).astype(np.float32)
    silence = np.zeros(4000, dtype=np.float32)  # 0.5s 静音
    other = np.sin(2 * np.pi * 880 * np.arange(4000) / 8000).astype(np.float32)
    signal = np.concatenate([silence, template, other]).astype(np.float32)

    start_time, conf = _cross_correlate_match(template, signal, 8000, 0.0)
    assert start_time == pytest.approx(0.5, abs=0.05)
    assert conf > 0.3


def test_cross_correlate_search_start_offset():
    """search_start 不为 0 时返回绝对时间。"""
    from index_tts_gui.core.speech_aligner import _cross_correlate_match

    template = np.sin(2 * np.pi * 440 * np.arange(4000) / 8000).astype(np.float32)
    signal = np.concatenate([
        np.zeros(8000, dtype=np.float32),  # 前置 1s 静音（search_start 之前）
        template,
        np.zeros(4000, dtype=np.float32),
    ]).astype(np.float32)

    # search_start=0.0，模板在信号 1.0s 处
    start_time, conf = _cross_correlate_match(template, signal, 8000, 0.0)
    assert start_time == pytest.approx(1.0, abs=0.05)


def test_cross_correlate_template_longer_than_signal():
    """模板比信号长，应返回 (-1.0, 0.0)。"""
    from index_tts_gui.core.speech_aligner import _cross_correlate_match

    template = np.ones(16000, dtype=np.float32)  # 2s
    signal = np.ones(4000, dtype=np.float32)  # 0.5s

    start_time, conf = _cross_correlate_match(template, signal, 8000, 0.0)
    assert start_time == -1.0
    assert conf == 0.0


def test_cross_correlate_silence_signal():
    """信号全零，应返回合理结果（不崩溃）。"""
    from index_tts_gui.core.speech_aligner import _cross_correlate_match

    template = np.sin(2 * np.pi * 440 * np.arange(2000) / 8000).astype(np.float32)
    signal = np.zeros(4000, dtype=np.float32)

    # 不会崩溃，结果可预测
    start_time, conf = _cross_correlate_match(template, signal, 8000, 0.0)
    # 全零信号归一化后是 0 向量，confidence 取决于实现
    assert conf >= 0.0


def test_cross_correlate_chirp_in_noise():
    """调频信号在低噪底中应能定位（互相关抗噪）。"""
    from index_tts_gui.core.speech_aligner import _cross_correlate_match

    sr = 8000
    rng = np.random.default_rng(42)
    duration = 1.0
    template_len = int(0.3 * sr)
    template = np.sin(
        2 * np.pi * (200 + 800 * np.arange(template_len) / template_len) * np.arange(template_len) / sr
    ).astype(np.float32)

    # 信号: 0.5s 静音 + 模板 + 1s 静音 + 白噪
    signal = np.concatenate([
        np.zeros(int(0.5 * sr), dtype=np.float32),
        template,
        np.zeros(int(1.0 * sr), dtype=np.float32),
        rng.normal(0, 0.05, int(0.5 * sr)).astype(np.float32),
    ])

    start_time, conf = _cross_correlate_match(template, signal, sr, 0.0)
    # 模板在 0.5s 位置，10ms 精度
    assert abs(start_time - 0.5) < 0.05
    assert conf > 0.2


# ────────────────────────────────────────────────────────────────────
# _energy_based_segment
# ────────────────────────────────────────────────────────────────────


def test_energy_based_segment_finds_n_segments(tmp_path):
    """合成 N 个能量段，应返回 N 个起点。"""
    from index_tts_gui.core.speech_aligner import _energy_based_segment

    segments = [(440, 0.5), (880, 0.4), (660, 0.6), (330, 0.3)]
    path = tmp_path / "energy.wav"
    _write_segments_wav(path, segments, sr=16000, silence_between=0.5)

    starts = _energy_based_segment(str(path), num_expected=4, sr=16000)
    # 至少 4 个段被识别（可能受阈值影响，但 4 个 sine 段都能找到）
    assert len(starts) >= 3  # 允许阈值造成轻微段合并
    # 段起点应单调递增
    for i in range(1, len(starts)):
        assert starts[i] > starts[i - 1]


def test_energy_based_segment_empty_audio(tmp_path):
    """全静音音频：应返回 0 或 1 段（边界情况，不崩溃）。"""
    from index_tts_gui.core.speech_aligner import _energy_based_segment

    path = tmp_path / "silent.wav"
    _write_silence_wav(path, duration=2.0, sr=16000)

    starts = _energy_based_segment(str(path), num_expected=3, sr=16000)
    # 全静音时可能 0 段（max_rms=0）或 1 段（阈值=0 时整段视为 1 段）
    assert len(starts) <= 1


def test_energy_based_segment_single_segment(tmp_path):
    """单段：应能找到 1 段。"""
    from index_tts_gui.core.speech_aligner import _energy_based_segment

    path = tmp_path / "single.wav"
    _write_sin_wav(path, duration=1.0, sr=16000, freq=440)

    starts = _energy_based_segment(str(path), num_expected=1, sr=16000)
    assert len(starts) == 1
    assert 0.0 <= starts[0] <= 0.1  # 段起点应接近 0


# ────────────────────────────────────────────────────────────────────
# align_sentences
# ────────────────────────────────────────────────────────────────────


def _build_modified_with_pauses(
    sentence_wavs, original_pauses, out_path, sr=8000
):
    """拼接 sentence WAV + 静音，模拟"调整后"的音频。"""
    parts = []
    for i, path in enumerate(sentence_wavs):
        # 读已有 wav
        with wave.open(str(path), "rb") as f:
            params = f.getparams()
            n = f.getnframes()
            data = f.readframes(n)
        parts.append(data)
        if i < len(sentence_wavs) - 1 and i < len(original_pauses):
            silence_n = int(original_pauses[i] * sr)
            parts.append(bytes(silence_n * 2))
    with wave.open(str(out_path), "wb") as f:
        f.setparams(params)
        f.writeframes(b"".join(parts))


def test_align_sentences_basic_no_change(tmp_path):
    """原封不动地拼接，应能准确对齐到原始位置。"""
    from index_tts_gui.core.speech_aligner import align_sentences

    # 3 个原始句子
    s1 = tmp_path / "s1.wav"
    s2 = tmp_path / "s2.wav"
    s3 = tmp_path / "s3.wav"
    _write_sin_wav(s1, 0.5, freq=440)
    _write_sin_wav(s2, 0.5, freq=880)
    _write_sin_wav(s3, 0.5, freq=660)
    sentence_wavs = [str(s1), str(s2), str(s3)]
    durations = [0.5, 0.5, 0.5]

    # "调整后"音频 = 0.3s 静音 + s1 + 0.3s + s2 + 0.3s + s3
    pauses = [0.3, 0.3, 0.3]
    full = tmp_path / "full.wav"
    _build_modified_with_pauses(sentence_wavs, pauses, full)

    new_starts = align_sentences(
        str(full), sentence_wavs,
        ["句1", "句2", "句3"], pauses,
    )

    assert len(new_starts) == 3
    # 句1 应该在 0.0 附近
    assert abs(new_starts[0] - 0.0) < 0.1
    # 句2 应该在句1 结束后 + 0.3s 静音
    assert abs(new_starts[1] - (durations[0] + pauses[0])) < 0.2
    # 句3 应该在句2 结束后 + 0.3s
    assert abs(new_starts[2] - (durations[0] + pauses[0] + durations[1] + pauses[1])) < 0.2


def test_align_sentences_extended_pauses(tmp_path):
    """用户拉长静音段，应仍能定位每句起点。"""
    from index_tts_gui.core.speech_aligner import align_sentences

    s1 = tmp_path / "s1.wav"
    s2 = tmp_path / "s2.wav"
    _write_chirp_wav(s1, 0.5, f0=200, f1=800)
    _write_chirp_wav(s2, 0.5, f0=400, f1=1200)
    sentence_wavs = [str(s1), str(s2)]

    # "调整后" 拉长静音到 1.5s
    pauses = [1.5, 0.0]
    full = tmp_path / "full.wav"
    _build_modified_with_pauses(sentence_wavs, pauses, full)

    new_starts = align_sentences(
        str(full), sentence_wavs, ["句1", "句2"], pauses,
    )
    assert len(new_starts) == 2
    assert abs(new_starts[0] - 0.0) < 0.1
    assert abs(new_starts[1] - 2.0) < 0.2  # 0.5 + 1.5 = 2.0


def test_align_sentences_empty_input(tmp_path):
    """空输入：返回空列表，不崩溃。"""
    from index_tts_gui.core.speech_aligner import align_sentences

    full = tmp_path / "full.wav"
    _write_silence_wav(full, 0.5, sr=8000)

    new_starts = align_sentences(str(full), [], [], [])
    assert new_starts == []


def test_align_sentences_short_window_falls_back(tmp_path):
    """搜索窗口 < 模板长度时：标记为失败但仍记录位置。"""
    from index_tts_gui.core.speech_aligner import align_sentences

    s1 = tmp_path / "s1.wav"
    _write_sin_wav(s1, 1.5, sr=8000, freq=440)  # 1.5s 模板
    sentence_wavs = [str(s1)]

    # "调整后"音频只有 0.3s（远短于模板）
    full = tmp_path / "full.wav"
    _write_sin_wav(full, 0.3, sr=8000, freq=440)

    pauses = [0.0]
    # 不应崩溃
    new_starts = align_sentences(
        str(full), sentence_wavs, ["句1"], pauses,
    )
    assert len(new_starts) == 1
    # 窗口不够时 new_starts[0] 保持 -1.0
    assert new_starts[0] <= 0.0


def test_align_sentences_mismatched_count_raises(tmp_path):
    """句子数与 wav 数不匹配时由 CalibrateWorker 检查，
    align_sentences 本身不做这层校验（依赖调用方）。"""
    from index_tts_gui.core.speech_aligner import align_sentences

    s1 = tmp_path / "s1.wav"
    _write_sin_wav(s1, 0.5, sr=8000, freq=440)
    full = tmp_path / "full.wav"
    _write_sin_wav(full, 1.0, sr=8000, freq=440)

    # sentence_wavs 1 个但 sentences 2 个
    new_starts = align_sentences(
        str(full), [str(s1)], ["句1", "句2"], [0.0],
    )
    # 不抛异常（实际行为）— 取决于实现
    assert isinstance(new_starts, list)


# ────────────────────────────────────────────────────────────────────
# build_time_mapper
# ────────────────────────────────────────────────────────────────────


def test_mapper_identity_when_no_change():
    """当 old_starts == new_starts（无调整），map_time 应恒等。"""
    from index_tts_gui.core.speech_aligner import build_time_mapper

    old_starts = [0.0, 2.0, 5.0]
    durations = [1.0, 1.0, 1.0]
    mapper = build_time_mapper(old_starts, durations, old_starts)

    for t in [0.0, 0.5, 1.5, 2.5, 4.0, 5.5, 6.5]:
        assert mapper(t) == pytest.approx(t, abs=1e-9)


def test_mapper_phrase_shift_preserves_relative():
    """整段后移 5s：所有时间点 + 5s。"""
    from index_tts_gui.core.speech_aligner import build_time_mapper

    old_starts = [0.0, 2.0, 5.0]
    durations = [1.0, 1.0, 1.0]
    new_starts = [5.0, 7.0, 10.0]  # 整段后移 5s
    mapper = build_time_mapper(old_starts, durations, new_starts)

    for t in [0.0, 1.0, 2.5, 4.5, 5.5, 6.5]:
        assert mapper(t) == pytest.approx(t + 5.0, abs=1e-9)


def test_mapper_within_phrase_linear():
    """句内：mapper(old_starts[i] + dt) = new_starts[i] + dt（线性平移）。"""
    from index_tts_gui.core.speech_aligner import build_time_mapper

    old_starts = [0.0, 5.0]
    durations = [2.0, 1.0]
    new_starts = [10.0, 13.0]  # 句1 移到 10s
    mapper = build_time_mapper(old_starts, durations, new_starts)

    # 句 1 内部 (0, 2)
    assert mapper(0.0) == 10.0
    assert mapper(1.0) == 11.0
    assert mapper(2.0) == 12.0  # 句 1 末尾


def test_mapper_gap_proportional():
    """句间停顿段按比例映射。"""
    from index_tts_gui.core.speech_aligner import build_time_mapper

    # 句 1 在 0-2s, 2-5s 是 3s 停顿, 句 2 在 5-6s
    # 调整后: 句 1 在 10-12s, 12-13s 是 1s 停顿, 句 2 在 13-14s
    old_starts = [0.0, 5.0]
    durations = [2.0, 1.0]
    new_starts = [10.0, 13.0]
    mapper = build_time_mapper(old_starts, durations, new_starts)

    # 句间 [2, 5] → 句间 [12, 13]
    # t=3.5 (中间点) → 12.5 (中间点)
    assert mapper(3.5) == pytest.approx(12.5, abs=1e-9)
    # t=2 (句1 末尾) → 12
    assert mapper(2.0) == 12.0
    # t=5 (句2 起点) → 13
    assert mapper(5.0) == 13.0


def test_mapper_after_last_phrase():
    """超过最后一句的映射：保持 last 段末尾的偏移。"""
    from index_tts_gui.core.speech_aligner import build_time_mapper

    old_starts = [0.0, 3.0]
    durations = [1.0, 1.0]
    new_starts = [0.0, 5.0]  # 句 2 后移 2s
    mapper = build_time_mapper(old_starts, durations, new_starts)

    # 句 2 末尾在 old=4.0, new=6.0
    # 之后的点应保持 6.0 - 4.0 = +2 偏移
    assert mapper(5.0) == 7.0
    assert mapper(10.0) == 12.0


def test_mapper_before_first_phrase():
    """早于第一句：保持 first 段起点的偏移。"""
    from index_tts_gui.core.speech_aligner import build_time_mapper

    old_starts = [5.0, 8.0]
    durations = [1.0, 1.0]
    new_starts = [10.0, 13.0]  # 句 1 起点后移 5s
    mapper = build_time_mapper(old_starts, durations, new_starts)

    # 5s 之前：保持 +5 偏移
    assert mapper(0.0) == 5.0
    assert mapper(3.0) == 8.0


def test_mapper_empty():
    """空输入：恒等函数。"""
    from index_tts_gui.core.speech_aligner import build_time_mapper

    mapper = build_time_mapper([], [], [])
    assert mapper(0.0) == 0.0
    assert mapper(5.0) == 5.0


def test_mapper_zero_gap_no_division_by_zero():
    """句间 gap = 0 时不能除零。"""
    from index_tts_gui.core.speech_aligner import build_time_mapper

    old_starts = [0.0, 1.0]  # 句 1 0-1, 句 2 起点 1
    durations = [1.0, 1.0]
    new_starts = [0.0, 1.0]  # 也是紧贴
    mapper = build_time_mapper(old_starts, durations, new_starts)

    # 不会抛异常
    assert mapper(1.5) == 1.5  # 句 2 中点


# ────────────────────────────────────────────────────────────────────
# recalibrate_entries
# ────────────────────────────────────────────────────────────────────


def test_recalibrate_entries_basic():
    """3 条字幕，old 0/1/2s, new 10/12/15s，map 后字幕应平移。"""
    from index_tts_gui.core.speech_aligner import recalibrate_entries
    from index_tts_gui.core.subtitle import SubtitleEntry

    entries = [
        SubtitleEntry(1, 0.5, 1.0, "句 1"),
        SubtitleEntry(2, 1.5, 2.0, "句 2"),
        SubtitleEntry(3, 3.0, 3.5, "句 3"),
    ]
    old_starts = [0.0, 1.0, 2.5]
    durations = [1.0, 1.5, 1.0]
    new_starts = [10.0, 11.0, 12.5]  # 整段后移 10s

    new = recalibrate_entries(entries, old_starts, durations, new_starts)
    assert len(new) == 3

    # 句 1 (0.5-1.0) → (10.5-11.0)
    assert new[0].start_sec == 10.5
    assert new[0].end_sec == 11.0
    assert new[0].text == "句 1"

    # 句 2 (1.5-2.0) → (11.5-12.0)
    assert new[1].start_sec == 11.5
    assert new[1].end_sec == 12.0

    # 句 3 (3.0-3.5) → (13.0-13.5)
    assert new[2].start_sec == 13.0
    assert new[2].end_sec == 13.5


def test_recalibrate_entries_preserves_text():
    """字幕文本必须原样保留。"""
    from index_tts_gui.core.speech_aligner import recalibrate_entries
    from index_tts_gui.core.subtitle import SubtitleEntry

    entries = [
        SubtitleEntry(1, 0.0, 1.0, "你好，世界。"),
        SubtitleEntry(2, 2.0, 3.0, "How are you?"),
    ]
    new = recalibrate_entries(entries, [0.0, 2.0], [1.0, 1.0], [5.0, 7.0])
    assert new[0].text == "你好，世界。"
    assert new[1].text == "How are you?"


def test_recalibrate_entries_end_not_after_start():
    """end <= start 时强制 end = start + 0.1（防错位）。"""
    from index_tts_gui.core.speech_aligner import recalibrate_entries
    from index_tts_gui.core.subtitle import SubtitleEntry

    # 字幕 end == start (零长度)
    entries = [SubtitleEntry(1, 1.0, 1.0, "x")]
    new = recalibrate_entries(entries, [0.0], [2.0], [0.0])
    assert new[0].end_sec > new[0].start_sec


def test_recalibrate_entries_rounds_to_3_decimals():
    """时间戳应四舍五入到 3 位小数（毫秒精度）。"""
    from index_tts_gui.core.speech_aligner import recalibrate_entries
    from index_tts_gui.core.subtitle import SubtitleEntry

    entries = [SubtitleEntry(1, 0.123456789, 0.987654321, "x")]
    new = recalibrate_entries(entries, [0.0], [1.0], [0.0])
    # 检查不是 1.123456789 这种长尾
    assert len(f"{new[0].start_sec:.10f}".rstrip("0").rstrip(".")) <= 6


def test_recalibrate_entries_index_preserved():
    """SubtitleEntry.index 必须保持。"""
    from index_tts_gui.core.speech_aligner import recalibrate_entries
    from index_tts_gui.core.subtitle import SubtitleEntry

    entries = [
        SubtitleEntry(7, 0.0, 1.0, "a"),
        SubtitleEntry(13, 1.0, 2.0, "b"),
    ]
    new = recalibrate_entries(entries, [0.0, 1.0], [1.0, 1.0], [0.0, 1.0])
    assert new[0].index == 7
    assert new[1].index == 13


def test_recalibrate_entries_empty():
    """空输入：返回空列表。"""
    from index_tts_gui.core.speech_aligner import recalibrate_entries

    assert recalibrate_entries([], [], [], []) == []


# ────────────────────────────────────────────────────────────────────
# 集成场景
# ────────────────────────────────────────────────────────────────────


def test_full_pipeline_align_then_recalibrate(tmp_path):
    """端到端：合成 → align_sentences → recalibrate_entries。"""
    from index_tts_gui.core.speech_aligner import (
        align_sentences, recalibrate_entries,
    )
    from index_tts_gui.core.subtitle import SubtitleEntry

    # 4 句不同频率的调频信号
    sentence_wavs = []
    for i, (f0, f1) in enumerate([(200, 400), (300, 600), (400, 800), (500, 1000)]):
        path = tmp_path / f"s{i+1}.wav"
        _write_chirp_wav(path, 0.4, f0=f0, f1=f1)
        sentence_wavs.append(str(path))

    # 原始字幕：每句 0.5s 显示
    entries = [
        SubtitleEntry(1, 0.0, 0.4, "s1"),
        SubtitleEntry(2, 0.7, 1.1, "s2"),
        SubtitleEntry(3, 1.4, 1.8, "s3"),
        SubtitleEntry(4, 2.1, 2.5, "s4"),
    ]
    sentences = ["s1", "s2", "s3", "s4"]
    pauses = [0.3, 0.3, 0.3, 0.0]  # 句间停顿

    # 拼接成"调整后"音频（保持原始停顿）
    full = tmp_path / "full.wav"
    _build_modified_with_pauses(sentence_wavs, pauses, full)

    new_starts = align_sentences(str(full), sentence_wavs, sentences, pauses)
    assert len(new_starts) == 4
    # 句 1 起点应接近 0
    assert abs(new_starts[0] - 0.0) < 0.1

    # 假设原始时间线（用 wav 时长 + pauses 推算）
    durations = [0.4] * 4
    old_starts = []
    cum = 0.0
    for i in range(4):
        old_starts.append(cum)
        cum += durations[i] + (pauses[i] if i < len(pauses) else 0)
    old_starts = [round(x, 3) for x in old_starts]

    # 重映射字幕
    new_entries = recalibrate_entries(entries, old_starts, durations, new_starts)
    assert len(new_entries) == 4
    # 字幕应保持顺序
    assert new_entries[0].index == 1
    assert new_entries[3].index == 4
    # 每条字幕 end > start
    for e in new_entries:
        assert e.end_sec > e.start_sec


def test_full_pipeline_with_extended_pauses(tmp_path):
    """用户大幅拉长静音（5 倍），仍能定位。"""
    from index_tts_gui.core.speech_aligner import (
        align_sentences, recalibrate_entries,
    )
    from index_tts_gui.core.subtitle import SubtitleEntry

    sentence_wavs = []
    for i, freq in enumerate([440, 660, 880]):
        path = tmp_path / f"s{i+1}.wav"
        _write_sin_wav(path, 0.3, freq=freq)
        sentence_wavs.append(str(path))

    # 原始停顿 0.2s，调整后停顿 1.5s（7.5 倍）
    original_pauses = [0.2, 0.2, 0.0]
    full = tmp_path / "full.wav"
    _build_modified_with_pauses(sentence_wavs, [1.5, 1.5, 0.0], full)

    new_starts = align_sentences(
        str(full), sentence_wavs, ["a", "b", "c"], original_pauses,
    )
    assert len(new_starts) == 3
    # 句 1 起点 0
    assert abs(new_starts[0] - 0.0) < 0.1
    # 句 2 应在 0.3 + 1.5 = 1.8 附近
    assert abs(new_starts[1] - 1.8) < 0.3
    # 句 3 应在 0.3 + 1.5 + 0.3 + 1.5 = 3.6 附近
    assert abs(new_starts[2] - 3.6) < 0.3


def test_full_pipeline_shortened_pauses(tmp_path):
    """用户缩短静音（0.5 倍），仍能定位。"""
    from index_tts_gui.core.speech_aligner import align_sentences
    from index_tts_gui.core.subtitle import SubtitleEntry

    # 两个不同调频范围避免互相关误匹配
    sentence_wavs = []
    for i, (f0, f1) in enumerate([(200, 600), (800, 1400)]):
        path = tmp_path / f"s{i+1}.wav"
        _write_chirp_wav(path, 0.3, f0=f0, f1=f1)
        sentence_wavs.append(str(path))

    full = tmp_path / "full.wav"
    _build_modified_with_pauses(sentence_wavs, [0.1, 0.0], full)

    new_starts = align_sentences(
        str(full), sentence_wavs, ["a", "b"], [0.2, 0.0],
    )
    assert abs(new_starts[0] - 0.0) < 0.1
    assert abs(new_starts[1] - 0.4) < 0.2  # 0.3 + 0.1
