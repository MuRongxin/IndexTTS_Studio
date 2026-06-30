"""Subtitler 模块测试

优先使用真实 WAV 文件验证时长计算，ffprobe 不可用时自动跳过相关用例。
长句切分的文本逻辑也提供不依赖音频的独立测试。
"""

import math
import shutil
import struct
import wave
from pathlib import Path

import pytest

from index_tts_gui.core.subtitle import SubtitleEntry
from index_tts_gui.core.subtitler import (
    _split_by_pauses,
    _split_manuscript,
    entries_to_srt,
    generate_srt,
    generate_srt_from_sentences,
    generate_srt_from_sentences_with_pauses,
)

FFPROBE_AVAILABLE = shutil.which("ffprobe") is not None

SAMPLE_RATE = 22050


def _build_wav(path: Path, segments: list[tuple[str, float, float | None]]):
    """生成简单 WAV 文件。

    segments 每项为 (kind, duration, freq)。kind 为 'tone' 或 'silence'，
    freq 仅在 tone 时使用。
    """
    frames = b""
    for kind, duration, freq in segments:
        n = int(SAMPLE_RATE * duration)
        if kind == "tone":
            samples = [
                int(0.5 * 32767 * math.sin(2 * math.pi * freq * i / SAMPLE_RATE))
                for i in range(n)
            ]
            frames += struct.pack("<" + "h" * len(samples), *samples)
        else:  # silence
            frames += b"\x00" * (n * 2)

    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(frames)


def test_entries_to_srt_empty():
    assert entries_to_srt([]) == ""


def test_entries_to_srt_format_and_negative_clamping():
    entries = [
        SubtitleEntry(1, -1.0, 2.0, "你好。"),
        SubtitleEntry(2, 2.0, 3.1234, "再见。"),
    ]
    srt = entries_to_srt(entries)

    lines = srt.splitlines()
    assert lines[0] == "1"
    assert lines[1] == "00:00:00,000 --> 00:00:02,000"
    assert lines[2] == "你好。"
    assert lines[3] == ""
    assert lines[4] == "2"
    assert lines[5] == "00:00:02,000 --> 00:00:03,123"
    assert lines[6] == "再见。"
    assert srt.endswith("\n")


def test_split_manuscript_simple():
    text = "你好。这是测试！真的吗？"
    result = _split_manuscript(text)
    assert result == ["你好。", "这是测试！", "真的吗？"]


def test_split_manuscript_newlines_become_spaces():
    text = "第一句。\n\n第二句。"
    result = _split_manuscript(text)
    assert result == ["第一句。", "第二句。"]


def test_split_manuscript_merges_continuation_after_quote():
    # 引号内的句子被标点切开后，后续无标点的延续文本应合并回前一句
    text = '他说：“你好。”然后走了。'
    result = _split_manuscript(text)
    assert len(result) == 1
    assert "你好" in result[0]
    assert "然后走了" in result[0]


@pytest.mark.skipif(not FFPROBE_AVAILABLE, reason="ffprobe 未安装")
def test_generate_srt_from_sentences_basic_with_real_wav(tmp_path: Path):
    wav1 = tmp_path / "s1.wav"
    wav2 = tmp_path / "s2.wav"
    _build_wav(wav1, [("tone", 1.0, 440)])
    _build_wav(wav2, [("tone", 1.5, 880)])

    entries = generate_srt_from_sentences(
        ["你好。", "世界。"], [str(wav1), str(wav2)]
    )

    assert len(entries) == 2
    assert entries[0].index == 1
    assert entries[0].text == "你好。"
    assert entries[0].start_sec == pytest.approx(0.0, abs=0.01)
    assert entries[0].end_sec == pytest.approx(1.0, abs=0.01)
    assert entries[1].index == 2
    assert entries[1].text == "世界。"
    assert entries[1].start_sec == pytest.approx(1.0, abs=0.01)
    assert entries[1].end_sec == pytest.approx(2.5, abs=0.01)


@pytest.mark.skipif(not FFPROBE_AVAILABLE, reason="ffprobe 未安装")
def test_generate_srt_from_sentences_with_pauses_and_real_wav(tmp_path: Path):
    wav1 = tmp_path / "s1.wav"
    wav2 = tmp_path / "s2.wav"
    _build_wav(wav1, [("tone", 1.0, 440)])
    _build_wav(wav2, [("tone", 1.5, 880)])

    entries = generate_srt_from_sentences_with_pauses(
        ["第一句。", "第二句。"],
        [str(wav1), str(wav2)],
        pauses=[0.5, 0.0],
    )

    assert len(entries) == 2
    # 第二句的开始时间 = 第一句时长 + 第一句后的停顿
    assert entries[1].start_sec == pytest.approx(1.5, abs=0.01)
    assert entries[1].end_sec == pytest.approx(3.0, abs=0.01)


@pytest.mark.skipif(not FFPROBE_AVAILABLE, reason="ffprobe 未安装")
def test_generate_srt_from_manuscript(tmp_path: Path):
    wav1 = tmp_path / "s1.wav"
    wav2 = tmp_path / "s2.wav"
    _build_wav(wav1, [("tone", 1.0, 440)])
    _build_wav(wav2, [("tone", 1.5, 880)])

    entries = generate_srt(
        "你好。世界。",
        [str(wav1), str(wav2)],
    )

    assert len(entries) == 2
    assert [e.text for e in entries] == ["你好。", "世界。"]
    assert entries[0].end_sec == pytest.approx(1.0, abs=0.01)
    assert entries[1].start_sec == pytest.approx(1.0, abs=0.01)


def test_generate_srt_from_sentences_with_stub_durations(
    tmp_path: Path, monkeypatch,
):
    """不依赖 ffprobe，仅验证时间轴累加逻辑。"""
    wav1 = tmp_path / "s1.wav"
    wav2 = tmp_path / "s2.wav"
    # 文件本身不需要真实音频内容
    wav1.write_text("dummy")
    wav2.write_text("dummy")

    stub_durations = {str(wav1): 1.2, str(wav2): 0.8}

    def _fake_duration(path: str) -> float:
        return stub_durations.get(path, 0.0)

    monkeypatch.setattr(
        "index_tts_gui.core.subtitler.get_wav_duration", _fake_duration
    )

    entries = generate_srt_from_sentences_with_pauses(
        ["A", "B"],
        [str(wav1), str(wav2)],
        pauses=[0.3, 0.0],
    )

    assert len(entries) == 2
    assert entries[0].start_sec == pytest.approx(0.0, abs=0.001)
    assert entries[0].end_sec == pytest.approx(1.2, abs=0.001)
    assert entries[1].start_sec == pytest.approx(1.5, abs=0.001)
    assert entries[1].end_sec == pytest.approx(2.3, abs=0.001)


@pytest.mark.skipif(not FFPROBE_AVAILABLE, reason="ffprobe 未安装")
def test_long_sentence_split_by_detected_pauses(tmp_path: Path):
    # 0.4s 有声 + 0.3s 静音 + 0.4s 有声，总时长约 1.1s
    wav = tmp_path / "long.wav"
    _build_wav(
        wav,
        [
            ("tone", 0.4, 440),
            ("silence", 0.3, None),
            ("tone", 0.4, 440),
        ],
    )

    sentence = "一二三四五六七八九十一二三四五六七八九十"  # 20 字
    entries = generate_srt_from_sentences(
        [sentence], [str(wav)], max_chars=10
    )

    assert len(entries) > 1
    assert "".join(e.text for e in entries) == sentence
    assert all(len(e.text) <= 10 for e in entries)
    assert entries[0].start_sec == pytest.approx(0.0, abs=0.01)
    assert entries[-1].end_sec == pytest.approx(1.1, abs=0.02)


def test_split_by_pauses_hard_split_exceeds_max_chars():
    """直接测试长句切分策略，不依赖音频能量检测。"""
    sentence = "一二三四五六七八九十一二三四五六七八九十"  # 20 字
    # 在正中间假装检测到一个停顿
    chunks = _split_by_pauses(sentence, [0.5], max_chars=10)

    assert "".join(chunks) == sentence
    assert all(len(c) <= 10 for c in chunks)


def test_split_by_pauses_no_pauses_returns_whole_sentence():
    sentence = "一二三四五六七八九十"
    assert _split_by_pauses(sentence, [], max_chars=5) == [sentence]


def test_generate_srt_from_sentences_empty():
    assert generate_srt_from_sentences([], []) == []
    assert generate_srt_from_sentences_with_pauses([], [], []) == []


@pytest.mark.skipif(not FFPROBE_AVAILABLE, reason="ffprobe 未安装")
def test_generate_srt_from_sentences_missing_wav_raises(tmp_path: Path):
    missing = str(tmp_path / "not_exists.wav")
    with pytest.raises(RuntimeError):
        generate_srt_from_sentences(["你好。"], [missing])
