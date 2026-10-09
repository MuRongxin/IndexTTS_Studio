"""
音频合并（ffmpeg concat），支持按标点插入停顿。
"""
import json
import logging
import os
import re
import subprocess
import tempfile

from index_tts_gui.core.pause_rules import compute_pauses


logger = logging.getLogger("index_tts")


def _run_ffprobe(args: list[str], wav_path: str, timeout: float = 30.0) -> dict:
    """调用 ffprobe 并校验返回结果。"""
    result = subprocess.run(
        ["ffprobe", "-v", "quiet"] + args + [wav_path],
        capture_output=True, text=True, timeout=timeout,
    )
    if result.returncode != 0:
        err = result.stderr.strip()[:500]
        raise RuntimeError(f"ffprobe 失败 ({wav_path}): {err}")
    if not result.stdout.strip():
        raise RuntimeError(f"ffprobe 返回为空: {wav_path}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"ffprobe 输出不是有效 JSON: {wav_path} - {e}") from e


def get_wav_duration(wav_path: str) -> float:
    """获取 WAV 文件时长（秒）"""
    data = _run_ffprobe(
        ["-show_entries", "format=duration", "-of", "json"], wav_path
    )
    if "format" not in data or "duration" not in data["format"]:
        raise RuntimeError(f"无法获取音频时长: {wav_path}")
    return float(data["format"]["duration"])


def _get_audio_info(wav_path: str) -> tuple[int, int]:
    """获取 WAV 采样率和声道数。"""
    data = _run_ffprobe(
        ["-show_entries", "stream=sample_rate,channels", "-of", "json"], wav_path
    )
    if "streams" not in data or not data["streams"]:
        raise RuntimeError(f"无法获取音频流信息: {wav_path}")
    stream = data["streams"][0]
    return int(stream["sample_rate"]), int(stream["channels"])


def _probe_common_format(wav_paths: list[str]) -> tuple[int, int, bool]:
    """探测所有输入的采样率/声道数。

    返回 (基准采样率, 基准声道数, 是否全部一致)，基准取第一个片段。
    全部一致时可直接 concat 流拷贝；不一致必须重编码统一格式，
    否则输出会在中途改变采样率，ffprobe 的 format=duration 不可靠
    （下游所有字幕时间戳都依赖它）。
    """
    base_rate, base_channels = _get_audio_info(wav_paths[0])
    uniform = True
    for path in wav_paths[1:]:
        rate, channels = _get_audio_info(path)
        if rate != base_rate or channels != base_channels:
            uniform = False
            break
    return base_rate, base_channels, uniform


def _generate_silence(
    duration: float,
    ref_path: str,
    output_path: str,
    sample_rate: int | None = None,
    channels: int | None = None,
):
    """生成静音 WAV。

    默认沿用 ref_path 的采样率/声道；显式给出 sample_rate/channels 时
    按该参数生成，用于混合采样率场景下让所有静音保持同一格式。
    """
    if sample_rate is None or channels is None:
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
        check=True, capture_output=True, timeout=30.0,
    )


def merge_wavs(
    wav_paths: list[str],
    output_path: str,
    force_format: tuple[int, int] | None = None,
):
    """
    用 ffmpeg concat 合并多个 WAV 文件。

    Args:
        wav_paths: WAV 文件路径列表（按顺序）
        output_path: 输出文件路径
        force_format: (采样率, 声道数)。为 None 时直接流拷贝（默认，
            要求各输入格式一致）；给出时改为重编码到该统一格式，
            用于输入采样率/声道数不一致的场景
    """
    if not wav_paths:
        raise ValueError("没有可合并的音频文件")

    logger.info(
        "合并音频: files=%d output=%s first=%s force_format=%s",
        len(wav_paths), output_path, wav_paths[0], force_format
    )
    if force_format is None:
        codec_args = ["-c", "copy"]
    else:
        sample_rate, channels = force_format
        codec_args = [
            "-c:a", "pcm_s16le", "-ar", str(sample_rate), "-ac", str(channels),
        ]

    fd, list_path = tempfile.mkstemp(suffix=".txt", prefix="concat_")
    try:
        with os.fdopen(fd, "w") as f:
            for p in wav_paths:
                abs_path = os.path.abspath(p)
                # 转义单引号：' -> '\''，确保 concat 文件格式安全
                safe_path = abs_path.replace("'", "'\\''")
                f.write(f"file '{safe_path}'\n")

        subprocess.run(
            [
                "ffmpeg", "-y", "-f", "concat", "-safe", "0",
                "-i", list_path, *codec_args, output_path,
            ],
            check=True, capture_output=True, timeout=300.0,
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
    pauses = compute_pauses(sentences, base_pause)
    logger.info("标点规则停顿: %s", pauses)
    merge_wavs_with_custom_pauses(wav_paths, pauses, output_path)


def merge_wavs_with_custom_pauses(
    wav_paths: list[str],
    pauses: list[float],
    output_path: str,
    on_progress: "callable | None" = None,
    leading: bool = False,
):
    """
    合并 WAV 片段，使用自定义停顿时长。

    Args:
        wav_paths: WAV 文件路径列表
        pauses: 停顿时长列表，长度应与 wav_paths 相同；默认表示每段
                **之后**的停顿，leading=True 时表示每段**之前**的停顿
        output_path: 输出文件路径
        on_progress: 进度回调 (current, total, message)，逐段生成静音时触发
        leading: 为 True 时把停顿插在对应片段之前（配音按时间轴顺延
                 的场景需要先补静音再放音频）
    """
    if not wav_paths:
        raise ValueError("没有可合并的音频文件")
    if len(wav_paths) != len(pauses):
        raise ValueError(
            f"音频片段数量（{len(wav_paths)}）与停顿数量（{len(pauses)}）不一致"
        )

    logger.info("自定义停顿合并: files=%d pauses=%s", len(wav_paths), pauses)

    total = len(wav_paths)
    # 所有静音统一按第一个片段的采样率/声道生成；输入格式不一致时
    # 最终 concat 走重编码，避免输出中途改变采样率
    base_rate, base_channels, uniform = _probe_common_format(wav_paths)
    if not uniform:
        logger.warning("输入音频格式不一致，合并时重编码到 %dHz/%d 声道",
                       base_rate, base_channels)
    force_format = None if uniform else (base_rate, base_channels)

    with tempfile.TemporaryDirectory(prefix="tts_merge_") as tmpdir:
        concat_items: list[str] = []
        for i, path in enumerate(wav_paths):
            if on_progress:
                on_progress(i + 1, total, f"合并片段 {i + 1}/{total}")
            pause = pauses[i] if i < len(pauses) else 0.0
            silence_path = None
            if pause > 0:
                silence_path = os.path.join(tmpdir, f"silence_{i:04d}.wav")
                logger.debug("生成静音: index=%d duration=%.2f", i, pause)
                try:
                    _generate_silence(
                        pause, path, silence_path,
                        sample_rate=base_rate, channels=base_channels,
                    )
                except subprocess.CalledProcessError as e:
                    err = e.stderr.decode("utf-8", errors="replace")[:500]
                    logger.error("生成静音失败: index=%d error=%s", i, err)
                    raise RuntimeError(f"生成第 {i} 段静音失败: {err}") from e

            if leading and silence_path is not None:
                concat_items.append(silence_path)
            concat_items.append(path)
            if not leading and silence_path is not None:
                concat_items.append(silence_path)

        if on_progress:
            on_progress(total, total, f"拼接 {len(concat_items)} 段音频")
        merge_wavs(concat_items, output_path, force_format=force_format)


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
    # 输出目录可能尚未创建（新工程/未合成），按无分句音频处理
    if not os.path.isdir(output_dir):
        return []
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



