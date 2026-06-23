"""ASS/SSA 字幕格式导出"""

from __future__ import annotations

import os
from typing import List

from index_tts_gui.core.subtitle import SubtitleEntry


def _seconds_to_ass_time(seconds: float) -> str:
    """秒 → ASS 时间格式 H:MM:SS.cc（厘秒）"""
    seconds = max(0.0, seconds)
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    cents = int((seconds - int(seconds)) * 100)
    return f"{hours}:{minutes:02d}:{secs:02d}.{cents:02d}"


def _hex_to_ass_color(hex_color: str) -> str:
    """#RRGGBB / #AARRGGBB → &HBBGGRR& / &HAABBGGRR&"""
    hex_color = hex_color.lstrip("#")
    if len(hex_color) == 8:
        aa = hex_color[0:2]
        rr = hex_color[2:4]
        gg = hex_color[4:6]
        bb = hex_color[6:8]
        return f"&H{aa}{bb}{gg}{rr}&"
    if len(hex_color) == 6:
        rr = hex_color[0:2]
        gg = hex_color[2:4]
        bb = hex_color[4:6]
        return f"&H{bb}{gg}{rr}&"
    return "&HFFFFFF&"


def entries_to_ass(
    entries: List[SubtitleEntry],
    filepath: str,
    *,
    fade_in_ms: int = 100,
    fade_out_ms: int = 200,
    font_name: str = "Microsoft YaHei",
    font_size: int = 24,
    primary_color: str = "#FFFFFF",
    outline_color: str = "#08B1FF",
    back_color: str = "#00000000",
    bold: bool = False,
    italic: bool = False,
    underline: bool = False,
    outline_width: float = 1.0,
    alignment: int = 2,
) -> None:
    """将字幕条目导出为 ASS 文件，默认带 Fade 效果。"""
    directory = os.path.dirname(filepath)
    if directory:
        os.makedirs(directory, exist_ok=True)

    lines = [
        "[Script Info]",
        "Title: IndexTTS Studio Generated",
        "ScriptType: v4.00+",
        "Collisions: Normal",
        "PlayResX: 1920",
        "PlayResY: 1080",
        "Timer: 100.0000",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding",
        _style_to_ass_string(
            font_name, font_size, primary_color, outline_color, back_color,
            bold, italic, underline, outline_width, alignment,
        ),
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]

    for e in entries:
        start = _seconds_to_ass_time(e.start_sec)
        end = _seconds_to_ass_time(e.end_sec)
        text = e.text.replace("\n", "\\N")
        if fade_in_ms > 0 or fade_out_ms > 0:
            text = f"{{\\fad({fade_in_ms},{fade_out_ms})}}{text}"
        lines.append(
            f"Dialogue: 0,{start},{end},Default,,0,0,0,,{text}"
        )

    with open(filepath, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
        f.write("\n")


def _style_to_ass_string(
    font_name: str,
    font_size: int,
    primary_color: str,
    outline_color: str,
    back_color: str,
    bold: bool,
    italic: bool,
    underline: bool,
    outline_width: float,
    alignment: int,
) -> str:
    primary = _hex_to_ass_color(primary_color)
    outline = _hex_to_ass_color(outline_color)
    back = _hex_to_ass_color(back_color)
    bold_val = "1" if bold else "0"
    italic_val = "1" if italic else "0"
    underline_val = "1" if underline else "0"
    return (
        f"Style: Default,{font_name},{font_size},"
        f"{primary},{primary},{outline},{back},"
        f"{bold_val},{italic_val},{underline_val},"
        "0,100,100,0,0,1,"
        f"{outline_width:.1f},0,{alignment},10,10,10,1"
    )
