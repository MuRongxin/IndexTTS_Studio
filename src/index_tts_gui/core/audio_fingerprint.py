"""音频指纹提取与匹配 — 用于字幕时间轴重新对齐。

原理：将音频片段降采样为固定帧率的波形指纹，在目标音频上滑动互相关匹配。
"""
import numpy as np
import soundfile as sf

# 指纹的时间分辨率：10ms 一帧，100 帧/秒
FP_FRAME_MS = 10


def extract_fingerprint(
    wav_path: str,
    start_sec: float,
    end_sec: float,
) -> list[float]:
    """从 WAV 片段中按固定帧率提取波形指纹。

    每 10ms 取一个采样点的绝对值，归一化到 [-1, 1]。
    短于 10ms 的片段返回空列表。

    Args:
        wav_path: 音频文件路径
        start_sec: 片段起始时间（秒）
        end_sec: 片段结束时间（秒）

    Returns:
        归一化波形指纹列表，长度 ≈ (end_sec - start_sec) * 100
    """
    dur = end_sec - start_sec
    if dur <= 0.01:
        return []

    data, sr = sf.read(wav_path, dtype="float32", always_2d=False)
    if data.ndim > 1:
        data = data.mean(axis=1)
    data = data.astype(np.float64)

    frame_samples = max(1, int(FP_FRAME_MS / 1000.0 * sr))
    start_sample = max(0, int(start_sec * sr))
    end_sample = min(len(data), int(end_sec * sr))

    fp = []
    for pos in range(start_sample, end_sample, frame_samples):
        if pos < len(data):
            fp.append(float(data[pos]))
    return _normalize(fp)


def _normalize(fp: list[float]) -> list[float]:
    """归一化到 [-1, 1]，全零时不变。"""
    if not fp:
        return fp
    arr = np.array(fp, dtype=np.float64)
    max_abs = np.max(np.abs(arr))
    if max_abs > 0:
        arr /= max_abs
    return arr.tolist()


def match_fingerprint(
    fingerprint: list[float],
    target_wav_path: str,
    search_start: float = 0.0,
    search_end: float | None = None,
    min_correlation: float = 0.6,
) -> tuple[float, float] | None:
    """在目标音频中按相同帧率滑动指纹，找最佳匹配位置。

    Args:
        fingerprint: extract_fingerprint 的输出
        target_wav_path: 目标音频文件路径
        search_start: 搜索起始时间（秒）
        search_end: 搜索结束时间（秒），None 表示文件末尾
        min_correlation: 最低相关系数阈值

    Returns:
        (最佳匹配时间, 相关系数)，失败返回 None
    """
    if not fingerprint:
        return None

    fp = np.array(fingerprint, dtype=np.float64)
    fp_norm = np.linalg.norm(fp)
    if fp_norm == 0:
        return None
    fp_len = len(fp)

    data, sr = sf.read(target_wav_path, dtype="float32", always_2d=False)
    if data.ndim > 1:
        data = data.mean(axis=1)
    data = data.astype(np.float64)
    total_duration = len(data) / sr

    search_end = min(search_end or total_duration, total_duration)
    if search_end <= search_start:
        return None

    frame_samples = max(1, int(FP_FRAME_MS / 1000.0 * sr))
    start_pos = int(search_start * sr)
    end_pos = min(len(data) - fp_len * frame_samples, int(search_end * sr))
    if end_pos <= start_pos:
        return None

    best_corr = -1.0
    best_time = search_start

    # 每 10ms 步进一次
    for pos in range(start_pos, end_pos, frame_samples):
        window = np.array(
            [abs(data[pos + i * frame_samples]) for i in range(fp_len)],
            dtype=np.float64,
        )
        w_norm = np.linalg.norm(window)
        if w_norm > 0:
            corr = float(np.dot(fp, window) / (fp_norm * w_norm))
            if corr > best_corr:
                best_corr = corr
                best_time = pos / sr

    if best_corr >= min_correlation:
        return (best_time, best_corr)
    return None
