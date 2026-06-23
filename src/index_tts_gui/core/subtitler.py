"""
字幕生成：能量检测 + 文本切分 → SRT
"""
import re
import numpy as np
import librosa
import soundfile as sf
from dataclasses import dataclass


@dataclass
class SubtitleEntry:
    index: int
    start_sec: float
    end_sec: float
    text: str


def generate_srt(
    full_audio_path: str,
    manuscript_text: str,
    sentence_wavs: list[str],
    max_chars: int = 24,
) -> list[SubtitleEntry]:
    """
    生成字幕条目列表。
    
    策略：
    1. 按句末标点拆分原文
    2. 每个句子 WAV 获取精确时长
    3. 对长句（>max_chars）用能量检测找内部停顿
    4. 在停顿 + 标点处切分子幕
    
    Args:
        full_audio_path: 完整音频路径（用于波形检测，备用）
        manuscript_text: 原文全文
        sentence_wavs: 按顺序的每句 WAV 路径列表
        max_chars: 每条字幕最大字符数
    
    Returns:
        SubtitleEntry 列表
    """
    # 1. 分句
    sentences = _split_manuscript(manuscript_text)

    # 2. 每句时长
    durations = [_get_duration(p) for p in sentence_wavs]

    entries = []
    idx = 1
    cumulative = 0.0

    for si, (sentence, dur) in enumerate(zip(sentences, durations)):
        if si >= len(sentence_wavs):
            break

        start_t = cumulative
        end_t = cumulative + dur

        if len(sentence) <= max_chars:
            entries.append(SubtitleEntry(idx, start_t, end_t, sentence))
            idx += 1
        else:
            # 能量检测长句内停顿
            wav_path = sentence_wavs[si]
            pauses = _detect_pauses(wav_path)
            chunks = _split_by_pauses(sentence, pauses, max_chars)

            total_cc = sum(len(c) for c in chunks)
            chunk_start = start_t
            for ci, c in enumerate(chunks):
                ratio = len(c) / total_cc if total_cc > 0 else 1 / len(chunks)
                chunk_end = chunk_start + max(ratio * dur, 0.8)
                if ci == len(chunks) - 1:
                    chunk_end = end_t

                entries.append(SubtitleEntry(idx, chunk_start, chunk_end, c))
                idx += 1
                chunk_start = chunk_end

        cumulative += dur

    return entries


def entries_to_srt(entries: list[SubtitleEntry]) -> str:
    """将字幕条目转为 SRT 字符串"""
    def _fmt(sec: float) -> str:
        h = int(sec // 3600)
        m = int((sec % 3600) // 60)
        s = int(sec % 60)
        ms = int((sec - int(sec)) * 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    lines = []
    for e in entries:
        lines.append(str(e.index))
        lines.append(f"{_fmt(e.start_sec)} --> {_fmt(e.end_sec)}")
        lines.append(e.text)
        lines.append("")
    return "\n".join(lines)


# ── 内部辅助 ──

def _split_manuscript(text: str) -> list[str]:
    text = re.sub(r'\n+', '', text)
    raw = re.split(r'(?<=[。！？])', text)
    raw = [s.strip() for s in raw if s.strip()]
    merged = []
    for s in raw:
        if merged and not re.match(
            r'^[\u4e00-\u9fff\u201c\u2018\u300c\uff08（"“\[]', s
        ):
            merged[-1] += s
        else:
            merged.append(s)
    return merged


def _get_duration(wav_path: str) -> float:
    import subprocess, json
    r = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
         "-of", "json", wav_path],
        capture_output=True, text=True,
    )
    return float(json.loads(r.stdout)["format"]["duration"])


PUNCT = '。！？；：，、'


def _detect_pauses(wav_path: str) -> list[float]:
    """返回归一化停顿位置列表（0~1）"""
    try:
        y, sr = sf.read(wav_path)
        hop = int(sr * 0.010)
        frame = int(sr * 0.025)
        rms = librosa.feature.rms(y=y, frame_length=frame, hop_length=hop)[0]
        thresh = np.max(rms) * 0.03
        is_speech = rms > thresh

        min_gap = int(0.15 / 0.010)
        pauses = []
        in_silence = False
        silence_start = 0
        for i, speech in enumerate(is_speech):
            if not speech and not in_silence:
                silence_start = i
                in_silence = True
            elif speech and in_silence:
                dur = (i - silence_start) * 0.010
                if dur > 0.15:
                    mid = (silence_start + i) / 2
                    pauses.append(mid / len(is_speech))
                in_silence = False

        if pauses:
            filtered = [pauses[0]]
            for p in pauses[1:]:
                if p - filtered[-1] > 0.08:
                    filtered.append(p)
            return filtered
        return []
    except Exception:
        return []


def _split_by_pauses(
    sentence: str, pauses: list[float], max_chars: int
) -> list[str]:
    """按停顿位置切分文本"""
    if not pauses or len(sentence) <= max_chars:
        return [sentence]

    total = len(sentence)
    cuts = [int(p * total) for p in pauses]
    cuts = [c for c in cuts if 4 < c < total - 4]
    if not cuts:
        return [sentence]

    # 在标点处微调
    adjusted = []
    for cut in cuts:
        best = cut
        for offset in range(8):
            pos = cut + offset
            if pos < total and sentence[pos] in PUNCT:
                best = pos + 1
                break
            pos = cut - offset
            if pos >= 0 and sentence[pos] in PUNCT:
                best = pos + 1
                break
        adjusted.append(best)
    adjusted = sorted(set(adjusted))

    chunks = []
    prev = 0
    for cut in adjusted:
        if cut > prev:
            chunks.append(sentence[prev:cut])
            prev = cut
    if prev < total:
        chunks.append(sentence[prev:])

    # 硬切超长
    final = []
    for c in chunks:
        if len(c) > max_chars:
            sub = re.split(rf'(?<=[{PUNCT}])', c)
            sub = [s for s in sub if s]
            merged_sub = []
            for s in sub:
                if merged_sub and len(s) + len(merged_sub[-1]) <= max_chars:
                    merged_sub[-1] += s
                else:
                    merged_sub.append(s)
            for s in merged_sub:
                while len(s) > max_chars:
                    final.append(s[:max_chars])
                    s = s[max_chars:]
                if s:
                    final.append(s)
        else:
            final.append(c)
    return final
