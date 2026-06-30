"""音频指纹提取与匹配 — 用于字幕时间轴重新对齐。

原理：从 WAV 片段提取 RMS 包络作为指纹，在目标音频上滑动互相关匹配。
"""
import numpy as np
import soundfile as sf


def extract_fingerprint(
    wav_path: str,
    start_sec: float,
    end_sec: float,
    num_points: int = 200,
) -> list[float]:
    """从 WAV 文件中提取指定时间段的 RMS 包络指纹。

    Args:
        wav_path: 音频文件路径
        start_sec: 起始时间（秒）
        end_sec: 结束时间（秒）
        num_points: 降采样后的指纹长度

    Returns:
        RMS 包络数组，长度 num_points，归一化到 [0, 1]
    """
    if start_sec >= end_sec:
        return [0.0] * num_points

    # 读取音频
    data, sr = sf.read(wav_path, dtype="float32", always_2d=False)
    if data.ndim > 1:
        data = data.mean(axis=1)  # 转单声道

    start_sample = max(0, int(start_sec * sr))
    end_sample = min(len(data), int(end_sec * sr))
    if end_sample <= start_sample:
        return [0.0] * num_points

    segment = data[start_sample:end_sample].astype(np.float64)
    segment_len = len(segment)

    # 计算 RMS 包络：分帧 → RMS → 降采样
    frame_size = max(1, segment_len // num_points)
    rms = np.zeros(num_points, dtype=np.float64)
    for i in range(num_points):
        frame_start = i * frame_size
        frame_end = min(segment_len, frame_start + frame_size)
        if frame_end > frame_start:
            chunk = segment[frame_start:frame_end]
            rms[i] = np.sqrt(np.mean(chunk ** 2))
    # 归一化
    rms_max = rms.max()
    if rms_max > 0:
        rms /= rms_max
    return rms.tolist()


def match_fingerprint(
    fingerprint: list[float],
    target_wav_path: str,
    search_start: float = 0.0,
    search_end: float | None = None,
    step_ms: int = 10,
    min_correlation: float = 0.6,
) -> tuple[float, float] | None:
    """在目标音频中搜索指纹的最佳匹配位置。

    Args:
        fingerprint: 查询指纹（extract_fingerprint 的输出）
        target_wav_path: 目标音频文件路径
        search_start: 搜索范围起始（秒）
        search_end: 搜索范围结束（秒），None 表示文件末尾
        step_ms: 搜索步长（毫秒）
        min_correlation: 最低置信度阈值，低于此值返回 None

    Returns:
        (最佳匹配时间, 相关系数)，匹配失败返回 None
    """
    fp = np.array(fingerprint, dtype=np.float64)
    fp_norm = np.linalg.norm(fp)
    if fp_norm == 0:
        return None

    # 读取目标音频
    data, sr = sf.read(target_wav_path, dtype="float32", always_2d=False)
    if data.ndim > 1:
        data = data.mean(axis=1)
    data = data.astype(np.float64)
    total_duration = len(data) / sr

    search_end = min(search_end or total_duration, total_duration)
    if search_end <= search_start:
        return None

    step_samples = max(1, int(step_ms / 1000.0 * sr))
    fp_duration = len(fp) * step_samples / sr / 10  # 粗略估算指纹覆盖的时长

    best_corr = -1.0
    best_time = search_start

    pos = int(search_start * sr)
    end_pos = int(search_end * sr) - step_samples * len(fp)
    if end_pos <= pos:
        return None

    while pos < end_pos:
        # 从目标音频取同样长度的窗口
        window = np.zeros(len(fp), dtype=np.float64)
        for i in range(len(fp)):
            sample_idx = pos + i * step_samples
            if sample_idx < len(data):
                window[i] = abs(data[sample_idx])

        w_norm = np.linalg.norm(window)
        if w_norm > 0:
            corr = np.dot(fp, window) / (fp_norm * w_norm)
            if corr > best_corr:
                best_corr = corr
                best_time = pos / sr

        pos += step_samples

    if best_corr >= min_correlation:
        return (best_time, float(best_corr))
    return None
