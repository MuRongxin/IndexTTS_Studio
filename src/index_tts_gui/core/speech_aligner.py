"""
语音对齐：先在低采样率下对全文做逐句独立粗匹配（互不锚定，避免错误传播），
低置信句在相邻高置信句区间内带位置先验重匹配，最后在高采样率小窗口内精修，
重新计算字幕时间戳。

匹配置信度使用精确 NCC（逐窗去均值归一化互相关，[-1, 1]）：
同一份录音 ≈ 0.9+，不同录音（哪怕同文本重新合成）< 0.5。
只认正相关峰，避免反相噪声冒充匹配。

支持语序调整和句子删除：不强制单调，每句独立定位到新音频中的真实位置；
不做相邻句插值，否则语序改变后会整体错位。未能匹配的句子标记为 -1。

字幕映射按**句内切片**逐端点进行，而不是整句刚性平移：句内停顿位置在
生成字幕时就已知（subtitler.pause_offsets），把它当作额外的锚点后，
"在逗号处插入间隔"会被正确表达为字幕拉伸，"删掉句首/句尾那半"能被检出
并如实上报，而不是让字幕假装没变。没有切片信息时退化为整句刚性平移。
"""
import logging
from bisect import bisect_right
from dataclasses import dataclass, field
from typing import Optional

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

# ── 句内切片级对齐 ──
# 一个分句只有一个锚点（新起点）时，句内发生的任何位置变化都无法表达：
# 用户在逗号停顿处插入了间隔，字幕尾部就会整体滞后这么多秒。
# 句内停顿位置在生成字幕时就已经知道（见 subtitler.pause_offsets），
# 把对齐粒度从"分句"降到"句内切片"，即可让每个切片各自获得位移。

#: 切片模板比整句短得多，更容易撞出假峰，阈值必须比整句更严
SLICE_CONFIDENCE_THRESHOLD = 0.6
#: 切片起点相对整句刚性预测的允许偏移（秒）；超过说明句内被拉伸/压缩过
SLICE_MAX_SHIFT = 2.0


@dataclass
class AlignResult:
    """一次对齐的完整结果。

    Attributes:
        starts: 每句新起点（秒），-1 表示未匹配
        scores: 每句匹配置信度，-1 表示未匹配
        slice_deltas: [句][切片] 的位移（秒）。内层list 为空表示该句
                      没有句内切片信息（或未匹配），此时映射退化为整句
                      刚性平移。元素为 None 表示该切片未能在新音频中定位。
    """

    starts: list[float]
    scores: list[float]
    slice_deltas: list[list[Optional[float]]] = field(default_factory=list)


def _load_mono(wav_path: str, target_sr: int = TARGET_SR) -> np.ndarray:
    """加载音频为 mono 并重采样到 target_sr。空文件返回空数组。"""
    y, _sr = librosa.load(wav_path, sr=target_sr, mono=True)
    if len(y) == 0:
        logger.warning("音频为空: %s", wav_path)
        return np.zeros(0, dtype=np.float32)
    return y.astype(np.float32)


def cumulative_starts(
    durations: list[float],
    pauses: list[float] | None = None,
) -> list[float]:
    """由各句时长与句后停顿累计出每句起始时间。

    pauses 不足时用 0.0 补齐、多余部分忽略。UI 侧的配音/校准流程与本
    模块的粗匹配共用同一套累计规则，避免三处实现各自漂移。
    """
    n = len(durations)
    src = list(pauses) if pauses else []
    pauses = src + [0.0] * max(0, n - len(src))
    starts: list[float] = []
    acc = 0.0
    for i in range(n):
        starts.append(acc)
        acc += durations[i] + pauses[i]
    return starts


def is_matched(start: float, score: float) -> bool:
    """该句是否得到有效匹配：位置有效且置信度达到阈值。"""
    return start >= 0 and score >= CONFIDENCE_THRESHOLD


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
    # 空模板/空信号/全零（无方差）模板都会让后续归一化除零，直接判无匹配
    if L == 0 or N == 0 or L > N or nt < 1e-9:
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
) -> list[float]:
    """按能量检测语音段起止，返回各段起始时间。

    尝试多个阈值，取段数接近期望句数的结果，再用合并/分裂凑齐期望数量。
    """
    y, _sr = librosa.load(wav_path, sr=sr, mono=True)
    y = y.astype(np.float32)
    if len(y) == 0:
        logger.error("音频为空，无法做能量分割: %s", wav_path)
        return []

    hop = int(sr * 0.010)
    frame = int(sr * 0.025)
    rms = librosa.feature.rms(y=y, frame_length=frame, hop_length=hop)[0]
    if len(rms) == 0:
        logger.error("音频过短，无法做能量分割: %s", wav_path)
        return []

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


def _audio_order_bounds(
    n: int,
    starts: list[float],
    scores: list[float],
    audio_duration: float,
) -> dict[int, tuple[float, float]]:
    """给每个可靠句算出它在新音频里允许占据的区间。

    邻居按**新音频中的实际先后**取，而不是按数组下标取。原实现用
    下标邻居（i 前后最近的可靠句），用户调换语序后下标邻居 ≠ 音频邻居，
    搜索窗口会把目标句直接排除在外，重试必然失败。

    区间取开区间 (lo, hi)：句子内容不会越过两侧可靠句的起点。
    """
    ordered = sorted(
        (j for j in range(n) if is_matched(starts[j], scores[j])),
        key=lambda j: starts[j],
    )
    bounds: dict[int, tuple[float, float]] = {}
    for rank, j in enumerate(ordered):
        lo = starts[ordered[rank - 1]] if rank > 0 else 0.0
        hi = starts[ordered[rank + 1]] if rank + 1 < len(ordered) else audio_duration
        bounds[j] = (lo, hi)
    return bounds


def _locate_all_slice_deltas(
    full_fine: np.ndarray,
    sr: int,
    sentence_wavs: list[str],
    slice_offsets: list[list[float]] | None,
    starts: list[float],
    scores: list[float],
    audio_duration: float,
) -> list[list[Optional[float]]]:
    """定位每个可靠句的句内切片，返回 [句][切片] 的位移。

    搜索区间就是该句在音频里允许占据的范围（两侧可靠句之间），
    因此不会越界误配到别的句子上 —— 这是切片模板变短之后还能保持
    可信的关键。
    """
    n = len(sentence_wavs)
    empty: list[list[Optional[float]]] = [[] for _ in range(n)]
    if not slice_offsets:
        return empty

    bounds = _audio_order_bounds(n, starts, scores, audio_duration)
    out: list[list[Optional[float]]] = [[] for _ in range(n)]

    for i in range(n):
        offsets = slice_offsets[i] if i < len(slice_offsets) else []
        if not offsets or not is_matched(starts[i], scores[i]):
            continue
        lo, hi = bounds.get(i, (0.0, audio_duration))
        deltas = _locate_one_sentence_slices(
            full_fine, sr, sentence_wavs[i], offsets, starts[i], lo, hi,
        )
        if deltas:
            out[i] = deltas
    located = sum(1 for d in out if d)
    if located:
        logger.info("句内切片定位: %d/%d 句获得切片位移", located, n)
    return out


def _locate_one_sentence_slices(
    full_fine: np.ndarray,
    sr: int,
    sentence_wav: str,
    offsets: list[float],
    new_start: float,
    lo: float,
    hi: float,
) -> list[Optional[float]]:
    """定位一个分句内部各切片的位移。

    offsets 是内部停顿在原始句内的秒偏移。切片 0 的起点就是句首，
    位移直接等于整句位移，无需再匹配；其余切片在 (lo, hi) 内做内容匹配。

    匹配到的位置偏离"刚性预测"超过 SLICE_MAX_SHIFT 时不采信 —— 那说明
    这次匹配抓到的是别处的相似内容，而不是本句的切片。
    """
    original = _load_mono(sentence_wav, TARGET_SR)
    if len(original) == 0:
        return []
    bounds = [0.0, *offsets, len(original) / sr]
    n_slices = len(bounds) - 1
    if n_slices < 2:
        return []

    sentence_delta = new_start - 0.0
    # 切片 0 也要实测：用户把句首那半删掉时，只有实测才能发现，
    # 否则会退化成刚性平移、让字幕假装没变（实测里就是这样）。
    deltas: list[Optional[float]] = []

    seg_lo = max(0, int(lo * sr))
    seg_hi = min(len(full_fine), int(hi * sr))
    segment = full_fine[seg_lo:seg_hi]
    if len(segment) < len(original):
        return [sentence_delta] + [None] * (n_slices - 1)

    for k in range(n_slices):
        t_start = bounds[k]
        t_end = bounds[k + 1]
        template = original[int(t_start * sr): int(t_end * sr)]
        if len(template) < int(0.15 * sr):
            deltas.append(None)
            continue
        predicted = t_start + sentence_delta
        st, cf = _cross_correlate_match(
            template, segment, sr, seg_lo / sr, expected_start=predicted,
        )
        if not is_matched(st, cf) or cf < SLICE_CONFIDENCE_THRESHOLD:
            deltas.append(None)
            continue
        delta = st - t_start
        if abs(delta - sentence_delta) > SLICE_MAX_SHIFT:
            logger.info(
                "切片 %d 匹配位置 %.2f 偏离刚性预测 %.2f 过多，不采信",
                k, st, predicted,
            )
            deltas.append(None)
            continue
        deltas.append(delta)

    # 一个切片都定位不到，说明这里的切片匹配不可靠（噪声等），而不是
    # 内容被删除 —— 退化为整句刚性平移，与历史行为一致，不牵连用户。
    if all(d is None for d in deltas):
        return [sentence_delta] + [None] * (n_slices - 1)
    return deltas


def align_sentences_detailed(
    modified_wav_path: str,
    sentence_wavs: list[str],
    sentences: list[str],
    original_pauses: list[float],
    slice_offsets: list[list[float]] | None = None,
) -> AlignResult:
    """
    在调整后的音频中定位每句原始 WAV 的位置，并给出句内切片的位移。

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
      6. 句内切片级定位：对每个可靠句，把它的句内停顿位置也定位一遍，
         使句内插入/删除间隔能被正确映射（见 _locate_all_slice_deltas）

    Args:
        slice_offsets: [句][内部停顿秒偏移]，由 subtitler.pause_offsets 产出。
            为 None 或空列表时只做整句对齐，退化为历史上的刚性平移。

    Returns:
        AlignResult：整句定位 + 句内切片位移
    """
    n = len(sentence_wavs)
    if n == 0:
        return AlignResult([], [], [])

    original_durations = [get_wav_duration(p) for p in sentence_wavs]
    old_starts = cumulative_starts(original_durations, original_pauses)

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
        if len(tpl) == 0 or len(tpl) > len(full_coarse):
            logger.warning(
                "句子 %d 模板为空或长于整段音频，标记为失败", i + 1
            )
            continue
        st, cf = _cross_correlate_match(tpl, full_coarse, COARSE_SR, 0.0)
        coarse_starts[i] = st
        coarse_conf[i] = cf
        logger.debug("句子 %d/%d 粗匹配: start=%.3f conf=%.3f", i + 1, n, st, cf)

    # 只按置信度判可靠性，不比较与原始位置的偏差：
    # 调整过后的音频本身就可能把句子移到任意位置（重排/增删停顿），
    # 用旧时间轴做偏差过滤会把正确匹配误判为假匹配。
    reliable = [
        is_matched(coarse_starts[i], coarse_conf[i]) for i in range(n)
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
        if is_matched(st, cf):
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
        fallback_starts = _energy_based_segment(modified_wav_path, n)
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
        if len(tpl) == 0:
            # 空模板无法匹配，可靠句回落到粗匹配位置
            if reliable[i]:
                new_starts[i], scores[i] = coarse_starts[i], coarse_conf[i]
            continue
        tpl_dur = len(tpl) / TARGET_SR
        w_start = max(0.0, center - margin)
        w_end = min(fine_duration, center + tpl_dur + margin)
        seg = full_fine[int(w_start * TARGET_SR): int(w_end * TARGET_SR)]
        if len(seg) >= len(tpl):
            st, cf = _cross_correlate_match(
                tpl, seg, TARGET_SR, w_start, expected_start=center
            )
            if is_matched(st, cf):
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
        if is_matched(new_starts[i], scores[i]):
            continue
        new_starts[i] = -1.0
        scores[i] = -1.0
        missing += 1

    matched = n - missing
    logger.info("对齐完成: %d 句, 成功匹配 %d 句, 缺失 %d 句",
                n, matched, missing)

    slice_deltas = _locate_all_slice_deltas(
        full_fine, TARGET_SR, sentence_wavs, slice_offsets,
        new_starts, scores, fine_duration,
    )
    return AlignResult(new_starts, scores, slice_deltas)


def align_sentences(
    modified_wav_path: str,
    sentence_wavs: list[str],
    sentences: list[str],
    original_pauses: list[float],
) -> tuple[list[float], list[float]]:
    """整句对齐（历史接口）。需要句内切片精度请用 align_sentences_detailed。"""
    result = align_sentences_detailed(
        modified_wav_path, sentence_wavs, sentences, original_pauses
    )
    return result.starts, result.scores


class SentenceTimeMap:
    """把一个分句内的原始时间映射到新音频时间。

    映射按**切片**而非整句：时间落在哪个切片，就用那个切片自己的位移。
    因此"句内插入间隔"这类编辑会被正确表达为字幕拉伸，而不是整体平移。
    切片信息缺失或某个切片未定位时，退化为整句刚性平移。
    """

    #: 端点归属的边界容差（秒）。右端点按左闭右开处理，避免恰好落在
    #: 句子/切片起点上的字幕被误判为属于后者。
    _EPS = 1e-6

    def __init__(self, duration: float, offsets: list[float] | None,
                 deltas: list[Optional[float]] | None, sentence_delta: float):
        bounds = [0.0]
        bounds.extend(offsets or [])
        bounds.append(max(float(duration), 0.0))
        bounds = sorted(set(round(b, 6) for b in bounds))
        self._bounds = bounds
        self._deltas = list(deltas) if deltas else []
        self._fallback = sentence_delta

    @property
    def slice_count(self) -> int:
        return max(0, len(self._bounds) - 1)

    def slice_of(self, t: float, *, at_end: bool = False) -> int:
        """t 所属的切片下标。

        at_end=True 用于字幕的右端点：右端点是开区间上的排他边界，
        真正被包含的最后一个瞬间是 t - ε。不做这个处理的话，每条恰好
        结束在切片/句子起点上的字幕都会被误判为属于后一段。
        """
        n = self.slice_count
        if n <= 0:
            return -1
        probe = (t - self._EPS) if at_end else t
        k = bisect_right(self._bounds, probe) - 1
        return max(0, min(k, n - 1))

    def delta_of(self, k: int) -> Optional[float]:
        """切片 k 的位移。

        None 表示"该切片在新音频里没找到"，调用方据此判定内容缺失。
        只有在完全没有句内切片信息时才退化为整句位移 —— 这个区别很关键：
        两者都返回 fallback，会让"句首那半被删掉"被当成"没变"，字幕
        假装没变，比报错更糟。
        """
        if not self._deltas:
            return self._fallback
        if 0 <= k < len(self._deltas):
            return self._deltas[k]
        return self._fallback

    def has_slice_info(self) -> bool:
        return bool(self._deltas)

    def map(self, t: float, *, at_end: bool = False) -> Optional[float]:
        """把原始时间 t 映射到新音频时间；整句缺失时返回 None。"""
        k = self.slice_of(t, at_end=at_end)
        if k < 0:
            return None
        d = self.delta_of(k)
        if d is None:
            return None
        return t + d


def build_time_maps(
    old_sentence_starts: list[float],
    old_sentence_durations: list[float],
    result: AlignResult,
    slice_offsets: list[list[float]] | None = None,
) -> list[Optional[SentenceTimeMap]]:
    """为每个分句构造时间映射；该句未匹配时为 None。"""
    n = len(old_sentence_starts)
    maps: list[Optional[SentenceTimeMap]] = []
    slice_deltas = result.slice_deltas or []
    for i in range(n):
        if not is_matched(result.starts[i], result.scores[i]):
            maps.append(None)
            continue
        delta = result.starts[i] - old_sentence_starts[i]
        offsets = slice_offsets[i] if slice_offsets and i < len(slice_offsets) else []
        maps.append(SentenceTimeMap(
            old_sentence_durations[i] if i < len(old_sentence_durations) else 0.0,
            offsets,
            slice_deltas[i] if i < len(slice_deltas) else [],
            delta,
        ))
    return maps


@dataclass
class RecalibrateReport:
    """重映射的结果分类，用于如实上报而不是静默产出错误结果。"""

    kept: int = 0
    dropped: list[int] = field(default_factory=list)          # 整条无法映射
    truncated_head: list[int] = field(default_factory=list)   # 起始切片缺失，锚定尾部
    truncated_tail: list[int] = field(default_factory=list)   # 结束切片缺失，锚定头部
    unswappable: list[int] = field(default_factory=list)      # 起止切片顺序被调换
    overlaps: list[tuple[int, int]] = field(default_factory=list)
    flat_spans: list[int] = field(default_factory=list)       # 映射后零长/负长

    @property
    def has_issues(self) -> bool:
        """是否存在需要用户过目的异常（正常重映射不会置位）。"""
        return bool(
            self.dropped or self.truncated_head or self.truncated_tail
            or self.unswappable or self.overlaps or self.flat_spans
        )

    def summary(self) -> str:
        bits = [f"保留 {self.kept} 条"]
        if self.dropped:
            bits.append(f"丢弃 {len(self.dropped)} 条（对应语音已不在音频中）")
        if self.truncated_head:
            bits.append(f"{len(self.truncated_head)} 条开头内容缺失（已锚定尾部）")
        if self.truncated_tail:
            bits.append(f"{len(self.truncated_tail)} 条结尾内容缺失（已锚定头部）")
        if self.unswappable:
            bits.append(
                f"{len(self.unswappable)} 条因前后语音顺序被调换、无法用单条字幕表达"
            )
        if self.flat_spans:
            bits.append(f"{len(self.flat_spans)} 条被压成最短时长")
        if self.overlaps:
            bits.append(f"{len(self.overlaps)} 处字幕重叠")
        return "，".join(bits)


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


def recalibrate_entries_detailed(
    entries: list[SubtitleEntry],
    old_sentence_starts: list[float],
    old_sentence_durations: list[float],
    new_sentence_starts: list[float],
    slice_offsets: list[list[float]] | None = None,
    slice_deltas: list[list[Optional[float]]] | None = None,
) -> tuple[list[SubtitleEntry], RecalibrateReport]:
    """逐端点归属地重新映射字幕时间戳。

    与 recalibrate_entries 的区别：start 与 end 各自按所属切片映射，
    而不是"看中点归谁、整条一起平移"。这样"句内插入间隔"会被正确表达为
    字幕拉伸，"整句移动"仍然表现为刚性平移。

    退化策略（都记入 report，不静默）：
    - 两端所属切片都缺失 -> 丢弃（内容已不在音频中）
    - 仅起始切片缺失 -> 锚定尾部、保持原时长（开头内容被删）
    - 仅结束切片缺失 -> 锚定头部、保持原时长（结尾内容被删）
    - 起止切片的新位置顺序颠倒 -> 单条连续字幕无法表达，丢弃并上报

    Returns:
        (新字幕列表, 结果分类报告)
    """
    n = len(old_sentence_starts)
    if n == 0:
        return list(entries), RecalibrateReport(kept=len(entries))

    fake = AlignResult(list(new_sentence_starts), [], list(slice_deltas or []))
    # scores 未单独传入时，用阈值从起点推导匹配状态
    fake.scores = [
        CONFIDENCE_THRESHOLD if s >= 0 else -1.0 for s in new_sentence_starts
    ]
    maps = build_time_maps(old_sentence_starts, old_sentence_durations, fake,
                           slice_offsets)

    report = RecalibrateReport()
    out: list[SubtitleEntry] = []
    for orig_index, e in enumerate(entries, 1):
        i = sentence_of(
            (e.start_sec + e.end_sec) / 2.0, old_sentence_starts
        )
        tm = maps[i] if 0 <= i < len(maps) else None
        if tm is None:
            report.dropped.append(orig_index)
            continue

        new_start = tm.map(e.start_sec)
        # 右端点按左闭右开处理：恰好落在切片起点的字幕仍属前一片
        new_end = tm.map(e.end_sec, at_end=True)

        if new_start is None and new_end is None:
            report.dropped.append(orig_index)
            continue
        if new_start is None:
            # 开头内容已被删除：锚定尾部并保留原时长
            new_start = new_end - max(0.0, e.end_sec - e.start_sec)
            report.truncated_head.append(orig_index)
        elif new_end is None:
            new_end = new_start + max(0.0, e.end_sec - e.start_sec)
            report.truncated_tail.append(orig_index)
        elif _is_swapped(tm, e):
            # 起止切片在音频里换了先后 —— 一条连续字幕表达不了
            report.unswappable.append(orig_index)
            continue

        new_start = max(0.0, new_start)
        if new_end <= new_start:
            new_end = new_start + 0.1
            report.flat_spans.append(orig_index)
        report.kept += 1
        out.append(SubtitleEntry(
            index=len(out) + 1,
            start_sec=round(new_start, 3),
            end_sec=round(new_end, 3),
            text=e.text,
        ))

    report.overlaps = _find_overlaps(out)
    return out, report


def report_lines(report: RecalibrateReport, limit: int = 6) -> list[str]:
    """把结果分类展开成给用户看的明细行。

    静默产出错误结果比报错更糟：字幕少了几条 / 位置错了，用户只有
    靠逐帧比对才发现。每类异常都要在这里明确说出来。
    """
    lines: list[str] = []

    def _brief(label: str, items: list, fmt=str) -> None:
        if not items:
            return
        shown = "、".join(fmt(x) for x in items[:limit])
        more = f" 等 {len(items)} 项" if len(items) > limit else ""
        lines.append(f"  {label}: {shown}{more}")

    _brief("内容已不在音频中（已移除）", report.dropped)
    _brief("开头内容缺失（已锚定尾部）", report.truncated_head)
    _brief("结尾内容缺失（已锚定头部）", report.truncated_tail)
    _brief("前后语音顺序被调换，单条字幕无法表达（已移除）",
           report.unswappable)
    if report.overlaps:
        pairs = "、".join(f"{a}↔{b}" for a, b in report.overlaps[:limit])
        more = f" 等 {len(report.overlaps)} 处" if len(report.overlaps) > limit else ""
        lines.append(f"  字幕重叠，请手工调整: {pairs}{more}")
    return lines


def _is_swapped(tm: SentenceTimeMap, entry: SubtitleEntry) -> bool:
    """起止切片在音频中的先后顺序是否被调换。"""
    if not tm.has_slice_info():
        return False
    k_start = tm.slice_of(entry.start_sec)
    k_end = tm.slice_of(entry.end_sec, at_end=True)
    if k_start < 0 or k_end < 0 or k_start == k_end:
        return False
    b = tm._bounds
    d_start, d_end = tm.delta_of(k_start), tm.delta_of(k_end)
    if d_start is None or d_end is None:
        return False
    return (b[k_start] + d_start) > (b[k_end] + d_end)


def _find_overlaps(entries: list[SubtitleEntry]) -> list[tuple[int, int]]:
    """找出映射后互相压住的字幕对（1-based 序号对）。

    重映射之后重叠是完全可能的（例如两半句对调后各自跟随内容），
    之前既不检测也不上报，界面上表现为两块字幕同时亮着。
    """
    overlaps: list[tuple[int, int]] = []
    for a, b in zip(entries, entries[1:]):
        if b.start_sec < a.end_sec - 1e-3:
            overlaps.append((a.index, b.index))
    return overlaps


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
