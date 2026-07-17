"""测试 AudioEngine.extract_waveform 的区间提取、向量化正确性与缓存"""
import sys
import os

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from index_tts_gui.ui.audio_engine import AudioEngine


SR = 1000  # 1 秒 = 1000 采样点，便于换算


def make_engine(n_seconds=10.0, channels=1, seed=42):
    rng = np.random.default_rng(seed)
    n = int(n_seconds * SR)
    if channels == 1:
        data = rng.uniform(-1, 1, (n, 1)).astype(np.float32)
    else:
        data = rng.uniform(-1, 1, (n, channels)).astype(np.float32)
    eng = AudioEngine()
    eng.waveform = data
    eng.sample_rate = SR
    eng.duration = n_seconds
    eng.filepath = "fake.wav"
    return eng


def naive_minmax(mono, num_bars):
    """与实现相同的均分块策略，但用朴素循环求 min/max（用于对照向量化结果）。"""
    num_bars = min(num_bars, len(mono))
    counts = np.full(num_bars, len(mono) // num_bars, dtype=np.int64)
    counts[: len(mono) % num_bars] += 1
    starts = np.concatenate(([0], np.cumsum(counts)[:-1]))
    out = np.zeros((num_bars, 2), dtype=np.float32)
    for i, s in enumerate(starts):
        e = s + counts[i]
        out[i, 0] = mono[s:e].min()
        out[i, 1] = mono[s:e].max()
    return out


def test_full_range_matches_naive():
    eng = make_engine()
    bars = eng.extract_waveform(777)
    expected = naive_minmax(eng.waveform[:, 0], 777)
    assert bars.shape == expected.shape
    np.testing.assert_allclose(bars, expected, atol=1e-6)


def test_region_extraction_covers_only_region():
    eng = make_engine()
    start, end = 2.0, 4.0
    num_bars = 50
    bars = eng.extract_waveform(num_bars, start, end)
    seg = eng.waveform[int(start * SR):int(end * SR), 0]
    expected = naive_minmax(seg, num_bars)
    assert bars.shape == (num_bars, 2)
    np.testing.assert_allclose(bars, expected, atol=1e-6)


def test_region_spike_lands_in_correct_bar():
    eng = make_engine()
    eng.waveform[:] = 0.0
    spike_sample = 2100  # 2.1s
    eng.waveform[spike_sample, 0] = 0.9
    bars = eng.extract_waveform(50, 2.0, 4.0)  # 每条 40ms
    idx = int((spike_sample / SR - 2.0) / 0.04)
    assert bars[idx, 1] == pytest.approx(0.9)
    assert bars[:idx, 1].max() == 0.0
    assert bars[idx + 1:, 1].max() == 0.0


def test_end_defaults_to_file_tail():
    eng = make_engine()
    bars = eng.extract_waveform(20, 8.0)
    seg = eng.waveform[8 * SR:, 0]
    expected = naive_minmax(seg, 20)
    np.testing.assert_allclose(bars, expected, atol=1e-6)


def test_bars_clamped_to_segment_length():
    eng = make_engine()
    # 区间只有 10 个采样点，请求 500 条 → 每条 1 采样
    bars = eng.extract_waveform(500, 1.0, 1.01)
    assert bars.shape[0] == 10
    np.testing.assert_allclose(bars[:, 0], bars[:, 1], atol=1e-6)


def test_stereo_is_averaged():
    eng = make_engine(channels=2)
    bars = eng.extract_waveform(100)
    mono = eng.waveform.mean(axis=1)
    expected = naive_minmax(mono, 100)
    np.testing.assert_allclose(bars, expected, atol=1e-6)


def test_cache_returns_same_object_and_invalidates():
    eng = make_engine()
    a = eng.extract_waveform(100)
    b = eng.extract_waveform(100)
    assert a is b
    c = eng.extract_waveform(100, 1.0, 3.0)
    assert c is not a
    eng.invalidate_cache()
    d = eng.extract_waveform(100)
    assert d is not a


def test_not_loaded_returns_zeros():
    eng = AudioEngine()
    bars = eng.extract_waveform(100)
    assert bars.shape == (100, 2)
    assert np.all(bars == 0)
