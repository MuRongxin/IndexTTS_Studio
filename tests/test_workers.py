"""UI Worker 线程测试

覆盖 VoiceUploadWorker、SynthesisWorker、SingleSynthesisWorker、MergeWorker、SplitWorker。
重点验证信号生命周期：finished 触发后可以安全 deleteLater。
"""

import os
import shutil
import struct
import sys
import math
import time
import wave

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

# 在导入 PySide6 之前设置无头平台，避免 CI 环境没有显示器时失败
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication

from index_tts_gui.ui.voice_upload_worker import VoiceUploadWorker
from index_tts_gui.ui.synthesis_worker import SynthesisWorker
from index_tts_gui.ui.synthesis_panel import SingleSynthesisWorker
from index_tts_gui.ui.merge_worker import MergeWorker
from index_tts_gui.ui.split_worker import SplitWorker


FFMPEG_AVAILABLE = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


@pytest.fixture(scope="session", autouse=True)
def qapp():
    """确保整个测试会话有一个 QCoreApplication。"""
    app = QCoreApplication.instance()
    if app is None:
        app = QCoreApplication([])
    yield app


def _write_wav(path, duration, sample_rate=16000, freq=440):
    """生成单声道 16-bit PCM WAV 文件。"""
    path = str(path)
    n_samples = int(sample_rate * duration)
    with wave.open(path, "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(sample_rate)
        for i in range(n_samples):
            val = int(32767 * math.sin(2 * math.pi * freq * i / sample_rate))
            f.writeframes(struct.pack("<h", val))


def _wait_for_worker(worker, app, timeout_ms=5000):
    """等待 worker 线程结束并处理完信号。"""
    # 先清空事件队列中的残留信号
    app.processEvents()
    start = time.time()
    while worker.isRunning() and (time.time() - start) * 1000 < timeout_ms:
        app.processEvents()
        time.sleep(0.01)
    # 再处理几轮事件，确保信号槽全部投递完毕
    for _ in range(30):
        app.processEvents()
        time.sleep(0.01)


# ── VoiceUploadWorker ──

class _FakeUploadClient:
    def __init__(self, result=None, exc=None):
        self.result = result if result is not None else {"code": 200, "msg": "ok"}
        self.exc = exc
        self.calls = []

    def upload_audio(self, path: str):
        self.calls.append(path)
        if self.exc is not None:
            raise self.exc
        return self.result


def test_voice_upload_worker_success(tmp_path, qapp):
    """上传成功时应 emits started、success(audio_name)、finished。"""
    wav = tmp_path / "ref.wav"
    _write_wav(wav, 0.1)

    client = _FakeUploadClient()
    worker = VoiceUploadWorker(client, str(wav), "voice_a")

    started, success, errors, finished = [], [], [], []
    worker.started.connect(lambda: started.append(True))
    worker.success.connect(success.append)
    worker.error.connect(errors.append)
    worker.finished.connect(lambda: finished.append(True))

    worker.start()
    _wait_for_worker(worker, qapp)

    assert len(started) >= 1
    assert success == ["voice_a"]
    assert errors == []
    assert len(finished) >= 1
    assert client.calls == [str(wav)]

    worker.deleteLater()
    qapp.processEvents()


def test_voice_upload_worker_failure_response(tmp_path, qapp):
    """服务端返回非 200 时应 emit error 并附带 msg。"""
    wav = tmp_path / "ref.wav"
    _write_wav(wav, 0.1)

    client = _FakeUploadClient(result={"code": 500, "msg": "bad format"})
    worker = VoiceUploadWorker(client, str(wav), "voice_b")

    success, errors, finished = [], [], []
    worker.success.connect(success.append)
    worker.error.connect(errors.append)
    finished_count = [0]
    worker.finished.connect(lambda: finished_count.__setitem__(0, finished_count[0] + 1))

    worker.start()
    _wait_for_worker(worker, qapp)

    assert success == []
    assert finished_count[0] >= 1
    assert len(errors) == 1
    assert "bad format" in errors[0]


def test_voice_upload_worker_exception(tmp_path, qapp):
    """upload_audio 抛异常时应 emit error 并 finished。"""
    wav = tmp_path / "ref.wav"
    _write_wav(wav, 0.1)

    client = _FakeUploadClient(exc=RuntimeError("network down"))
    worker = VoiceUploadWorker(client, str(wav), "voice_c")

    success, errors, finished_count = [], [], [0]
    worker.success.connect(success.append)
    worker.error.connect(errors.append)
    worker.finished.connect(lambda: finished_count.__setitem__(0, finished_count[0] + 1))

    worker.start()
    _wait_for_worker(worker, qapp)

    assert success == []
    assert finished_count[0] >= 1
    assert len(errors) == 1
    assert "network down" in errors[0]


# ── SynthesisWorker ──

class _FakeSynthClient:
    def __init__(self, responses):
        """responses 是可迭代对象，元素为 bytes 或 Exception。"""
        self._responses = list(responses)
        self.calls = []

    def synthesize(self, text: str, audio_name: str):
        self.calls.append((text, audio_name))
        if not self._responses:
            raise RuntimeError("unexpected synthesize call")
        resp = self._responses.pop(0)
        if isinstance(resp, Exception):
            raise resp
        return resp


def test_synthesis_worker_multi_sentence(tmp_path, qapp):
    """多句正常合成：进度、sentence_done、finished(wav_map) 都正确。"""
    out_dir = tmp_path / "out"
    client = _FakeSynthClient([b"wav1", b"wav2"])
    worker = SynthesisWorker(
        sentences=["hello world", "第二句。"],
        audio_name="voice",
        output_dir=str(out_dir),
        client=client,
    )

    progress, done, logs, errors, finished_map = [], [], [], [], []
    worker.progress.connect(lambda c, t, s: progress.append((c, t, s)))
    worker.sentence_done.connect(lambda idx, path: done.append((idx, path)))
    worker.log.connect(logs.append)
    worker.error.connect(errors.append)
    worker.finished.connect(finished_map.append)

    worker.start()
    _wait_for_worker(worker, qapp)

    assert errors == []
    assert len(finished_map) == 1
    wav_map = finished_map[0]
    assert len(wav_map) == 2
    assert wav_map[0]["index"] == 0
    assert wav_map[1]["index"] == 1
    assert wav_map[0]["text"] == "hello world"
    assert len(done) == 2
    assert (out_dir / "sentence_01_hello_world.wav").exists()
    assert (out_dir / "sentence_02_第二句.wav").exists()
    assert progress[0] == (1, 2, "hello world")
    assert progress[-1] == (2, 2, "第二句。")


def test_synthesis_worker_cancel(tmp_path, qapp):
    """启动后立即 cancel，应提前结束且不抛 error。"""
    out_dir = tmp_path / "out"
    client = _FakeSynthClient([b"a", b"b", b"c"])
    worker = SynthesisWorker(
        sentences=["1", "2", "3"],
        audio_name="voice",
        output_dir=str(out_dir),
        client=client,
    )

    errors, finished_map = [], []
    worker.error.connect(errors.append)
    worker.finished.connect(finished_map.append)

    worker.start()
    worker.cancel()
    _wait_for_worker(worker, qapp)

    assert len(finished_map) == 1
    assert errors == []
    # 取消后合成的句数应少于 3
    assert len(finished_map[0]) < 3


def test_synthesis_worker_partial_failure(tmp_path, qapp):
    """其中一句失败时，应继续完成其他句并 finished 部分 wav_map。"""
    out_dir = tmp_path / "out"
    client = _FakeSynthClient([b"ok", RuntimeError("tts failed")])
    worker = SynthesisWorker(
        sentences=["hello", "world"],
        audio_name="voice",
        output_dir=str(out_dir),
        client=client,
    )

    done, errors, finished_map = [], [], []
    worker.sentence_done.connect(lambda idx, path: done.append((idx, path)))
    worker.error.connect(errors.append)
    worker.finished.connect(finished_map.append)

    worker.start()
    _wait_for_worker(worker, qapp)

    assert len(errors) == 1
    assert "tts failed" in errors[0]
    assert len(finished_map) == 1
    wav_map = finished_map[0]
    assert len(wav_map) == 1
    assert wav_map[0]["text"] == "hello"
    assert (out_dir / "sentence_01_hello.wav").exists()
    assert not (out_dir / "sentence_02_world.wav").exists()


# ── SingleSynthesisWorker ──

def test_single_synthesis_worker_success(tmp_path, qapp):
    """单句重新合成成功。"""
    out_dir = tmp_path / "out"
    out_dir.mkdir(parents=True, exist_ok=True)
    client = _FakeSynthClient([b"single"])
    worker = SingleSynthesisWorker(
        index=2,
        sentence="only one",
        audio_name="voice",
        output_dir=str(out_dir),
        client=client,
    )

    success, errors, logs = [], [], []
    worker.success.connect(lambda idx, path: success.append((idx, path)))
    worker.error.connect(lambda idx, msg: errors.append((idx, msg)))
    worker.log.connect(logs.append)

    worker.start()
    _wait_for_worker(worker, qapp)

    assert errors == []
    assert len(success) == 1
    assert success[0][0] == 2
    assert (out_dir / "sentence_03_only_one.wav").exists()


def test_single_synthesis_worker_failure(tmp_path, qapp):
    """单句重新合成失败。"""
    out_dir = tmp_path / "out"
    client = _FakeSynthClient([RuntimeError("bad")])
    worker = SingleSynthesisWorker(
        index=0,
        sentence="fail",
        audio_name="voice",
        output_dir=str(out_dir),
        client=client,
    )

    success, errors = [], []
    worker.success.connect(lambda idx, path: success.append((idx, path)))
    worker.error.connect(lambda idx, msg: errors.append((idx, msg)))

    worker.start()
    _wait_for_worker(worker, qapp)

    assert success == []
    assert len(errors) == 1
    assert errors[0][0] == 0
    assert "bad" in errors[0][1]


# ── MergeWorker ──

@pytest.mark.skipif(not FFMPEG_AVAILABLE, reason="需要系统安装 ffmpeg 与 ffprobe")
def test_merge_worker_success(tmp_path, qapp):
    """本地 WAV 合并流程：生成 full_dub.wav 与字幕条目。"""
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    _write_wav(out_dir / "sentence_01_hello.wav", 0.5, freq=440)
    _write_wav(out_dir / "sentence_02_world.wav", 0.4, freq=880)

    worker = MergeWorker(
        sentences=["hello", "world"],
        output_dir=str(out_dir),
        llm_cfg={},
        pauses=[0.1, 0.0],
    )

    log, progress, finished_entries, errors = [], [], [], []
    worker.log.connect(log.append)
    worker.progress.connect(lambda c, t, m: progress.append((c, t, m)))
    worker.finished.connect(finished_entries.append)
    worker.error.connect(errors.append)

    worker.start()
    _wait_for_worker(worker, qapp)

    assert errors == []
    assert len(finished_entries) == 1
    entries = finished_entries[0]
    assert len(entries) == 2
    assert entries[0].text == "hello"
    assert entries[1].text == "world"
    assert entries[0].start_sec < entries[1].start_sec

    full = out_dir / "full_dub.wav"
    assert full.exists()

    from index_tts_gui.core.merger import get_wav_duration
    dur = get_wav_duration(str(full))
    # 0.5 + 0.1(pause) + 0.4
    assert dur == pytest.approx(1.0, abs=0.05)


def test_merge_worker_empty_output(tmp_path, qapp):
    """输出目录没有 sentence_*.wav 时应 emit error。"""
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    worker = MergeWorker(
        sentences=["hello"],
        output_dir=str(out_dir),
        llm_cfg={},
        pauses=[0.0],
    )

    finished_entries, errors = [], []
    worker.finished.connect(finished_entries.append)
    worker.error.connect(errors.append)

    worker.start()
    _wait_for_worker(worker, qapp)

    assert finished_entries == []
    assert len(errors) == 1
    assert "未找到" in errors[0]


def test_merge_worker_count_mismatch(tmp_path, qapp):
    """音频文件数与句子数不一致时应 emit error。"""
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    _write_wav(out_dir / "sentence_01_hello.wav", 0.2)

    worker = MergeWorker(
        sentences=["hello", "world"],
        output_dir=str(out_dir),
        llm_cfg={},
        pauses=[0.0, 0.0],
    )

    errors = []
    worker.error.connect(errors.append)

    worker.start()
    _wait_for_worker(worker, qapp)

    assert len(errors) == 1
    assert "不一致" in errors[0]


def test_merge_worker_validation_error(tmp_path, qapp):
    """文件名中的文本与当前句子不一致时应 emit error。"""
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    _write_wav(out_dir / "sentence_01_hello.wav", 0.2)

    worker = MergeWorker(
        sentences=["world"],
        output_dir=str(out_dir),
        llm_cfg={},
        pauses=[0.0],
    )

    errors = []
    worker.error.connect(errors.append)

    worker.start()
    _wait_for_worker(worker, qapp)

    assert len(errors) == 1
    assert "不匹配" in errors[0]


@pytest.mark.skipif(not FFMPEG_AVAILABLE, reason="需要系统安装 ffmpeg 与 ffprobe")
def test_merge_worker_cancel(tmp_path, qapp):
    """合并启动后立即 cancel，不应 crash，也不应 emit finished/error。"""
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    _write_wav(out_dir / "sentence_01_hello.wav", 0.5)
    _write_wav(out_dir / "sentence_02_world.wav", 0.5)

    worker = MergeWorker(
        sentences=["hello", "world"],
        output_dir=str(out_dir),
        llm_cfg={},
        pauses=[0.0, 0.0],
    )

    finished_entries, errors = [], []
    worker.finished.connect(finished_entries.append)
    worker.error.connect(errors.append)

    worker.start()
    worker.cancel()
    _wait_for_worker(worker, qapp)

    assert not worker.isRunning()
    # 取消后不一定有信号，重点是没抛异常


# ── SplitWorker ──

def test_split_worker_rule_mode(tmp_path, qapp):
    """规则拆分模式：直接返回拆分结果，used_llm=False。"""
    worker = SplitWorker(
        text="你好。世界。",
        mode="rule",
        llm_cfg={},
        max_length=0,
    )

    started, finished = [], []
    worker.started.connect(lambda: started.append(True))
    worker.finished.connect(lambda s, u, m: finished.append((s, u, m)))

    worker.start()
    _wait_for_worker(worker, qapp)

    assert len(started) >= 1
    assert len(finished) >= 1
    sentences, used_llm, msg = finished[0]
    assert sentences == ["你好。", "世界。"]
    assert used_llm is False
    assert "规则拆分完成" in msg


def test_split_worker_auto_fallback(tmp_path, qapp):
    """auto 模式无有效 LLM 配置时回退规则拆分。"""
    worker = SplitWorker(
        text="今天天气不错。我们出去吧。",
        mode="auto",
        llm_cfg={},
        max_length=0,
    )

    finished = []
    worker.finished.connect(lambda s, u, m: finished.append((s, u, m)))

    worker.start()
    _wait_for_worker(worker, qapp)

    assert len(finished) == 1
    sentences, used_llm, msg = finished[0]
    assert len(sentences) == 2
    assert used_llm is False
    assert "回退" in msg or "LLM 未配置" in msg


# ── 生命周期清理 ──

def test_worker_lifecycle_cleanup(tmp_path, qapp):
    """worker finished 后可以 deleteLater，不会访问悬空资源。"""
    wav = tmp_path / "ref.wav"
    _write_wav(wav, 0.1)

    client = _FakeUploadClient()
    worker = VoiceUploadWorker(client, str(wav), "lifecycle")

    finished = []
    worker.finished.connect(lambda: finished.append(True))

    worker.start()
    _wait_for_worker(worker, qapp)

    assert finished

    worker.deleteLater()
    for _ in range(30):
        qapp.processEvents()
        time.sleep(0.01)

    # 线程已结束即可，QObject deleteLater 会在事件循环中处理
    assert not worker.isRunning()
