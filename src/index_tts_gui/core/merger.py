"""
音频合并（ffmpeg concat），支持按标点插入停顿。
"""
import json
import logging
import os
import re
import subprocess
import tempfile


logger = logging.getLogger("index_tts")


def get_wav_duration(wav_path: str) -> float:
    """获取 WAV 文件时长（秒）"""
    result = subprocess.run(
        [
            "ffprobe", "-v", "quiet",
            "-show_entries", "format=duration",
            "-of", "json", wav_path,
        ],
        capture_output=True, text=True,
    )
    data = json.loads(result.stdout)
    if "format" not in data or "duration" not in data["format"]:
        raise RuntimeError(f"无法获取音频时长: {wav_path}")
    return float(data["format"]["duration"])


def _get_audio_info(wav_path: str) -> tuple[int, int]:
    """获取 WAV 采样率和声道数。"""
    result = subprocess.run(
        [
            "ffprobe", "-v", "quiet",
            "-show_entries", "stream=sample_rate,channels",
            "-of", "json", wav_path,
        ],
        capture_output=True, text=True,
    )
    data = json.loads(result.stdout)
    stream = data["streams"][0]
    return int(stream["sample_rate"]), int(stream["channels"])


def _generate_silence(duration: float, ref_path: str, output_path: str):
    """生成与参考音频同格式的静音 WAV。"""
    sample_rate, channels = _get_audio_info(ref_path)
    layout = "mono" if channels == 1 else "stereo"
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i",
            f"anullsrc=r={sample_rate}:cl={layout}",
            "-t", str(duration),
            "-acodec", "pcm_s16le",
            "-ar", str(sample_rate),
            "-ac", str(channels),
            output_path,
        ],
        check=True, capture_output=True,
    )


def _compute_pauses(sentences: list[str], base_pause: float = 0.12) -> list[float]:
    """
    根据句子末尾标点计算每句之后的停顿时长。

    最后一句后面返回 0（不需要停顿）。
    """
    pauses = []
    for s in sentences:
        s = s.strip()
        if not s:
            pauses.append(base_pause)
            continue
        last_char = s[-1]
        if last_char in "。！？":
            pauses.append(0.55)
        elif last_char in "，、；：":
            pauses.append(0.22)
        else:
            pauses.append(base_pause)
    # 最后一句不需要尾部停顿
    if pauses:
        pauses[-1] = 0.0
    return pauses


def merge_wavs(wav_paths: list[str], output_path: str):
    """
    用 ffmpeg concat 合并多个 WAV 文件。

    Args:
        wav_paths: WAV 文件路径列表（按顺序）
        output_path: 输出文件路径
    """
    if not wav_paths:
        raise ValueError("没有可合并的音频文件")

    logger.info(
        "合并音频: files=%d output=%s first=%s",
        len(wav_paths), output_path, wav_paths[0]
    )

    fd, list_path = tempfile.mkstemp(suffix=".txt", prefix="concat_")
    try:
        with os.fdopen(fd, "w") as f:
            for p in wav_paths:
                f.write(f"file '{os.path.abspath(p)}'\n")

        result = subprocess.run(
            [
                "ffmpeg", "-y", "-f", "concat", "-safe", "0",
                "-i", list_path, "-c", "copy", output_path,
            ],
            check=True, capture_output=True,
        )
        logger.info("合并完成: %s", output_path)
    except subprocess.CalledProcessError as e:
        err = e.stderr.decode("utf-8", errors="replace")[:500]
        logger.error("ffmpeg 合并失败: %s", err)
        raise RuntimeError(f"ffmpeg 合并失败: {err}") from e
    finally:
        if os.path.exists(list_path):
            os.remove(list_path)


def merge_wavs_with_pauses(
    wav_paths: list[str],
    sentences: list[str],
    output_path: str,
    base_pause: float = 0.12,
):
    """
    合并 WAV 片段，根据句子末尾标点插入停顿。

    Args:
        wav_paths: WAV 文件路径列表（顺序与 sentences 一致）
        sentences: 句子文本列表
        output_path: 输出文件路径
        base_pause: 无标点时的默认停顿（秒）
    """
    if len(wav_paths) != len(sentences):
        raise ValueError(
            f"音频片段数量（{len(wav_paths)}）与句子数量（{len(sentences)}）不一致"
        )
    pauses = _compute_pauses(sentences, base_pause)
    logger.info("标点规则停顿: %s", pauses)
    merge_wavs_with_custom_pauses(wav_paths, pauses, output_path)


def merge_wavs_with_custom_pauses(
    wav_paths: list[str],
    pauses: list[float],
    output_path: str,
):
    """
    合并 WAV 片段，使用自定义停顿时长。

    Args:
        wav_paths: WAV 文件路径列表
        pauses: 每段之后的停顿时长列表，长度应与 wav_paths 相同
        output_path: 输出文件路径
    """
    if not wav_paths:
        raise ValueError("没有可合并的音频文件")
    if len(wav_paths) != len(pauses):
        raise ValueError(
            f"音频片段数量（{len(wav_paths)}）与停顿数量（{len(pauses)}）不一致"
        )

    logger.info("自定义停顿合并: files=%d pauses=%s", len(wav_paths), pauses)

    with tempfile.TemporaryDirectory(prefix="tts_merge_") as tmpdir:
        concat_items: list[str] = []
        for i, path in enumerate(wav_paths):
            concat_items.append(path)
            pause = pauses[i] if i < len(pauses) else 0.0
            if pause > 0:
                silence_path = os.path.join(tmpdir, f"silence_{i:04d}.wav")
                logger.debug("生成静音: index=%d duration=%.2f", i, pause)
                _generate_silence(pause, path, silence_path)
                concat_items.append(silence_path)

        merge_wavs(concat_items, output_path)


def sanitize_for_filename(text: str, max_len: int = 20) -> str:
    """把句子文本处理成可用在文件名中的字符串。"""
    text = text.strip()
    text = re.sub(r'[^\w\s\u4e00-\u9fff]', "", text)
    text = re.sub(r'\s+', "_", text)
    if len(text) > max_len:
        text = text[:max_len]
    text = text.strip("_")
    if not text:
        text = "no_text"
    return text


def parse_sentence_wav_name(name: str) -> tuple[int, str] | None:
    """
    解析 sentence_XX_文本.wav 文件名。

    返回 (序号, 文本)，解析失败返回 None。
    """
    if not name.startswith("sentence_") or not name.endswith(".wav"):
        return None
    body = name[len("sentence_"):-len(".wav")]
    # 匹配：两位数字 + 可选的下划线文本
    m = re.match(r"^(\d+)(?:_(.*))?$", body)
    if not m:
        return None
    index = int(m.group(1))
    text = m.group(2) or ""
    return index, text


def collect_sentence_wavs(output_dir: str) -> list[str]:
    """按序号收集 output_dir 下的 sentence_*.wav 文件。"""
    files = []
    for name in os.listdir(output_dir):
        if parse_sentence_wav_name(name) is not None:
            files.append(os.path.join(output_dir, name))

    def _sort_key(path: str) -> int:
        name = os.path.basename(path)
        parsed = parse_sentence_wav_name(name)
        return parsed[0] if parsed else 0

    return sorted(files, key=_sort_key)


def validate_wav_order(wav_paths: list[str], sentences: list[str]) -> list[str]:
    """
    校验 WAV 文件名中的文本与 sentences 是否一致。

    返回错误信息列表，空列表表示校验通过。
    """
    errors = []
    for i, (path, sentence) in enumerate(zip(wav_paths, sentences), 1):
        name = os.path.basename(path)
        parsed = parse_sentence_wav_name(name)
        if parsed is None:
            errors.append(f"第 {i} 个文件名格式异常: {name}")
            continue
        _, text_in_name = parsed
        expected = sanitize_for_filename(sentence)
        if text_in_name != expected:
            errors.append(
                f"第 {i} 个文件名文本与当前句子不匹配: "
                f"文件名='{text_in_name}' 当前='{expected}'"
            )
    if errors:
        logger.warning("WAV 顺序校验失败: %s", errors)
    else:
        logger.info("WAV 顺序校验通过: %d 个文件", len(wav_paths))
    return errors



