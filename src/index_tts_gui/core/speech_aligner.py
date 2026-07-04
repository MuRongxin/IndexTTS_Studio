"""
语音对齐：通过 FFT 互相关在修改后的音频中定位每句原始 WAV 的位置，
重新计算字幕时间戳。当互相关匹配置信度不足时回退到能量分割法。
"""
import logging
from typing import Callable

import numpy as np
import librosa
import scipy.signal

from index_tts_gui.core.merger import get_wav_duration
from index_tts_gui.core.subtitle import SubtitleEntry


logger = logging.getLogger(__name__)

TARGET_SR = 8000
CONFIDENCE_THRESHOLD = 0.25
FALLBACK_RATIO = 0.3
SEARCH_BEFORE = 3.0
SEARCH_AFTER = 8.0


def _load_mono(wav_path: str, target_sr: int = TARGET_SR) -> np.ndarray:
    """加载音频为 mono 并重采样到 target_sr。"""
    y, _sr = librosa.load(wav_path, sr=target_sr, mono=True)
    return y.astype(np.float32)


def _cross_correlate_match(
    template: np.ndarray,
    signal_segment: np.ndarray,
    sr: int,
    search_start: float,
) -> tuple[float, float]:
    """
    在 signal_segment 中匹配 template，返回 (start_time_in_original_audio, confidence)。
    返回的时间相对于完整音频的起始（search_start + 匹配偏移）。
    """
    t = template.copy()
    s = signal_segment.copy()

    t = (t - np.mean(t)) / (np.std(t) + 1e-10)
    s = (s - np.mean(s)) / (np.std(s) + 1e-10)

    if len(t) > len(s):
        return -1.0, 0.0

    corr = scipy.signal.correlate(s, t, method="fft")

    peak_idx = int(np.argmax(np.abs(corr)))
    peak_val = float(np.abs(corr[peak_idx]))
    confidence = peak_val / len(t)

    lag = peak_idx - (len(t) - 1)
    start_sample = max(0, lag)
    start_time = search_start + start_sample / sr

    return start_time, confidence


def _energy_based_segment(
    wav_path: str, num_expected: int, sr: int = 16000
) -> list[float]:
    """按能量检测语音段起止，返回各段起始时间。"""
    y, _sr = librosa.load(wav_path, sr=sr, mono=True)
    y = y.astype(np.float32)

    hop = int(sr * 0.010)
    frame = int(sr * 0.025)
    rms = librosa.feature.rms(y=y, frame_length=frame, hop_length=hop)[0]

    max_rms = float(np.max(rms))
    if max_rms <= 0:
        return []

    for threshold_factor in [0.03, 0.02, 0.05, 0.01, 0.015]:
        thresh = max_rms * threshold_factor
        is_speech = rms > thresh

        segments: list[tuple[float, float]] = []
        in_speech = False
        speech_start = 0
        for i, s in enumerate(is_speech):
            if s and not in_speech:
                speech_start = i
                in_speech = True
            elif not s and in_speech:
                dur = (i - speech_start) * 0.010
                if dur > 0.15:
                    segments.append(
                        (speech_start * 0.010, i * 0.010)
                    )
                in_speech = False
        if in_speech:
            dur = (len(is_speech) - speech_start) * 0.010
            if dur > 0.15:
                segments.append(
                    (speech_start * 0.010, len(is_speech) * 0.010)
                )

        if len(segments) == num_expected:
            logger.info("能量分割匹配成功: threshold=%.3f segments=%d",
                        threshold_factor, len(segments))
            return [s[0] for s in segments]

        if len(segments) > 0:
            logger.info("能量分割尝试: threshold=%.3f segments=%d (期望 %d)",
                        threshold_factor, len(segments), num_expected)

    if segments:
        logger.warning("能量分割未匹配期望数量，使用最接近结果: %d vs %d",
                       len(segments), num_expected)
        if len(segments) > num_expected:
            return [s[0] for s in segments[:num_expected]]
        return [s[0] for s in segments]

    logger.error("能量分割完全失败")
    return []


def align_sentences(
    modified_wav_path: str,
    sentence_wavs: list[str],
    sentences: list[str],
    original_pauses: list[float],
) -> list[float]:
    """
    在修改后的 full_dub.wav 中定位每句原始 WAV 的位置。

    Returns:
        new_starts: 每句在修改后音频中的新起始时间（秒）
    """
    n = len(sentence_wavs)
    if n == 0:
        return []

    original_durations = [get_wav_duration(p) for p in sentence_wavs]

    logger.info("加载修改后音频: %s", modified_wav_path)
    full_audio = _load_mono(modified_wav_path, TARGET_SR)
    full_duration = len(full_audio) / TARGET_SR
    logger.info("修改后音频时长: %.2fs", full_duration)

    new_starts: list[float] = [-1.0] * n
    scores: list[float] = [0.0] * n
    low_confidence_count = 0

    for i in range(n):
        template = _load_mono(sentence_wavs[i], TARGET_SR)
        template_dur = len(template) / TARGET_SR

        if i == 0:
            search_start = 0.0
            search_window = min(
                original_durations[0] + original_pauses[0] * 3 + SEARCH_AFTER,
                full_duration,
            )
        else:
            prev_end = new_starts[i - 1] + original_durations[i - 1]
            search_start = max(0.0, prev_end - SEARCH_BEFORE)
            remaining = full_duration - search_start
            search_window = min(
                original_pauses[i - 1] * 3 + template_dur + SEARCH_AFTER,
                remaining,
            )

        start_sample = int(search_start * TARGET_SR)
        end_sample = int((search_start + search_window) * TARGET_SR)
        end_sample = min(end_sample, len(full_audio))
        signal_segment = full_audio[start_sample:end_sample]

        if len(signal_segment) < len(template):
            logger.warning("句子 %d 搜索窗口小于模板长度，标记为失败", i)
            low_confidence_count += 1
            if i > 0:
                new_starts[i] = new_starts[i - 1] + original_durations[i - 1]
            continue

        start_time, confidence = _cross_correlate_match(
            template, signal_segment, TARGET_SR, search_start
        )

        if start_time >= 0 and confidence >= CONFIDENCE_THRESHOLD:
            new_starts[i] = start_time
            scores[i] = confidence
        else:
            low_confidence_count += 1
            new_starts[i] = start_time if start_time >= 0 else -1.0
            scores[i] = confidence

        logger.debug("句子 %d/%d: start=%.3f conf=%.3f", i + 1, n, start_time, confidence)

    if low_confidence_count / max(n, 1) > FALLBACK_RATIO:
        logger.warning("低置信度句子过多 (%d/%d)，回退到能量分割",
                       low_confidence_count, n)
        fallback_starts = _energy_based_segment(modified_wav_path, n)
        if len(fallback_starts) == n:
            logger.info("能量分割成功，使用能量分割结果")
            return fallback_starts
        else:
            logger.warning("能量分割结果不完整 (%d/%d)，修复互相关结果",
                           len(fallback_starts), n)

    # 修复无效的 new_starts：用前一句位置 + 前句时长推算
    for i in range(n):
        if new_starts[i] < 0:
            if i > 0:
                new_starts[i] = new_starts[i - 1] + original_durations[i - 1]
            else:
                new_starts[i] = 0.0
            scores[i] = -1.0

    logger.info("对齐完成: %d 句, 低置信度 %d 句", n, low_confidence_count)
    return new_starts


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

        for i in range(n):
            if old_starts[i] <= t <= old_ends[i]:
                return new_starts[i] + (t - old_starts[i])

            if i < n - 1 and old_ends[i] < t < old_starts[i + 1]:
                old_gap = old_starts[i + 1] - old_ends[i]
                new_gap = new_starts[i + 1] - new_ends[i]
                if old_gap > 1e-6:
                    ratio = (t - old_ends[i]) / old_gap
                    return new_ends[i] + ratio * max(new_gap, 0.0)
                else:
                    return new_ends[i] + (t - old_ends[i])

        return t + (new_ends[-1] - old_ends[-1])

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
