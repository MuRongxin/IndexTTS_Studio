"""CalibrateWorker 单元测试

覆盖：
1. 正常流程：emit log/progress/finished 信号
2. 取消机制：cancel() 后 worker 提前返回
3. 错误处理：缺 sentence WAV / 数量不匹配
4. 信号生命周期：finished 触发后可以安全 deleteLater
5. pauses 长度处理
"""
import os
import struct
import sys
import math
import time
import wave
import shutil
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

# 必须在导入 PySide6 之前
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from index_tts_gui.core.subtitle import SubtitleEntry
from index_tts_gui.ui.calibrate_worker import CalibrateWorker


FFMPEG_AVAILABLE = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


@pytest.fixture(scope="session", autouse=True)
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    yield app


def _write_sin_wav(path, duration, sr=16000, freq=440):
    n = int(sr * duration)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        for i in range(n):
            val = int(32767 * math.sin(2 * math.pi * freq * i / sr))
            w.writeframes(struct.pack("<h", val))


def _write_silence_wav(path, duration, sr=16000):
    n = int(sr * duration)
    silence = bytes(n * 2)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(silence)


def _build_full_with_pauses(sentence_wavs, pauses, out_path, sr=16000):
    """拼接 sentence WAV + 静音，模拟"调整后"音频。"""
    parts = []
    for i, p in enumerate(sentence_wavs):
        with wave.open(str(p), "rb") as f:
            n = f.getnframes()
            parts.append(f.readframes(n))
        if i < len(sentence_wavs) - 1 and i < len(pauses):
            silence_n = int(pauses[i] * sr)
            parts.append(bytes(silence_n * 2))
    with wave.open(str(out_path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(sr)
        f.writeframes(b"".join(parts))


def _wait_for_worker(worker, app, timeout_ms=10000):
    start = time.time()
    while worker.isRunning() and (time.time() - start) * 1000 < timeout_ms:
        app.processEvents()
        time.sleep(0.01)
    for _ in range(20):
        app.processEvents()
        time.sleep(0.01)


def _make_project(tmp_path, n_sentences, durations, pauses, modified_pauses):
    """建立测试工程：output_dir 里放 sentence_XX_*.wav，full.wav 用 modified_pauses 拼接。"""
    output_dir = tmp_path / "output_tts"
    output_dir.mkdir()
    sentence_wavs = []
    for i in range(n_sentences):
        path = output_dir / f"sentence_{i+1:02d}_s{i+1}.wav"
        _write_sin_wav(path, durations[i], freq=440 + i * 100)
        sentence_wavs.append(str(path))

    full = output_dir / "full_dub.wav"
    _build_full_with_pauses(sentence_wavs, modified_pauses, full)

    sentences = [f"句{i+1}" for i in range(n_sentences)]

    # 原始字幕：每条字幕覆盖一个句子的时长
    entries = []
    cum = 0.0
    for i in range(n_sentences):
        entries.append(SubtitleEntry(
            i + 1, cum, cum + durations[i], f"句{i+1}"
        ))
        cum += durations[i]
        if i < len(pauses):
            cum += pauses[i]

    return str(full), sentence_wavs, sentences, pauses, entries


# ────────────────────────────────────────────────────────────────────
# 正常流程
# ────────────────────────────────────────────────────────────────────


@pytest.mark.skipif(not FFMPEG_AVAILABLE, reason="ffmpeg/ffprobe 不可用")
def test_calibrate_success_emits_signals(tmp_path, qapp):
    """正常流程：log/progress/finished 信号全部触发，entries 长度正确。"""
    full, sentence_wavs, sentences, pauses, entries = _make_project(
        tmp_path, n_sentences=3, durations=[0.4, 0.4, 0.4],
        pauses=[0.2, 0.2, 0.0], modified_pauses=[0.2, 0.2, 0.0],
    )

    worker = CalibrateWorker(
        modified_wav_path=full,
        sentences=sentences,
        output_dir=str(tmp_path / "output_tts"),
        original_pauses=pauses,
        current_entries=entries,
    )
    logs, progress, finished, errors = [], [], [], []
    worker.log.connect(logs.append)
    worker.progress.connect(lambda c, t, m: progress.append((c, t, m)))
    worker.finished.connect(finished.append)
    worker.error.connect(errors.append)

    worker.start()
    _wait_for_worker(worker, qapp)

    assert errors == [], f"不应有 error 信号: {errors}"
    assert len(finished) == 1
    result_entries = finished[0]
    assert len(result_entries) == 3
    # 字幕 end > start
    for e in result_entries:
        assert e.end_sec > e.start_sec
    # log 至少包含"开始校准"和"校准完成"
    assert any("开始校准" in log for log in logs)
    assert any("校准完成" in log for log in logs)
    # progress 3 步
    assert len(progress) == 3
    assert progress[0][0] == 1
    assert progress[-1][0] == 3

    worker.deleteLater()
    qapp.processEvents()


@pytest.mark.skipif(not FFMPEG_AVAILABLE, reason="ffmpeg/ffprobe 不可用")
def test_calibrate_extended_pauses(tmp_path, qapp):
    """用户拉长静音：worker 完成且 entries 时间戳变化。"""
    full, sentence_wavs, sentences, pauses, entries = _make_project(
        tmp_path, n_sentences=3, durations=[0.4, 0.4, 0.4],
        pauses=[0.2, 0.2, 0.0], modified_pauses=[1.5, 1.5, 0.0],
    )

    worker = CalibrateWorker(
        modified_wav_path=full,
        sentences=sentences,
        output_dir=str(tmp_path / "output_tts"),
        original_pauses=pauses,
        current_entries=entries,
    )
    finished, errors = [], []
    worker.finished.connect(finished.append)
    worker.error.connect(errors.append)

    worker.start()
    _wait_for_worker(worker, qapp)

    assert errors == []
    result = finished[0]
    # 句 1 字幕起始应接近 0
    assert abs(result[0].start_sec - 0.0) < 0.2
    # 句 2 字幕起始应在 0.4 + 1.5 = 1.9 附近
    assert abs(result[1].start_sec - 1.9) < 0.4

    worker.deleteLater()
    qapp.processEvents()


# ────────────────────────────────────────────────────────────────────
# 错误处理
# ────────────────────────────────────────────────────────────────────


def test_calibrate_no_sentence_wav_errors(tmp_path, qapp):
    """output_dir 为空（无 sentence_*.wav）：emit error，不 emit finished。"""
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()

    worker = CalibrateWorker(
        modified_wav_path=str(tmp_path / "fake.wav"),
        sentences=["句1", "句2"],
        output_dir=str(empty_dir),
        original_pauses=[0.0, 0.0],
        current_entries=[
            SubtitleEntry(1, 0.0, 1.0, "句1"),
            SubtitleEntry(2, 1.0, 2.0, "句2"),
        ],
    )
    finished, errors = [], []
    worker.finished.connect(finished.append)
    worker.error.connect(errors.append)

    worker.start()
    _wait_for_worker(worker, qapp)

    assert finished == []
    assert len(errors) == 1
    assert "sentence_*.wav" in errors[0] or "未找到" in errors[0]

    worker.deleteLater()
    qapp.processEvents()


def test_calibrate_count_mismatch_errors(tmp_path, qapp):
    """句子数与 sentence WAV 数不匹配：emit error。"""
    output_dir = tmp_path / "output_tts"
    output_dir.mkdir()
    # 1 个 sentence WAV，但传入 3 个句子
    _write_sin_wav(output_dir / "sentence_01_a.wav", 0.5, freq=440)

    worker = CalibrateWorker(
        modified_wav_path=str(output_dir / "fake.wav"),
        sentences=["句1", "句2", "句3"],
        output_dir=str(output_dir),
        original_pauses=[0.0, 0.0, 0.0],
        current_entries=[
            SubtitleEntry(i + 1, float(i), float(i + 1), f"句{i+1}")
            for i in range(3)
        ],
    )
    finished, errors = [], []
    worker.finished.connect(finished.append)
    worker.error.connect(errors.append)

    worker.start()
    _wait_for_worker(worker, qapp)

    assert finished == []
    assert len(errors) == 1
    assert "不一致" in errors[0]

    worker.deleteLater()
    qapp.processEvents()


# ────────────────────────────────────────────────────────────────────
# 取消机制
# ────────────────────────────────────────────────────────────────────


@pytest.mark.skipif(not FFMPEG_AVAILABLE, reason="ffmpeg/ffprobe 不可用")
def test_calibrate_cancel_stops_worker(tmp_path, qapp):
    """启动后立即 cancel：worker 不抛异常，可能不 emit finished。"""
    full, sentence_wavs, sentences, pauses, entries = _make_project(
        tmp_path, n_sentences=3, durations=[0.4, 0.4, 0.4],
        pauses=[0.2, 0.2, 0.0], modified_pauses=[0.2, 0.2, 0.0],
    )

    worker = CalibrateWorker(
        modified_wav_path=full,
        sentences=sentences,
        output_dir=str(tmp_path / "output_tts"),
        original_pauses=pauses,
        current_entries=entries,
    )
    finished, errors = [], []
    worker.finished.connect(finished.append)
    worker.error.connect(errors.append)

    worker.start()
    worker.cancel()  # 立即取消
    _wait_for_worker(worker, qapp)

    # 取消后不应有 error（cancel 是正常路径）
    assert errors == []
    # 取消后 worker 已结束
    assert not worker.isRunning()
    # finished 可能 emit（如果已经进入 finished 分支）也可能不 emit
    # 都属于可接受行为

    worker.deleteLater()
    qapp.processEvents()


# ────────────────────────────────────────────────────────────────────
# pauses 处理
# ────────────────────────────────────────────────────────────────────


@pytest.mark.skipif(not FFMPEG_AVAILABLE, reason="ffmpeg/ffprobe 不可用")
def test_calibrate_empty_pauses_uses_zero(tmp_path, qapp):
    """pauses 为空列表：用全 0 兜底，正常完成。"""
    full, sentence_wavs, sentences, pauses, entries = _make_project(
        tmp_path, n_sentences=2, durations=[0.4, 0.4],
        pauses=[0.2, 0.0], modified_pauses=[0.2, 0.0],
    )

    worker = CalibrateWorker(
        modified_wav_path=full,
        sentences=sentences,
        output_dir=str(tmp_path / "output_tts"),
        original_pauses=[],  # 空
        current_entries=entries,
    )
    finished, errors = [], []
    worker.finished.connect(finished.append)
    worker.error.connect(errors.append)

    worker.start()
    _wait_for_worker(worker, qapp)

    assert errors == []
    assert len(finished) == 1
    assert len(finished[0]) == 2

    worker.deleteLater()
    qapp.processEvents()


@pytest.mark.skipif(not FFMPEG_AVAILABLE, reason="ffmpeg/ffprobe 不可用")
def test_calibrate_short_pauses_padded_with_zero(tmp_path, qapp):
    """pauses 比 sentences 短：补 0 兜底。"""
    full, sentence_wavs, sentences, pauses, entries = _make_project(
        tmp_path, n_sentences=3, durations=[0.4, 0.4, 0.4],
        pauses=[0.2, 0.2, 0.0], modified_pauses=[0.2, 0.2, 0.0],
    )

    worker = CalibrateWorker(
        modified_wav_path=full,
        sentences=sentences,
        output_dir=str(tmp_path / "output_tts"),
        original_pauses=[0.2],  # 只 1 个，sentences 有 3 个
        current_entries=entries,
    )
    finished, errors = [], []
    worker.finished.connect(finished.append)
    worker.error.connect(errors.append)

    worker.start()
    _wait_for_worker(worker, qapp)

    assert errors == []
    assert len(finished) == 1
    assert len(finished[0]) == 3

    worker.deleteLater()
    qapp.processEvents()


# ────────────────────────────────────────────────────────────────────
# 生命周期
# ────────────────────────────────────────────────────────────────────


@pytest.mark.skipif(not FFMPEG_AVAILABLE, reason="ffmpeg/ffprobe 不可用")
def test_calibrate_lifecycle_deleteLater_safe(tmp_path, qapp):
    """finished 触发后 deleteLater 不应崩溃。"""
    full, sentence_wavs, sentences, pauses, entries = _make_project(
        tmp_path, n_sentences=2, durations=[0.4, 0.4],
        pauses=[0.2, 0.0], modified_pauses=[0.2, 0.0],
    )

    worker = CalibrateWorker(
        modified_wav_path=full,
        sentences=sentences,
        output_dir=str(tmp_path / "output_tts"),
        original_pauses=pauses,
        current_entries=entries,
    )
    finished = []
    worker.finished.connect(finished.append)

    worker.start()
    _wait_for_worker(worker, qapp)

    assert len(finished) == 1
    worker.deleteLater()
    # 处理事件让 deleteLater 生效
    for _ in range(20):
        qapp.processEvents()
        time.sleep(0.01)
    # 不应崩溃
    assert not worker.isRunning()


# ────────────────────────────────────────────────────────────────────
# progress 信号格式
# ────────────────────────────────────────────────────────────────────


@pytest.mark.skipif(not FFMPEG_AVAILABLE, reason="ffmpeg/ffprobe 不可用")
def test_calibrate_progress_signal_format(tmp_path, qapp):
    """progress 信号应按 (current, total, message) 格式 emit 3 次。"""
    full, sentence_wavs, sentences, pauses, entries = _make_project(
        tmp_path, n_sentences=2, durations=[0.4, 0.4],
        pauses=[0.2, 0.0], modified_pauses=[0.2, 0.0],
    )

    worker = CalibrateWorker(
        modified_wav_path=full,
        sentences=sentences,
        output_dir=str(tmp_path / "output_tts"),
        original_pauses=pauses,
        current_entries=entries,
    )
    progress = []
    worker.progress.connect(lambda c, t, m: progress.append((c, t, m)))

    worker.start()
    _wait_for_worker(worker, qapp)

    assert len(progress) == 3
    # 每步的 current 应该是 1, 2, 3
    currents = [p[0] for p in progress]
    assert currents == [1, 2, 3]
    # total 都是 3
    assert all(p[1] == 3 for p in progress)
    # message 是字符串
    assert all(isinstance(p[2], str) and p[2] for p in progress)

    worker.deleteLater()
    qapp.processEvents()
