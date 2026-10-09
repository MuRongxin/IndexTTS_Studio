"""音频变速 — 纯函数，通过 ffmpeg atempo 滤镜改变音频速度。"""
import os
import shutil
import subprocess


def change_audio_speed(
    input_path: str,
    output_path: str,
    rate: float,
    timeout: float = 120.0,
) -> None:
    """改变音频速度。

    Args:
        input_path: 输入 WAV 路径
        output_path: 输出 WAV 路径（**不能**与输入相同：ffmpeg 会先截断
            输出文件再读取输入，同路径会直接损坏音频）
        rate: 速度倍率，范围 0.5 ~ 2.0
        timeout: ffmpeg 执行超时秒数，超时按 RuntimeError 抛出

    Raises:
        RuntimeError: ffmpeg 不存在、执行失败或超时
        ValueError: rate 超出范围，或输出路径与输入相同
    """
    if not (0.5 <= rate <= 2.0):
        raise ValueError(f"速度倍率必须在 0.5~2.0 之间，当前: {rate}")

    if os.path.abspath(input_path) == os.path.abspath(output_path):
        raise ValueError(
            "输出路径不能与输入路径相同（ffmpeg 会先截断输出再读取输入）"
        )

    if shutil.which("ffmpeg") is None:
        raise RuntimeError("未找到 ffmpeg，请先安装")

    # atempo 滤镜范围是 0.5~2.0，超出需链式：atempo=2.0,atempo=1.25 → 2.5x
    # 此处 rate 限制在 0.5~2.0，单个 atempo 即可
    cmd = [
        "ffmpeg", "-y",
        "-i", input_path,
        "-filter:a", f"atempo={rate}",
        "-acodec", "pcm_s16le",
        output_path,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(
            f"ffmpeg 变速超时（{timeout:.0f}s）: {input_path}"
        ) from e
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg 变速失败: {result.stderr[:300]}")
