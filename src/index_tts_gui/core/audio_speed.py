"""音频变速 — 纯函数，通过 ffmpeg atempo 滤镜改变音频速度。"""
import shutil
import subprocess


def change_audio_speed(input_path: str, output_path: str, rate: float) -> None:
    """改变音频速度。

    Args:
        input_path: 输入 WAV 路径
        output_path: 输出 WAV 路径（可和输入相同，直接覆盖）
        rate: 速度倍率，范围 0.5 ~ 2.0

    Raises:
        RuntimeError: ffmpeg 不存在或执行失败
        ValueError: rate 超出范围
    """
    if not (0.5 <= rate <= 2.0):
        raise ValueError(f"速度倍率必须在 0.5~2.0 之间，当前: {rate}")

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
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg 变速失败: {result.stderr[:300]}")
