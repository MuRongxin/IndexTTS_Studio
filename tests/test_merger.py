"""测试 merger 模块"""
import os
import shutil
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from index_tts_gui.core import merger

FFMPEG_AVAILABLE = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def _generate_sine_wav(
    path: str, duration: float, sample_rate: int = 16000, freq: int = 440
):
    """生成简单正弦波 WAV 文件，便于测试真实音频行为。"""
    import numpy as np
    from scipy.io import wavfile

    samples = int(sample_rate * duration)
    t = np.linspace(0.0, duration, samples, endpoint=False)
    data = (np.sin(2 * np.pi * freq * t) * 32767).astype(np.int16)
    wavfile.write(path, sample_rate, data)


@pytest.mark.skipif(not FFMPEG_AVAILABLE, reason="ffmpeg/ffprobe not installed")
def test_get_wav_duration_reads_real_wav(tmp_path):
    """get_wav_duration 应能正确读取真实生成的 WAV 时长。"""
    wav = str(tmp_path / "tone.wav")
    _generate_sine_wav(wav, duration=1.25)
    dur = merger.get_wav_duration(wav)
    assert dur == pytest.approx(1.25, abs=0.05)


@pytest.mark.skipif(not FFMPEG_AVAILABLE, reason="ffmpeg/ffprobe not installed")
def test_get_wav_duration_missing_file_raises(tmp_path):
    """输入文件不存在时 get_wav_duration 应抛出 RuntimeError。"""
    missing = str(tmp_path / "not_exist.wav")
    with pytest.raises(RuntimeError):
        merger.get_wav_duration(missing)


@pytest.mark.skipif(not FFMPEG_AVAILABLE, reason="ffmpeg/ffprobe not installed")
def test_merge_wavs_two_files_output_duration_is_sum(tmp_path):
    """merge_wavs 合并两段音频后，输出时长应约等于两者之和。"""
    a = str(tmp_path / "a.wav")
    b = str(tmp_path / "b.wav")
    out = str(tmp_path / "out.wav")
    _generate_sine_wav(a, 0.6)
    _generate_sine_wav(b, 0.8, freq=660)

    merger.merge_wavs([a, b], out)

    assert os.path.exists(out)
    assert merger.get_wav_duration(out) == pytest.approx(1.4, abs=0.1)


@pytest.mark.skipif(not FFMPEG_AVAILABLE, reason="ffmpeg/ffprobe not installed")
def test_merge_wavs_with_custom_pauses_inserts_silence(tmp_path):
    """merge_wavs_with_custom_pauses 应在片段之间插入指定时长的静音。"""
    a = str(tmp_path / "a.wav")
    b = str(tmp_path / "b.wav")
    out = str(tmp_path / "out.wav")
    _generate_sine_wav(a, 0.5)
    _generate_sine_wav(b, 0.5, freq=660)

    merger.merge_wavs_with_custom_pauses([a, b], [0.3, 0.0], out)

    assert os.path.exists(out)
    assert merger.get_wav_duration(out) == pytest.approx(1.3, abs=0.1)


@pytest.mark.skipif(not FFMPEG_AVAILABLE, reason="ffmpeg/ffprobe not installed")
def test_merge_wavs_with_pauses_uses_punctuation_rules(tmp_path):
    """merge_wavs_with_pauses 应根据句子标点计算句间停顿。"""
    a = str(tmp_path / "a.wav")
    b = str(tmp_path / "b.wav")
    out = str(tmp_path / "out.wav")
    _generate_sine_wav(a, 0.5)
    _generate_sine_wav(b, 0.5, freq=660)

    # "你好。" 结尾为句号，对应停顿 0.55s；最后一句无尾部停顿
    merger.merge_wavs_with_pauses([a, b], ["你好。", "测试"], out)

    assert os.path.exists(out)
    assert merger.get_wav_duration(out) == pytest.approx(1.55, abs=0.1)


def test_merge_wavs_empty_list_raises():
    """merge_wavs 传入空列表时应抛出 ValueError。"""
    with pytest.raises(ValueError, match="没有可合并的音频文件"):
        merger.merge_wavs([], "out.wav")


def test_merge_wavs_with_custom_pauses_mismatch_raises(tmp_path):
    """音频片段数量与停顿时长数量不一致时应抛出 ValueError。"""
    a = str(tmp_path / "a.wav")
    _generate_sine_wav(a, 0.5)
    with pytest.raises(ValueError, match="音频片段数量.*停顿数量.*不一致"):
        merger.merge_wavs_with_custom_pauses([a], [0.1, 0.2], "out.wav")


def test_merge_wavs_with_pauses_mismatch_raises(tmp_path):
    """音频片段数量与句子数量不一致时应抛出 ValueError。"""
    a = str(tmp_path / "a.wav")
    _generate_sine_wav(a, 0.5)
    with pytest.raises(ValueError, match="音频片段数量.*句子数量.*不一致"):
        merger.merge_wavs_with_pauses([a], ["一句", "两句"], "out.wav")


def test_parse_sentence_wav_name_valid():
    """parse_sentence_wav_name 应能正确解析标准 sentence_*.wav 文件名。"""
    assert merger.parse_sentence_wav_name("sentence_01.wav") == (1, "")
    assert merger.parse_sentence_wav_name("sentence_02_你好.wav") == (2, "你好")
    assert merger.parse_sentence_wav_name("sentence_12_hello_world.wav") == (
        12,
        "hello_world",
    )


def test_parse_sentence_wav_name_invalid():
    """非标准文件名应返回 None。"""
    assert merger.parse_sentence_wav_name("foo.wav") is None
    assert merger.parse_sentence_wav_name("sentence_01.txt") is None
    assert merger.parse_sentence_wav_name("sentence_xx.wav") is None
    assert merger.parse_sentence_wav_name("sentence_.wav") is None


def test_sanitize_for_filename():
    """sanitize_for_filename 应能清理特殊字符、截断超长文本并处理空字符串。"""
    assert merger.sanitize_for_filename("  hello world  ") == "hello_world"
    assert merger.sanitize_for_filename("a,b!c?") == "abc"
    assert merger.sanitize_for_filename("中文测试") == "中文测试"
    assert merger.sanitize_for_filename("") == "no_text"
    assert merger.sanitize_for_filename("!!!") == "no_text"
    long_text = "a" * 30
    assert merger.sanitize_for_filename(long_text, max_len=10) == "a" * 10


def test_collect_sentence_wavs_sorts_by_index(tmp_path):
    """collect_sentence_wavs 应按序号升序收集 sentence_*.wav 文件。"""
    names = [
        "sentence_02_b.wav",
        "sentence_10_z.wav",
        "sentence_01_a.wav",
        "other.txt",
    ]
    for name in names:
        (tmp_path / name).write_bytes(b"")

    result = merger.collect_sentence_wavs(str(tmp_path))

    assert [os.path.basename(p) for p in result] == [
        "sentence_01_a.wav",
        "sentence_02_b.wav",
        "sentence_10_z.wav",
    ]


def test_validate_wav_order_passes(tmp_path):
    """文件名中的文本与 sentences 一致时，校验应返回空错误列表。"""
    for name in ["sentence_01_你好.wav", "sentence_02_世界.wav"]:
        (tmp_path / name).write_bytes(b"")

    wavs = merger.collect_sentence_wavs(str(tmp_path))
    errors = merger.validate_wav_order(wavs, ["你好。", "世界。"])

    assert errors == []


def test_validate_wav_order_detects_text_mismatch(tmp_path):
    """文件名中的文本与当前句子不一致时应给出错误提示。"""
    (tmp_path / "sentence_01_你好.wav").write_bytes(b"")

    wavs = merger.collect_sentence_wavs(str(tmp_path))
    errors = merger.validate_wav_order(wavs, ["世界。"])

    assert len(errors) == 1
    assert "不匹配" in errors[0]


def test_validate_wav_order_detects_bad_format(tmp_path):
    """文件名不符合 sentence_*.wav 格式时应给出格式异常提示。"""
    (tmp_path / "bad_01.wav").write_bytes(b"")

    errors = merger.validate_wav_order([str(tmp_path / "bad_01.wav")], ["你好。"])

    assert len(errors) == 1
    assert "格式异常" in errors[0]
