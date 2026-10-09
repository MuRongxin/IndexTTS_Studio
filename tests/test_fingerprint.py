"""声学指纹测试 —— 覆盖计算、量化往返、落盘、以及"跨重新合成"定位。

核心场景：用户增量合成/单句重新生成后，磁盘上的 take 与full_dub.wav
里那一版波形不同，逐样本互相关必然失效；指纹描述的是"这段话听起来
是什么样"，应当仍能定位。
"""
import os
import struct
import sys
import wave

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from index_tts_gui.core import fingerprint as fp


SR = fp.FINGERPRINT_SR

# 两组共振峰 = 两段不同文本；渲染同一段文本时共振峰必须完全一致，
# 只有 F0/相位/时长/噪声不同 —— 这才是"同一句话的两遍 TTS"。
VOW_A = [(700, 90, 1.0), (1220, 110, 0.5), (2600, 170, 0.25)]
VOW_B = [(400, 80, 1.0), (1900, 130, 0.4), (2550, 200, 0.2)]


def _render(formants, syllables, f0_base, seed):
    """类语音信号：固定共振峰 + 变化 F0 + 音节包络 + 轻噪。"""
    rng = np.random.default_rng(seed)
    parts = []
    for dur in syllables:
        n = int(SR * dur)
        t = np.arange(n) / SR
        x = np.zeros(n)
        for fr, bw, amp in formants:
            x += amp * np.exp(-np.pi * bw * t) * np.cos(
                2 * np.pi * fr * t + rng.uniform(0, 6.28))
        f0 = f0_base * (1 + 0.05 * np.sin(2 * np.pi * 1.5 * t + rng.uniform(0, 6.28)))
        x *= 0.6 + 0.4 * np.sin(2 * np.pi * np.cumsum(f0) / SR)
        x *= 0.5 + 0.5 * np.sin(np.pi * t / dur)
        parts.append(x)
    y = np.concatenate(parts) + rng.normal(0, 0.003, int(SR * sum(syllables)))
    return y.astype(np.float32)


def _write(path, y):
    y = np.clip(y, -1, 1)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(b"".join(struct.pack("<h", int(v * 32000)) for v in y))


def test_compute_shape_and_determinism():
    y = _render(VOW_A, [0.2, 0.2], 150, 1)
    a = fp.compute(y, SR)
    b = fp.compute(y, SR)
    assert len(a) == fp.FINGERPRINT_LEN
    assert a == b, "同一输入必须得到同一指纹"
    assert all(isinstance(v, int) for v in a)


def test_compute_rejects_degenerate_input():
    assert fp.compute(np.zeros(0, dtype=np.float32), SR) == []
    assert fp.compute(np.zeros(SR // 10, dtype=np.float32), SR) == []


def test_same_text_renders_are_close():
    """同一文本的两次渲染，指纹必须足够接近（能跨过重新合成）。"""
    a1 = fp.compute(_render(VOW_A, [0.20, 0.22, 0.20], 150, 1), SR)
    a2 = fp.compute(_render(VOW_A, [0.21, 0.215, 0.205], 137, 2), SR)
    d = fp.distance(a1, a2)
    assert d < fp.MATCH_THRESHOLD, f"同文本两次渲染距离过大: {d:.4f}"


def test_different_text_is_far():
    """不同文本必须明显分开，否则指纹会把字幕配到别的句子上。"""
    a = fp.compute(_render(VOW_A, [0.2, 0.2], 150, 1), SR)
    b = fp.compute(_render(VOW_B, [0.2, 0.2], 158, 3), SR)
    d = fp.distance(a, b)
    assert d > fp.MATCH_THRESHOLD * 2, f"异文本距离过小: {d:.4f}"


def test_distance_of_invalid_input_is_worst():
    good = fp.compute(_render(VOW_A, [0.2], 150, 1), SR)
    assert fp.distance([], good) == 1.0
    assert fp.distance(good, []) == 1.0
    assert fp.distance(good, good[:5]) == 1.0, "长度不符应判为最差"


def test_save_load_roundtrip(tmp_path):
    a = fp.compute(_render(VOW_A, [0.2, 0.2], 150, 1), SR)
    b = fp.compute(_render(VOW_B, [0.2, 0.2], 158, 3), SR)
    fp.save(str(tmp_path), {1: a, 2: b})
    loaded = fp.load(str(tmp_path))
    assert set(loaded) == {1, 2}
    assert fp.distance(loaded[1], a) < 1e-9, "量化往返必须无损"
    assert fp.distance(loaded[1], b) > fp.MATCH_THRESHOLD


def test_load_tolerates_missing_and_corrupt(tmp_path):
    """缺文件/损坏文件都必须降级为空字典，而不是让校准崩掉。"""
    assert fp.load(str(tmp_path)) == {}

    (tmp_path / fp.FINGERPRINT_FILE).write_text("{不是 json", encoding="utf-8")
    assert fp.load(str(tmp_path)) == {}

    (tmp_path / fp.FINGERPRINT_FILE).write_text(
        '{"fingerprints": {"1": "不是列表", "2": [1,2,3]}}',
        encoding="utf-8",
    )
    assert fp.load(str(tmp_path)) == {}, "类型错误/长度不符的条目应被丢弃"


def test_locate_finds_position(tmp_path):
    """在滑窗音频里定位到指纹所属片段。"""
    import librosa

    a1 = _render(VOW_A, [0.20, 0.22, 0.20], 150, 1)
    b1 = _render(VOW_B, [0.21, 0.21, 0.21], 158, 3)
    a2 = _render(VOW_A, [0.21, 0.215, 0.205], 137, 2)
    gap = np.zeros(int(SR * 0.4), dtype=np.float32)
    full = np.concatenate([b1, gap, a2])
    t0 = len(b1) / SR + 0.4

    matrix = librosa.feature.mfcc(
        y=full, sr=SR, n_mfcc=fp.N_MFCC, hop_length=fp.HOP_LENGTH,
    )
    win = int((len(a1) / SR) * SR / fp.HOP_LENGTH)
    step = max(1, int(fp.SEARCH_STEP_SEC * SR / fp.HOP_LENGTH))

    pos, dist = fp.locate(fp.compute(a1, SR), matrix, fp.HOP_LENGTH, win,
                           0.0, len(full) / SR, step, SR)
    assert pos >= 0
    assert dist <= fp.MATCH_THRESHOLD
    assert abs(pos - t0) < 0.2, f"定位误差过大: {pos:.2f} vs {t0:.2f}"


def test_align_fallback_rescues_resynthesized_sentence(tmp_path):
    """端到端：take 被重新合成后，指纹回退仍能把句子定位回来。

    这是整个指纹功能的立题场景：磁盘上的 take 是新渲染的，而
    full_dub.wav 里是合并时的旧版本，逐样本互相关必然对不上。
    """
    from index_tts_gui.core.speech_aligner import align_sentences_detailed

    a1 = _render(VOW_A, [0.20, 0.22, 0.20], 150, 1)
    a2 = _render(VOW_A, [0.21, 0.215, 0.205], 137, 2)
    b1 = _render(VOW_B, [0.21, 0.21, 0.21], 158, 3)
    gap = np.zeros(int(SR * 0.4), dtype=np.float32)
    full = np.concatenate([b1, gap, a1])
    t0 = len(b1) / SR + 0.4

    p1 = tmp_path / "sentence_01_a.wav"
    p2 = tmp_path / "sentence_02_b.wav"
    _write(p1, a2)
    _write(p2, b1)
    fullp = tmp_path / "full.wav"
    _write(fullp, full)

    # 指纹在合并时落盘，描述的是 full_dub.wav 里那一版（a1），不是 a2
    fp.save(str(tmp_path), {1: fp.compute(a1, SR), 2: fp.compute(b1, SR)})

    plain = align_sentences_detailed(
        str(fullp), [str(p1), str(p2)], ["A", "B"], [0.4, 0.0],
    )
    assert plain.starts[0] < 0, "前提：纯波形匹配应当失败"

    rescued = align_sentences_detailed(
        str(fullp), [str(p1), str(p2)], ["A", "B"], [0.4, 0.0],
        None, fp.load(str(tmp_path)),
    )
    assert rescued.starts[0] >= 0, "指纹应当把这句救回来"
    assert abs(rescued.starts[0] - t0) < 0.25, (
        f"救回的位置应接近真实位置: {rescued.starts[0]:.2f} vs {t0:.2f}"
    )