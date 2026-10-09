"""句子声学指纹 —— 让"同一段文本的另一次 TTS 渲染"仍能被识别。

波形互相关（NCC）判定身份的前提是**逐样本一致**：同一份录音 ≈ 0.9+，
同一段文本重新合成一遍 < 0.5。因此只要用户增量合成或单句重新生成过，
被改动的那句就无法定位，字幕会被判定为"已删除"而移除。

指纹走的是另一条路：MFCC 系数（去掉第 0 个整体能量项）的逐句均值与
标准差。它描述的是"这段话听起来是什么样"，而不是"这段波形长什么样"，
所以对重新渲染鲁棒。

指纹在**合并时**落盘（与 full_dub.wav 同源），描述的正是被拼进
full_dub.wav 的那一版take。之后即使该take 被重新合成，指纹仍然描述
full_dub.wav 里的内容 —— 这正是校准需要参照的对象。

落盘格式：与 WAV 同目录的 fingerprints.json，形如
    {"version": 1, "sr": 16000, "n_mfcc": 20,
     "fingerprints": {"1": [...整数...], "2": [...]}}
整数是量化后的均值/方差，便于 JSON 体积与可读性。
"""
from __future__ import annotations

import json
import logging
import os

import numpy as np


logger = logging.getLogger("index_tts")


#: 指纹提取用的采样率（与字幕生成侧无关，只要求自洽）
FINGERPRINT_SR = 16000
#: MFCC 系数个数；第 0 个是整体能量，丢弃以获得增益不变性
N_MFCC = 20
#: 每句指纹的整数个数 = (N_MFCC - 1) 均值 + (N_MFCC - 1) 标准差
FINGERPRINT_LEN = (N_MFCC - 1) * 2
#: 量化步长。MFCC 均值大致落在 ±100，用 4 得到约 0.25 的分辨率，
#: 远小于同文本/异文本之间的典型距离（实测 0.02 vs 0.16）
QUANT_SCALE = 4.0
#: 量化后的整数上下界，防止损坏数据造成离谱数值
QUANT_CLAMP = 32000

#: 判定"同文本"的最大余弦距离。实测同文本 ≈0.02~0.04、异文本 ≈0.16~0.21，
#: 取 0.08 留足裕量。低于此值才认为指纹匹配成功。
MATCH_THRESHOLD = 0.08

#: MFCC 的帧移。512@16kHz = 32ms，与逐句身份判定所需的精度匹配
HOP_LENGTH = 512
#: 滑窗搜索步长（秒）
SEARCH_STEP_SEC = 0.05
#: 滑窗宽度的缺省值；调用方通常传入该句原始时长
DEFAULT_WINDOW_SEC = 1.0

FINGERPRINT_FILE = "fingerprints.json"
FINGERPRINT_VERSION = 1


def _quantize(vec: np.ndarray) -> list[int]:
    q = np.round(np.asarray(vec, dtype=np.float64) * QUANT_SCALE)
    q = np.clip(q, -QUANT_CLAMP, QUANT_CLAMP)
    return [int(v) for v in q]


def _dequantize(fps: list[int]) -> np.ndarray:
    return np.asarray(fps, dtype=np.float64) / QUANT_SCALE


def compute(y: np.ndarray, sr: int) -> list[int]:
    """从单声道波形计算指纹（量化后的整数列表）。"""
    import librosa

    if y is None or len(y) < int(0.05 * FINGERPRINT_SR):
        return []
    y = np.asarray(y, dtype=np.float32)
    if np.max(np.abs(y)) <= 0:
        return []
    # 重采样到统一采样率，保证不同时长的 take 之间可比
    if sr != FINGERPRINT_SR:
        y = librosa.resample(y, orig_sr=sr, target_sr=FINGERPRINT_SR)
    S = librosa.feature.mfcc(y=y, sr=FINGERPRINT_SR, n_mfcc=N_MFCC)
    if S.shape[1] == 0:
        return []
    body = S[1:]  # 丢掉第 0 个整体能量系数 -> 增益不变
    return _quantize(np.concatenate([body.mean(axis=1), body.std(axis=1)]))


def compute_file(path: str) -> list[int]:
    """从 WAV 文件计算指纹；失败返回空列表（不抛异常）。"""
    try:
        import librosa

        y, sr = librosa.load(path, sr=FINGERPRINT_SR, mono=True)
        return compute(y, sr)
    except Exception as e:
        logger.warning("计算指纹失败 %s: %s", path, e)
        return []


def distance(a: list[int], b: list[int]) -> float:
    """指纹间的余弦距离；任一为空或长度不符时返回 1.0（最差）。"""
    if not a or not b or len(a) != len(b):
        return 1.0
    va, vb = _dequantize(a), _dequantize(b)
    na, nb = float(np.linalg.norm(va)), float(np.linalg.norm(vb))
    if na < 1e-9 or nb < 1e-9:
        return 1.0
    return float(1.0 - (va @ vb) / (na * nb))


def fingerprint_path(output_dir: str) -> str:
    """指纹文件路径，与被描述的 WAV 同目录。"""
    return os.path.join(output_dir, FINGERPRINT_FILE)


def save(output_dir: str, fingerprints: dict[int, list[int]]) -> str:
    """原子写出指纹文件，返回路径。"""
    path = fingerprint_path(output_dir)
    data = {
        "version": FINGERPRINT_VERSION,
        "sr": FINGERPRINT_SR,
        "n_mfcc": N_MFCC,
        "fingerprints": {
            str(k): v for k, v in sorted(fingerprints.items()) if v
        },
    }
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception as e:
        logger.warning("写指纹文件失败 %s: %s", path, e)
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        return ""
    return path


def load(output_dir: str) -> dict[int, list[int]]:
    """读取指纹文件；不存在或损坏时返回空字典（降级为纯波形匹配）。"""
    path = fingerprint_path(output_dir)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logger.warning("指纹文件损坏，已忽略: %s - %s", path, e)
        return {}
    if not isinstance(data, dict):
        return {}
    raw = data.get("fingerprints")
    if not isinstance(raw, dict):
        return {}
    out: dict[int, list[int]] = {}
    for k, v in raw.items():
        try:
            idx = int(k)
        except (TypeError, ValueError):
            continue
        if not isinstance(v, list) or len(v) != FINGERPRINT_LEN:
            continue
        if all(isinstance(x, int) for x in v):
            out[idx] = v
    return out


def locate(
    fingerprint: list[int],
    mfcc_matrix: np.ndarray,
    hop_length: int,
    window_frames: int,
    lo: float,
    hi: float,
    step_frames: int,
    sr: int,
) -> tuple[float, float]:
    """在 mfcc_matrix 的 [lo, hi] 秒区间内滑窗找最匹配位置。

    mfcc_matrix 形状为 (N_MFCC, frames)，是整段音频一次算好的；滑窗时
    只对帧区间重算均值/方差，因此无需为每个候选位置重算 MFCC。

    Returns:
        (位置秒, 最小余弦距离)；无可用窗口时返回 (-1.0, 1.0)
    """
    n_frames = mfcc_matrix.shape[1]
    if not fingerprint or n_frames < window_frames or window_frames <= 0:
        return -1.0, 1.0
    va = _dequantize(fingerprint)
    na = float(np.linalg.norm(va))
    if na < 1e-9:
        return -1.0, 1.0

    f_lo = max(0, int(lo * sr / hop_length))
    f_hi = min(n_frames - window_frames, int(hi * sr / hop_length))
    if f_hi < f_lo:
        return -1.0, 1.0

    best_pos, best_dist = -1.0, 1.0
    body = mfcc_matrix[1:]
    for start in range(f_lo, f_hi + 1, max(1, step_frames)):
        win = body[:, start:start + window_frames]
        if win.shape[1] < window_frames:
            break
        vb = np.concatenate([win.mean(axis=1), win.std(axis=1)])
        nb = float(np.linalg.norm(vb))
        if nb < 1e-9:
            continue
        d = float(1.0 - (va @ vb) / (na * nb))
        if d < best_dist:
            best_dist = d
            best_pos = start * hop_length / sr
    return best_pos, best_dist