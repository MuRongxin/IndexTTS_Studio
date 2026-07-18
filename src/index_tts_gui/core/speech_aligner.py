"""
语音对齐：先在低采样率下对全文做逐句独立粗匹配（互不锚定，避免错误传播），
再用最长递增子序列做单调性校验剔除离群匹配，最后在高采样率小窗口内精修，
重新计算字幕时间戳。互相关置信度不足时回退到带期望句数先验的能量分割法。
"""
import logging
from bisect import bisect_right
from typing import Callable, Optional

import numpy as np
import librosa
import scipy.signal

from index_tts_gui.core.merger import get_wav_duration
from index_tts_gui.core.subtitle import SubtitleEntry


logger = logging.getLogger(__name__)

TARGET_SR = 8000          # 精修采样率
COARSE_SR = 4000          # 粗匹配采样率（全文独立定位）
CONFIDENCE_THRESHOLD = 0.25
FALLBACK_RATIO = 0.3
REFINE_MARGIN = 3.0       # 精修窗口在粗位置前后的余量（秒）
TOP_K_PEAKS = 5           # 候选峰个数
PRIOR_CONF_RATIO = 0.7    # 候选峰置信度不低于最佳峰该比例时，优先选离先验位置近的
MONO_TOLERANCE = 0.05     # 单调性容差（秒）


def _load_mono(wav_path: str, target_sr: int = TARGET_SR) -> np.ndarray:
    """加载音频为 mono 并重采样到 target_sr。"""
    y, _sr = librosa.load(wav_path, sr=target_sr, mono=True)
    return y.astype(np.float32)


def _cross_correlate_match(
    template: np.ndarray,
    signal_segment: np.ndarray,
    sr: int,
    search_start: float,
    expected_start: Optional[float] = None,
    top_k: int = TOP_K_PEAKS,
) -> tuple[float, float]:
    """
    在 signal_segment 中匹配 template，返回 (start_time_in_original_audio, confidence)。
    返回的时间相对于完整音频的起始（search_start + 匹配偏移）。

    取 top-k 个候选峰；给定 expected_start 先验时，在置信度接近最佳峰
    （>= PRIOR_CONF_RATIO 倍）的候选中选离先验最近的，避免重复/相似
    内容造成的"自信误配"。
    """
    t = (template - np.mean(template)) / (np.std(template) + 1e-10)
    s = (signal_segment - np.mean(signal_segment)) / (np.std(signal_segment) + 1e-10)

    if len(t) > len(s):
        return -1.0, 0.0

    corr = scipy.signal.correlate(s, t, method="fft")
    # 只保留完整重叠区域：起点 lag ∈ [0, len(s)-len(t)]
    valid = np.abs(corr[len(t) - 1 : len(s)])
    if len(valid) == 0:
        return -1.0, 0.0

    # 候选峰：间距至少半个模板长，避免同一个匹配点附近重复出峰
    peaks, _ = scipy.signal.find_peaks(valid, distance=max(1, len(t) // 2))
    if len(peaks) == 0:
        peaks = np.array([int(np.argmax(valid))])
    top = peaks[np.argsort(valid[peaks])[::-1][:top_k]]

    best = int(top[np.argmax(valid[top])])
    chosen = best
    if expected_start is not None:
        best_conf = valid[best]
        cands = [p for p in top if valid[p] >= best_conf * PRIOR_CONF_RATIO]
        chosen = int(min(cands, key=lambda p: abs(p / sr + search_start - expected_start)))

    confidence = float(valid[chosen]) / len(t)
    start_time = search_start + chosen / sr

    return start_time, confidence


def _detect_speech_segments(is_speech: np.ndarray) -> list[tuple[float, float]]:
    """由布尔帧序列提取语音段 [(start, end)]（秒），过滤短于 0.15s 的段。"""
    segments: list[tuple[float, float]] = []
    in_speech = False
    speech_start = 0
    for i, s in enumerate(is_speech):
        if s and not in_speech:
            speech_start = i
            in_speech = True
        elif not s and in_speech:
            if (i - speech_start) * 0.010 > 0.15:
                segments.append((speech_start * 0.010, i * 0.010))
            in_speech = False
    if in_speech and (len(is_speech) - speech_start) * 0.010 > 0.15:
        segments.append((speech_start * 0.010, len(is_speech) * 0.010))
    return segments


def _fit_segment_count(
    segments: list[tuple[float, float]],
    rms: np.ndarray,
    num_expected: int,
) -> list[tuple[float, float]]:
    """以期望句数为先验调整段数：段多则合并间隔最小的相邻段，
    段少则在最长的可分裂段内按能量低谷分裂。"""
    segs = list(segments)

    while len(segs) > num_expected and len(segs) > 1:
        gaps = [segs[k + 1][0] - segs[k][1] for k in range(len(segs) - 1)]
        k = int(np.argmin(gaps))
        segs[k : k + 2] = [(segs[k][0], segs[k + 1][1])]

    while 0 < len(segs) < num_expected:
        # 找最长的可分裂段（两半都需 >= 0.15s，留足余量要求 >= 0.4s）
        cand = None
        for k, (a, b) in enumerate(segs):
            if b - a >= 0.4 and (cand is None or b - a > cand[2] - cand[1]):
                cand = (k, a, b)
        if cand is None:
            break
        k, a, b = cand
        fa, fb = int(a / 0.010), int(b / 0.010)
        margin = int(0.15 / 0.010)
        window = rms[fa:fb]
        if len(window) <= margin * 2:
            break
        split_rel = int(np.argmin(window[margin:-margin])) + margin
        split_t = (fa + split_rel) * 0.010
        segs[k : k + 1] = [(a, split_t), (split_t, b)]

    return segs


def _energy_based_segment(
    wav_path: str,
    num_expected: int,
    sr: int = 16000,
    expected_durations: Optional[list[float]] = None,
) -> list[float]:
    """按能量检测语音段起止，返回各段起始时间。

    尝试多个阈值，取段数接近期望句数的结果，再用合并/分裂凑齐期望数量。
    """
    y, _sr = librosa.load(wav_path, sr=sr, mono=True)
    y = y.astype(np.float32)

    hop = int(sr * 0.010)
    frame = int(sr * 0.025)
    rms = librosa.feature.rms(y=y, frame_length=frame, hop_length=hop)[0]

    max_rms = float(np.max(rms))
    if max_rms <= 0:
        return []

    best_segments: list[tuple[float, float]] = []
    for threshold_factor in [0.03, 0.02, 0.05, 0.01, 0.015]:
        thresh = max_rms * threshold_factor
        segments = _detect_speech_segments(rms > thresh)
        logger.info(
            "能量分割尝试: threshold=%.3f segments=%d (期望 %d)",
            threshold_factor, len(segments), num_expected,
        )
        if len(segments) == num_expected:
            best_segments = segments
            break
        if abs(len(segments) - num_expected) < abs(len(best_segments) - num_expected):
            best_segments = segments

    if not best_segments:
        logger.error("能量分割完全失败")
        return []

    fitted = _fit_segment_count(best_segments, rms, num_expected)
    logger.info("能量分割: 期望 %d 段, 调整前 %d 段, 调整后 %d 段",
                num_expected, len(best_segments), len(fitted))
    return [s[0] for s in fitted]


def _longest_increasing_subseq(values: list[float]) -> list[int]:
    """返回 values 的最长递增子序列的下标（允许 MONO_TOLERANCE 内的小回退）。"""
    n = len(values)
    if n == 0:
        return []
    dp = [1] * n
    parent = [-1] * n
    for i in range(n):
        for j in range(i):
            if values[j] < values[i] + MONO_TOLERANCE and dp[j] + 1 > dp[i]:
                dp[i] = dp[j] + 1
                parent[i] = j
    best = int(np.argmax(dp))
    seq = []
    while best >= 0:
        seq.append(best)
        best = parent[best]
    return sorted(seq)


def _interpolate_position(
    i: int,
    old_starts: list[float],
    new_starts: list[float],
    reliable: list[bool],
) -> float:
    """按原时间轴的相对位置，在相邻可靠句之间插值第 i 句的新位置。"""
    n = len(old_starts)
    left = max((j for j in range(i) if reliable[j]), default=None)
    right = min((j for j in range(i + 1, n) if reliable[j]), default=None)

    if left is not None and right is not None:
        span_old = old_starts[right] - old_starts[left]
        f = (old_starts[i] - old_starts[left]) / span_old if span_old > 1e-6 else 0.0
        return new_starts[left] + f * (new_starts[right] - new_starts[left])
    if left is not None:
        return new_starts[left] + (old_starts[i] - old_starts[left])
    if right is not None:
        return new_starts[right] - (old_starts[right] - old_starts[i])
    return old_starts[i]


def align_sentences(
    modified_wav_path: str,
    sentence_wavs: list[str],
    sentences: list[str],
    original_pauses: list[float],
) -> tuple[list[float], list[float]]:
    """
    在修改后的 full_dub.wav 中定位每句原始 WAV 的位置。

    流程：
      1. 粗匹配：低采样率下每句独立匹配全文（互不锚定，一句错不影响其他句）
      2. 单调性校验：最长递增子序列为锚点；离群/低置信句在锚点区间内带
         位置先验重匹配（重复文本选离预期位置最近的候选峰）
      3. 精修：高采样率下粗位置 ±REFINE_MARGIN 小窗口内重匹配
      4. 仍不可靠的句子按原时间轴比例在相邻可靠句之间插值

    Returns:
        (new_starts, scores): 每句的新起始时间（秒）与匹配置信度；
        scores[i] < 0 表示该句为插值结果，未得到有效匹配。
    """
    n = len(sentence_wavs)
    if n == 0:
        return [], []

    original_durations = [get_wav_duration(p) for p in sentence_wavs]
    pauses = list(original_pauses) + [0.0] * max(0, n - len(original_pauses))
    old_starts: list[float] = []
    acc = 0.0
    for i in range(n):
        old_starts.append(acc)
        acc += original_durations[i] + pauses[i]

    logger.info("加载修改后音频: %s", modified_wav_path)
    full_coarse = _load_mono(modified_wav_path, COARSE_SR)
    coarse_duration = len(full_coarse) / COARSE_SR
    logger.info("修改后音频时长: %.2fs", coarse_duration)

    # ── 第一遍：低采样率全文独立粗匹配 ──
    coarse_starts = [-1.0] * n
    coarse_conf = [0.0] * n
    templates_coarse: list[np.ndarray] = []
    for i in range(n):
        tpl = _load_mono(sentence_wavs[i], COARSE_SR)
        templates_coarse.append(tpl)
        if len(tpl) > len(full_coarse):
            logger.warning("句子 %d 模板长于整段音频，标记为失败", i + 1)
            continue
        st, cf = _cross_correlate_match(tpl, full_coarse, COARSE_SR, 0.0)
        coarse_starts[i] = st
        coarse_conf[i] = cf
        logger.debug("句子 %d/%d 粗匹配: start=%.3f conf=%.3f", i + 1, n, st, cf)

    reliable = [
        coarse_starts[i] >= 0 and coarse_conf[i] >= CONFIDENCE_THRESHOLD
        for i in range(n)
    ]

    # ── 单调性校验：离群匹配剔除为不可靠 ──
    idx_reliable = [i for i in range(n) if reliable[i]]
    anchors = set(idx_reliable)
    if idx_reliable:
        lis = _longest_increasing_subseq([coarse_starts[i] for i in idx_reliable])
        anchors = {idx_reliable[p] for p in lis}
    for i in idx_reliable:
        if i not in anchors:
            logger.warning(
                "句子 %d 粗匹配位置 (%.2fs) 违反单调性，剔除（conf=%.2f）",
                i + 1, coarse_starts[i], coarse_conf[i],
            )
            reliable[i] = False

    # ── 不可靠句：在相邻锚点区间内带位置先验重匹配 ──
    for i in range(n):
        if reliable[i]:
            continue
        left = max((j for j in range(i) if reliable[j]), default=None)
        right = min((j for j in range(i + 1, n) if reliable[j]), default=None)
        if left is None and right is None:
            continue
        expected = _interpolate_position(i, old_starts, coarse_starts, reliable)
        region_start = (
            coarse_starts[left] + original_durations[left] if left is not None else 0.0
        )
        region_end = coarse_starts[right] if right is not None else coarse_duration
        seg = full_coarse[int(region_start * COARSE_SR): int(region_end * COARSE_SR)]
        tpl = templates_coarse[i]
        if len(seg) < len(tpl):
            continue
        st, cf = _cross_correlate_match(
            tpl, seg, COARSE_SR, region_start, expected_start=expected
        )
        if st >= 0 and cf >= CONFIDENCE_THRESHOLD:
            coarse_starts[i], coarse_conf[i] = st, cf
            reliable[i] = True
            logger.info("句子 %d 锚点区间内重匹配成功: %.2fs conf=%.2f", i + 1, st, cf)

    reliable_count = sum(reliable)

    # ── 可靠匹配太少：回退能量分割 ──
    if (n - reliable_count) / max(n, 1) > FALLBACK_RATIO:
        logger.warning("低置信度句子过多 (%d/%d)，回退到能量分割",
                       n - reliable_count, n)
        fallback_starts = _energy_based_segment(
            modified_wav_path, n, expected_durations=original_durations
        )
        if len(fallback_starts) == n:
            logger.info("能量分割成功，使用能量分割结果")
            return fallback_starts, [-1.0] * n
        logger.warning("能量分割结果不完整 (%d/%d)，使用互相关+插值结果",
                       len(fallback_starts), n)

    # ── 第二遍：高采样率小窗口精修 ──
    full_fine = _load_mono(modified_wav_path, TARGET_SR)
    fine_duration = len(full_fine) / TARGET_SR
    new_starts = [-1.0] * n
    scores = [-1.0] * n
    for i in range(n):
        if not reliable[i]:
            continue
        tpl = _load_mono(sentence_wavs[i], TARGET_SR)
        tpl_dur = len(tpl) / TARGET_SR
        w_start = max(0.0, coarse_starts[i] - REFINE_MARGIN)
        w_end = min(fine_duration, coarse_starts[i] + tpl_dur + REFINE_MARGIN)
        seg = full_fine[int(w_start * TARGET_SR): int(w_end * TARGET_SR)]
        if len(seg) < len(tpl):
            new_starts[i], scores[i] = coarse_starts[i], coarse_conf[i]
            continue
        st, cf = _cross_correlate_match(
            tpl, seg, TARGET_SR, w_start, expected_start=coarse_starts[i]
        )
        if st >= 0 and cf >= CONFIDENCE_THRESHOLD:
            new_starts[i], scores[i] = st, cf
        else:
            # 精修失败则保留粗匹配位置
            new_starts[i], scores[i] = coarse_starts[i], coarse_conf[i]

    # ── 仍不可靠的句子：按原时间轴比例插值 ──
    for i in range(n):
        if reliable[i]:
            continue
        new_starts[i] = _interpolate_position(i, old_starts, new_starts, reliable)
        scores[i] = -1.0

    logger.info("对齐完成: %d 句, 锚点 %d 句, 插值 %d 句",
                n, sum(reliable), n - sum(reliable))
    return new_starts, scores


def build_time_mapper(
    old_starts: list[float],
    old_durations: list[float],
    new_starts: list[float],
) -> Callable[[float], float]:
    """
    构建时间映射函数: new_t = mapper(old_t)。

    句内音频内容未变，直接平移；句间停顿段按 (新间隙/旧间隙) 等比例映射。
    """
    n = len(old_starts)
    old_ends = [old_starts[i] + old_durations[i] for i in range(n)]
    new_ends = [new_starts[i] + old_durations[i] for i in range(n)]

    def map_time(t: float) -> float:
        if n == 0:
            return t

        if t <= old_starts[0]:
            return t + (new_starts[0] - old_starts[0])

        # 最后一个满足 old_starts[i] <= t 的句子
        i = bisect_right(old_starts, t) - 1

        if i >= n - 1 and t > old_ends[-1]:
            return t + (new_ends[-1] - old_ends[-1])

        if t <= old_ends[i]:
            return new_starts[i] + (t - old_starts[i])

        # 句间间隙：按新旧间隙比例映射
        old_gap = old_starts[i + 1] - old_ends[i]
        new_gap = new_starts[i + 1] - new_ends[i]
        if old_gap > 1e-6:
            ratio = (t - old_ends[i]) / old_gap
            return new_ends[i] + ratio * max(new_gap, 0.0)
        return new_ends[i] + (t - old_ends[i])

    return map_time


def recalibrate_entries(
    entries: list[SubtitleEntry],
    old_sentence_starts: list[float],
    old_sentence_durations: list[float],
    new_sentence_starts: list[float],
) -> list[SubtitleEntry]:
    """用对齐结果重新映射字幕条目的时间戳。"""
    mapper = build_time_mapper(
        old_sentence_starts, old_sentence_durations, new_sentence_starts
    )

    new_entries = []
    for e in entries:
        new_start = mapper(e.start_sec)
        new_end = mapper(e.end_sec)
        if new_end <= new_start:
            new_end = new_start + 0.1
        new_entries.append(
            SubtitleEntry(
                index=e.index,
                start_sec=round(new_start, 3),
                end_sec=round(new_end, 3),
                text=e.text,
            )
        )
    return new_entries
