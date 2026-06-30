"""测试音频变速模块 audio_speed"""
import array
import math
import os
import shutil
import sys
import wave

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from index_tts_gui.core.audio_speed import change_audio_speed


FFMPEG_AVAILABLE = shutil.which("ffmpeg") is not None


def _make_test_wav(path: str, duration_sec: float = 2.0, sample_rate: int = 16000) -> None:
    """生成一段简单的正弦波 WAV 文件。"""
    nframes = int(sample_rate * duration_sec)
    samples = array.array(
        "h",
        (int(32767 * math.sin(2 * math.pi * 440 * i / sample_rate)) for i in range(nframes)),
    )
    with wave.open(path, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.setnframes(nframes)
        wav.writeframes(samples.tobytes())


def _wav_duration(path: str) -> float:
    """读取 WAV 文件时长（秒）。"""
    with wave.open(path, "rb") as wav:
        return wav.getnframes() / wav.getframerate()


@pytest.fixture
def sample_wav(tmp_path):
    """提供一个 2 秒长的测试 WAV 文件路径。"""
    wav_path = str(tmp_path / "input.wav")
    _make_test_wav(wav_path, duration_sec=2.0)
    return wav_path


@pytest.mark.skipif(not FFMPEG_AVAILABLE, reason="未安装 ffmpeg")
def test_change_audio_speed_half(sample_wav, tmp_path):
    """0.5x 变速后输出文件存在，时长约为原时长 2 倍。"""
    output = str(tmp_path / "output_0_5.wav")
    change_audio_speed(sample_wav, output, 0.5)

    assert os.path.exists(output)
    assert _wav_duration(output) == pytest.approx(4.0, abs=0.1)


@pytest.mark.skipif(not FFMPEG_AVAILABLE, reason="未安装 ffmpeg")
def test_change_audio_speed_normal(sample_wav, tmp_path):
    """1.0x 变速后输出文件存在，时长基本保持不变。"""
    output = str(tmp_path / "output_1_0.wav")
    change_audio_speed(sample_wav, output, 1.0)

    assert os.path.exists(output)
    assert _wav_duration(output) == pytest.approx(2.0, abs=0.05)


@pytest.mark.skipif(not FFMPEG_AVAILABLE, reason="未安装 ffmpeg")
def test_change_audio_speed_one_and_half(sample_wav, tmp_path):
    """1.5x 变速后输出文件存在，时长约为原时长 2/3。"""
    output = str(tmp_path / "output_1_5.wav")
    change_audio_speed(sample_wav, output, 1.5)

    assert os.path.exists(output)
    assert _wav_duration(output) == pytest.approx(2.0 / 1.5, abs=0.05)


@pytest.mark.skipif(not FFMPEG_AVAILABLE, reason="未安装 ffmpeg")
def test_change_audio_speed_double(sample_wav, tmp_path):
    """2.0x 变速后输出文件存在，时长约为原时长的一半。"""
    output = str(tmp_path / "output_2_0.wav")
    change_audio_speed(sample_wav, output, 2.0)

    assert os.path.exists(output)
    assert _wav_duration(output) == pytest.approx(1.0, abs=0.05)


def test_change_audio_speed_below_min_raises():
    """速度倍率低于 0.5 时应抛出 ValueError。"""
    with pytest.raises(ValueError, match="速度倍率必须在 0.5~2.0 之间"):
        change_audio_speed("dummy.wav", "out.wav", 0.49)


def test_change_audio_speed_above_max_raises():
    """速度倍率高于 2.0 时应抛出 ValueError。"""
    with pytest.raises(ValueError, match="速度倍率必须在 0.5~2.0 之间"):
        change_audio_speed("dummy.wav", "out.wav", 2.01)


@pytest.mark.skipif(not FFMPEG_AVAILABLE, reason="未安装 ffmpeg")
def test_change_audio_speed_missing_input_raises(tmp_path):
    """输入文件不存在时应抛出 RuntimeError。"""
    missing = str(tmp_path / "not_exist.wav")
    output = str(tmp_path / "out.wav")
    with pytest.raises(RuntimeError):
        change_audio_speed(missing, output, 1.0)
