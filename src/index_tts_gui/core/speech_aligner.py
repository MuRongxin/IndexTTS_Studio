"""
语音对齐：先在低采样率下对全文做逐句独立粗匹配（互不锚定，避免错误传播），
低置信句在相邻高置信句区间内带位置先验重匹配，最后在高采样率小窗口内精修，
重新计算字幕时间戳。

匹配置信度使用精确 NCC（逐窗去均值归一化互相关，[-1, 1]）：
同一份录音 ≈ 0.9+，不同录音（哪怕同文本重新合成）< 0.5。
只认正相关峰，避免反相噪声冒充匹配。

支持语序调整和句子删除：不强制单调，每句独立定位到新音频中的真实位置；
字幕映射是**逐句刚性平移**（每句的音频内容不变，只是被移到新位置），
不做相邻句插值，否则语序改变后会整体错位。未能匹配的句子标记为 -1，
对应字幕条目直接丢弃（这句话已不在音频中）。
"""
import logging
from bisect import bisect_right
from typing import Callable, Optional

import numpy as np
import librosa
import scipy.signal

from index_tts_gui.core.merger import get_wav_duration
from index_tts_gui.core.subtitle import SubtitleEntry


logger = logging.getLogger("index_tts.speech_aligner")


TARGET_SR = 8000          # 精修采样率
COARSE_SR = 4000          # 粗匹配采样率（全文独立定位）
# NCC 阈值：同一份录音 0.9+，同文本不同一遍合成 / 假峰 < 0.5，取中间留裕量
CONFIDENCE_THRESHOLD = 0.5
REFINE_MARGIN = 3.0       # 精修窗口在粗位置前后的余量（秒）
TOP_K_PEAKS = 5           # 候选峰个数
PRIOR_CONF_RATIO = 0.7    # 候选峰置信度不低于最佳峰该比例时，优先选离先验位置近的

# 能量分割结果只能作为"待验证提示"，其位置很粗糙，验证窗口要放宽
HINT_REFINE_MARGIN = 12.0


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

    置信度是精确 NCC（逐窗去均值归一化互相关，[-1, 1]）：
    同一份录音的窗口 ≈ 0.9+，不同录音（含同文本重新合成）< 0.5。
    只认正相关峰——负相关（反相）不是"内容相同"的证据。

    取 top-k 个候选峰；给定 expected_start 先验时，在置信度接近最佳峰
    （>= PRIOR_CONF_RATIO 倍）的候选中选离先验最近的，避免重复/相似
    内容造成的"自信误配"。
    """
    t = template - np.mean(template)
    nt = float(np.linalg.norm(t))
    L = len(t)
    s = np.asarray(signal_segment, dtype=np.float64)
    N = len(s)
    if L > N or L == 0 or nt < 1e-9:
        return -1.0, 0.0

    num = scipy.signal.correlate(s, t.astype(np.float64), method="fft")[L - 1 : N]
    if len(num) == 0:
        return -1.0, 0.0

    # 逐窗局部能量（去均值）。设下限防止纯静音窗的数值伪影：
    # 真匹配窗口能量与模板相当，低于模板能量 15% 的窗不可能是匹配
    ones = np.ones(L)
    sq = scipy.signal.fftconvolve(s * s, ones, mode="valid")
    mu = scipy.signal.fftconvolve(s, ones, mode="valid") / L
    loc = np.sqrt(np.maximum(sq - L * mu * mu, (0.15 * nt) ** 2))
    ncc = np.clip(num / (nt * loc), -1.0, 1.0)

    # 负相关不是有效匹配，只在正相关峰里选；全负时 confidence 归 0。
    # 首尾各补一个 -inf：匹配落在搜索区端点（如音频开头/结尾的句子）时
    # 也必须能成为候选峰——find_peaks 本身不考虑数组端点
    padded = np.concatenate(([-np.inf], ncc, [-np.inf]))
    peaks, _ = scipy.signal.find_peaks(padded, distance=max(1, L // 2))
    peaks = peaks - 1
    if len(peaks) == 0:
        peaks = np.array([int(np.argmax(ncc))])
    top = peaks[np.argsort(ncc[peaks])[::-1][:top_k]]

    best = int(top[np.argmax(ncc[top])])
    chosen = best
    if expected_start is not None:
        best_conf = float(ncc[best])
        cands = [p for p in top if ncc[p] >= best_conf * PRIOR_CONF_RATIO]
        chosen = int(min(cands, key=lambda p: abs(p / sr + search_start - expected_start)))

    confidence = max(0.0, float(ncc[chosen]))
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
    在调整后的音频中定位每句原始 WAV 的位置。

    支持语序调整和句子删除：不强制单调性，每句独立定位到
    新音频中的真实位置，缺失句标记为 -1。

    流程：
      1. 粗匹配：低采样率下每句独立匹配全文（互不锚定，一句错不影响其他句）
      2. 低置信句在相邻高置信句区间内带位置先验重匹配
         （重复文本选离预期位置最近的候选峰）
      3. 能量分割只产生"待验证提示"，必须通过互相关验证
      4. 精修：高采样率下粗位置附近小窗口内重匹配
      5. 仍不可靠的句子标记为缺失（new_starts[i] = -1.0），
         其字幕条目会被丢弃——这句话已不在音频中

    Returns:
        (new_starts, scores): 每句的新起始时间（秒）与匹配置信度；
        scores[i] < 0 表示该句未得到有效匹配（可能已删除或未能定位）。
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

    # 只按置信度判可靠性，不比较与原始位置的偏差：
    # 调整过后的音频本身就可能把句子移到任意位置（重排/增删停顿），
    # 用旧时间轴做偏差过滤会把正确匹配误判为假匹配。
    reliable = [
        coarse_starts[i] >= 0 and coarse_conf[i] >= CONFIDENCE_THRESHOLD
        for i in range(n)
    ]

    # 不做单调性（LIS）剔除：用户可能调整了语序，高置信度的非单调
    # 匹配应予保留。低置信句由下方锚点区间重匹配处理。

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

    # ── 能量分割回退：只产生"待验证提示"，绝不直接采用 ──
    # 调整过后的音频可能删过句子，能量分割凑出的段数/位置都可能是错的；
    # 因此只把这些位置当作粗提示，必须通过高采样率互相关验证，
    # 验证不过就判为缺失（该句已不在音频中）。
    hints: dict[int, float] = {}
    if reliable_count == 0 or (n - reliable_count) / max(n, 1) >= 0.1:
        fallback_starts = _energy_based_segment(
            modified_wav_path, n, expected_durations=original_durations
        )
        if len(fallback_starts) == n:
            for i in range(n):
                if not reliable[i]:
                    hints[i] = fallback_starts[i]
            logger.warning(
                "可靠匹配 %d/%d，能量分割产生 %d 个待验证提示",
                reliable_count, n, len(hints),
            )

    # ── 第二遍：高采样率小窗口精修（含提示验证）──
    full_fine = _load_mono(modified_wav_path, TARGET_SR)
    fine_duration = len(full_fine) / TARGET_SR
    new_starts = [-1.0] * n
    scores = [-1.0] * n
    for i in range(n):
        if reliable[i]:
            center, margin = coarse_starts[i], REFINE_MARGIN
        elif i in hints:
            center, margin = hints[i], HINT_REFINE_MARGIN
        else:
            continue
        tpl = _load_mono(sentence_wavs[i], TARGET_SR)
        tpl_dur = len(tpl) / TARGET_SR
        w_start = max(0.0, center - margin)
        w_end = min(fine_duration, center + tpl_dur + margin)
        seg = full_fine[int(w_start * TARGET_SR): int(w_end * TARGET_SR)]
        if len(seg) >= len(tpl):
            st, cf = _cross_correlate_match(
                tpl, seg, TARGET_SR, w_start, expected_start=center
            )
            if st >= 0 and cf >= CONFIDENCE_THRESHOLD:
                new_starts[i], scores[i] = st, cf
                continue
        # 精修/验证失败：可靠句保留粗匹配位置，提示句判为缺失
        if reliable[i]:
            new_starts[i], scores[i] = coarse_starts[i], coarse_conf[i]

    # ── 标记缺失句 ──
    # 精修后置信度仍低于阈值的句子（含能量提示验证失败的），
    # 标记为缺失（new_starts=-1）：这句话已不在音频中，
    # 其字幕条目会由 recalibrate_entries 丢弃，而不是插值到幽灵位置。
    missing = 0
    for i in range(n):
        if scores[i] >= CONFIDENCE_THRESHOLD and new_starts[i] >= 0:
            continue
        new_starts[i] = -1.0
        scores[i] = -1.0
        missing += 1

    matched = n - missing
    logger.info("对齐完成: %d 句, 成功匹配 %d 句, 缺失 %d 句",
                n, matched, missing)
    return new_starts, scores


def build_time_mapper(
    old_starts: list[float],
    old_durations: list[float],
    new_starts: list[float],
) -> Callable[[float], float]:
    """
    构建时间映射函数: new_t = mapper(old_t)。

    每句的音频内容不变（仍是同一份 WAV），只是被放到了新位置，
    因此映射是**逐句刚性平移**：new_t = t + delta[i]，
    delta[i] = new_starts[i] - old_starts[i]。

    不能按"相邻句线性插值"映射：调整过后的音频可能改变了语序，
    旧时间轴上相邻的几句在新音频里未必相邻（甚至可能先后的顺序调换），
    插值会把字幕推到完全错误的位置。

    缺失句（new_starts[i] < 0，已被删除或未能匹配）没有可靠平移量，
    对应字幕条目由 recalibrate_entries 丢弃；此处用相邻可用句的
    平移量兜底，保证不会算出负时间。
    """
    n = len(old_starts)
    if n == 0:
        return lambda t: t

    deltas: list[Optional[float]] = [
        new_starts[i] - old_starts[i] if new_starts[i] >= 0 else None
        for i in range(n)
    ]

    # 前向/后向最近的可用平移量，供缺失句兜底
    prev_shift = [0.0] * n
    last: Optional[float] = None
    for i in range(n):
        if deltas[i] is not None:
            last = deltas[i]
        if last is not None:
            prev_shift[i] = last
    next_shift = [0.0] * n
    nxt: Optional[float] = None
    for i in range(n - 1, -1, -1):
        if deltas[i] is not None:
            nxt = deltas[i]
        if nxt is not None:
            next_shift[i] = nxt

    def map_time(t: float) -> float:
        i = bisect_right(old_starts, t) - 1
        if i < 0:
            i = 0
        elif i >= n:
            i = n - 1
        d = deltas[i]
        if d is None:
            d = prev_shift[i] if prev_shift[i] else next_shift[i]
        return t + d

    return map_time


def sentence_of(
    t: float,
    old_starts: list[float],
) -> int:
    """时间戳 t 所属的句子下标（按句子起点二分）。"""
    n = len(old_starts)
    if n == 0:
        return -1
    i = bisect_right(old_starts, t) - 1
    if i < 0:
        return 0
    if i >= n:
        return n - 1
    return i


def recalibrate_entries(
    entries: list[SubtitleEntry],
    old_sentence_starts: list[float],
    old_sentence_durations: list[float],
    new_sentence_starts: list[float],
) -> tuple[list[SubtitleEntry], int]:
    """
    用对齐结果重新映射字幕条目的时间戳。

    每句音频内容未变，译文条目随所在句整体平移。已被删除/未能匹配的
    句子（new_sentence_starts[i] < 0）说明这句话已不在音频里，
    其字幕条目保留下来只会错位显示，因此直接丢弃并计数。

    Returns:
        (新字幕列表, 丢弃的条目数)
    """
    n = len(old_sentence_starts)
    if n == 0:
        return list(entries), 0

    deltas: list[Optional[float]] = [
        new_sentence_starts[i] - old_sentence_starts[i]
        if new_sentence_starts[i] >= 0
        else None
        for i in range(n)
    ]

    new_entries: list[SubtitleEntry] = []
    dropped = 0
    for e in entries:
        i = sentence_of((e.start_sec + e.end_sec) / 2.0, old_sentence_starts)
        d = deltas[i]
        if d is None:
            dropped += 1
            continue
        new_start = e.start_sec + d
        new_end = e.end_sec + d
        if new_end <= new_start:
            new_end = new_start + 0.1
        new_entries.append(
            SubtitleEntry(
                index=len(new_entries) + 1,
                start_sec=round(max(0.0, new_start), 3),
                end_sec=round(new_end, 3),
                text=e.text,
            )
        )
    return new_entries, dropped
