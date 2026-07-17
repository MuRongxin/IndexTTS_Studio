"""音频波形提取与分析引擎 — 从音视频文件提取波形数据用于可视化"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import warnings
from typing import Optional

import numpy as np


class AudioEngine:
    """音频波形提取与分析引擎

    从音视频文件提取波形数据，提供降采样峰值数据用于时间轴可视化。

    Attributes:
        sample_rate: 采样率（Hz）
        duration: 音频时长（秒）
        filepath: 当前加载的音频文件路径
        waveform: 原始波形数据，shape=(n_samples, n_channels)
        peak_data: 降采样峰值数据，shape=(n_bars, 2)
    """

    def __init__(self):
        self.sample_rate: int = 0
        self.duration: float = 0.0
        self.filepath: str = ""
        self.waveform: Optional[np.ndarray] = None  # shape=(n_samples, n_channels)
        self.peak_data: Optional[np.ndarray] = None  # shape=(n_bars, 2)
        self._cache_key: Optional[tuple] = None

    def load_audio(self, filepath: str) -> bool:
        """加载音频文件。支持直接从视频文件提取。"""
        if not os.path.exists(filepath):
            return False

        try:
            import soundfile as sf
        except ImportError:
            return False

        sr = 22050
        waveform = None

        # 1. 尝试 soundfile
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                data, sr = sf.read(filepath, dtype="float32", always_2d=True)
            if data is not None and len(data) > 0:
                waveform = data
        except Exception:
            pass

        # 2. 尝试 librosa
        if waveform is None:
            try:
                import librosa

                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    y, sr = librosa.load(filepath, sr=22050, mono=True)
                if y is not None and len(y) > 0:
                    waveform = y.reshape(-1, 1).astype(np.float32)
            except Exception:
                pass

        # 3. 视频/不识别格式用 ffmpeg 提取临时 WAV
        if waveform is None:
            waveform, sr = self._extract_audio_via_ffmpeg(filepath)

        if waveform is not None and len(waveform) > 0:
            self.filepath = filepath
            self.sample_rate = sr
            self.waveform = waveform.astype(np.float32)
            self.duration = self.waveform.shape[0] / self.sample_rate
            self.invalidate_cache()
            return True

        self.clear()
        return False

    @staticmethod
    def _extract_audio_via_ffmpeg(filepath: str, sr: int = 22050):
        """使用 ffmpeg 从视频/音频文件提取音频到临时 WAV 并加载。"""
        if shutil.which("ffmpeg") is None:
            return None, sr

        tmp_wav = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                tmp_wav = f.name

            cmd = [
                "ffmpeg", "-y", "-i", filepath,
                "-vn", "-acodec", "pcm_s16le",
                "-ar", str(sr), "-ac", "1",
                tmp_wav,
            ]
            result = subprocess.run(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120
            )
            if result.returncode != 0:
                return None, sr

            import soundfile as sf
            data, sr = sf.read(tmp_wav, dtype="float32", always_2d=True)
            return data, sr
        except Exception:
            return None, sr
        finally:
            if tmp_wav and os.path.exists(tmp_wav):
                try:
                    os.remove(tmp_wav)
                except Exception:
                    pass

    def is_loaded(self) -> bool:
        return self.waveform is not None and self.waveform.size > 0

    def extract_waveform(
        self, num_bars: int = 2000, start: float = 0.0, end: float = 0.0
    ) -> np.ndarray:
        """将 [start, end] 秒区间的波形降采样为峰值条，返回 shape=(num_bars, 2)。

        每条对应一个互不重叠的采样区间，存 (min, max)；end <= start 时提取到
        文件尾。num_bars 超过区间采样数时按采样数截断（每条 1 采样）。
        结果按 (num_bars, start, end) 缓存。向量化实现，无 Python 逐条循环。
        """
        if not self.is_loaded():
            return np.zeros((num_bars, 2), dtype=np.float32)

        start = max(0.0, min(float(start), self.duration))
        if end <= start:
            end = self.duration
        end = min(float(end), self.duration)

        num_bars = max(1, int(num_bars))
        key = (num_bars, round(start, 3), round(end, 3))
        if self._cache_key == key and self.peak_data is not None:
            return self.peak_data

        if self.waveform.shape[1] > 1:
            mono = self.waveform.mean(axis=1)
        else:
            mono = self.waveform[:, 0]

        s0 = int(start * self.sample_rate)
        s1 = min(mono.shape[0], max(s0 + 1, int(end * self.sample_rate)))
        seg = mono[s0:s1]

        num_bars = min(num_bars, seg.shape[0])
        counts = np.full(num_bars, seg.shape[0] // num_bars, dtype=np.int64)
        counts[: seg.shape[0] % num_bars] += 1
        starts = np.concatenate(([0], np.cumsum(counts)[:-1]))

        peak_data = np.empty((num_bars, 2), dtype=np.float32)
        peak_data[:, 0] = np.minimum.reduceat(seg, starts)
        peak_data[:, 1] = np.maximum.reduceat(seg, starts)

        self.peak_data = peak_data
        self._cache_key = key
        return self.peak_data

    def invalidate_cache(self) -> None:
        """使峰值缓存失效（音频数据被替换后调用）。"""
        self.peak_data = None
        self._cache_key = None

    def clear(self) -> None:
        self.sample_rate = 0
        self.duration = 0.0
        self.filepath = ""
        self.waveform = None
        self.peak_data = None
        self._cache_key = None

    def __del__(self):
        self.clear()
