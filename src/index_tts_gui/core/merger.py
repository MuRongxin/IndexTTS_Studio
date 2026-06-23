"""
音频合并（ffmpeg concat）
"""
import subprocess
import json
import os
import tempfile


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
    return float(json.loads(result.stdout)["format"]["duration"])


def merge_wavs(wav_paths: list[str], output_path: str):
    """
    用 ffmpeg concat 合并多个 WAV 文件。
    
    Args:
        wav_paths: WAV 文件路径列表（按顺序）
        output_path: 输出文件路径
    """
    # 写 concat 列表
    fd, list_path = tempfile.mkstemp(suffix=".txt", prefix="concat_")
    try:
        with os.fdopen(fd, "w") as f:
            for p in wav_paths:
                f.write(f"file '{os.path.abspath(p)}'\n")

        subprocess.run(
            [
                "ffmpeg", "-y", "-f", "concat", "-safe", "0",
                "-i", list_path, "-c", "copy", output_path,
            ],
            check=True, capture_output=True,
        )
    finally:
        if os.path.exists(list_path):
            os.remove(list_path)
