"""字幕文件解析 — SRT / ASS → list[SubtitleEntry]"""
from __future__ import annotations

import os
import re

from index_tts_gui.core.subtitle import SubtitleEntry, parse_time_str

# 匹配 SRT 时间行：HH:MM:SS,mmm --> HH:MM:SS,mmm
_SRT_TIME_RE = re.compile(
    r"(\d{1,2}:\d{2}:\d{2}[.,]\d{1,3})\s*-->\s*(\d{1,2}:\d{2}:\d{2}[.,]\d{1,3})"
)
# 匹配 ASS override tags，如 {\fad(200,200)}、{\pos(100,200)\c&HFFFFFF&}
_ASS_OVERRIDE_RE = re.compile(r"\{[^{}]*\}")


def _read_text_file(path: str) -> str:
    """按 utf-8-sig → gbk 的顺序读取文本文件，均失败抛中文异常。"""
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError as e:
        raise ValueError(f"无法读取字幕文件: {path} - {e}") from e
    for encoding in ("utf-8-sig", "gbk"):
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    raise ValueError(f"字幕文件编码无法识别（已尝试 UTF-8 / GBK）: {path}")


def parse_subtitle_file(path: str) -> list[SubtitleEntry]:
    """按扩展名分发到 SRT / ASS 解析器，返回 SubtitleEntry 列表。"""
    if not os.path.exists(path):
        raise ValueError(f"字幕文件不存在: {path}")
    ext = os.path.splitext(path)[1].lower()
    if ext == ".srt":
        entries = parse_srt(path)
    elif ext in (".ass", ".ssa"):
        entries = parse_ass(path)
    else:
        raise ValueError(f"不支持的字幕格式: {ext}（仅支持 .srt / .ass）")
    return entries


# ── SRT ──


def parse_srt(path: str) -> list[SubtitleEntry]:
    """解析 SRT 文件：序号 + 时间行 + 多行文本。"""
    content = _read_text_file(path)
    content = content.replace("\r\n", "\n").replace("\r", "\n")
    lines = content.split("\n")

    # 按空行切块，并记录每块首行的行号（1-based，用于报错定位）
    blocks: list[tuple[int, list[str]]] = []
    current: list[str] = []
    current_start_line = 0
    for line_no, line in enumerate(lines, 1):
        if line.strip() == "":
            if current:
                blocks.append((current_start_line, current))
                current = []
                current_start_line = 0
        else:
            if not current:
                current_start_line = line_no
            current.append(line.rstrip())
    if current:
        blocks.append((current_start_line, current))

    entries: list[SubtitleEntry] = []
    for start_line, block in blocks:
        # 找时间行
        time_line_no = None
        time_match = None
        for offset, line in enumerate(block):
            m = _SRT_TIME_RE.search(line)
            if m:
                time_line_no = start_line + offset
                time_match = m
                break
        if time_match is None:
            raise ValueError(
                f"SRT 解析失败（第 {start_line} 行附近）：缺少 'HH:MM:SS,mmm --> HH:MM:SS,mmm' 时间行"
            )

        start_sec = parse_time_str(time_match.group(1))
        end_sec = parse_time_str(time_match.group(2))
        if start_sec < 0 or end_sec < 0:
            raise ValueError(
                f"SRT 解析失败（第 {time_line_no} 行）：时间格式无法识别: {time_match.group(0)}"
            )

        # 序号：时间行之前的整数行；缺失时按顺序分配
        index = len(entries) + 1
        for line in block:
            if line.strip().isdigit():
                index = int(line.strip())
                break

        # 文本：时间行之后的所有行（按块内偏移定位，避免行内子串匹配问题）
        time_offset = time_line_no - start_line
        text_lines = block[time_offset + 1 :]
        text = "\n".join(l.strip() for l in text_lines).strip()
        if not text:
            continue  # 空文本条目跳过

        entries.append(SubtitleEntry(index, start_sec, end_sec, text))

    if not entries:
        raise ValueError(f"SRT 解析失败: 文件中没有有效的字幕条目: {path}")
    # 统一重排 1-based 序号，保证下游时间轴与导出有序
    for i, e in enumerate(entries, 1):
        e.index = i
    return entries


# ── ASS ──


def _clean_ass_text(text: str) -> str:
    """剥离 override tags，把 \\N/\\n 转为换行、\\h 转为空格。"""
    text = _ASS_OVERRIDE_RE.sub("", text)
    text = text.replace("\\N", "\n").replace("\\n", "\n").replace("\\h", " ")
    return text.strip()


def parse_ass(path: str) -> list[SubtitleEntry]:
    """解析 ASS 文件的 [Events] 段 Dialogue 行。"""
    content = _read_text_file(path)
    lines = content.replace("\r\n", "\n").replace("\r", "\n").split("\n")

    # 定位 [Events] 段
    events_start = None
    for i, line in enumerate(lines):
        if line.strip().lower() == "[events]":
            events_start = i
            break
    if events_start is None:
        raise ValueError(f"ASS 解析失败: 未找到 [Events] 段: {path}")

    # 定位 Format: 行，并解析列位置
    format_fields: list[str] = []
    format_line_no = None
    for i in range(events_start + 1, len(lines)):
        line = lines[i].strip()
        if line.startswith("Format:"):
            format_fields = [f.strip().lower() for f in line[len("Format:") :].split(",")]
            format_line_no = i + 1  # 1-based
            break
    if not format_fields:
        raise ValueError(f"ASS 解析失败: [Events] 段缺少 Format 行: {path}")

    def _col(name: str) -> int | None:
        try:
            return format_fields.index(name)
        except ValueError:
            return None

    start_col, end_col, text_col = _col("start"), _col("end"), _col("text")
    if start_col is None or end_col is None or text_col is None:
        raise ValueError(
            f"ASS 解析失败（第 {format_line_no} 行）：Format 缺少 Start/End/Text 列: "
            f"{format_fields}"
        )

    entries: list[SubtitleEntry] = []
    index = 1
    for line_no, raw_line in enumerate(lines[events_start + 1 :], events_start + 2):
        line = raw_line.strip()
        if line.startswith("Dialogue:"):
            # Text 列可能包含逗号：按 Format 字段总数切分，最后一段即 Text
            body = line[len("Dialogue:") :].lstrip()
            parts = body.split(",", len(format_fields) - 1)
            if len(parts) < len(format_fields):
                raise ValueError(
                    f"ASS 解析失败（第 {line_no} 行）：Dialogue 字段数量不足"
                )
            start_sec = parse_time_str(parts[start_col])
            end_sec = parse_time_str(parts[end_col])
            if start_sec < 0 or end_sec < 0:
                raise ValueError(
                    f"ASS 解析失败（第 {line_no} 行）：时间格式无法识别: "
                    f"{parts[start_col]} / {parts[end_col]}"
                )
            text = _clean_ass_text(parts[text_col])
            if not text:
                continue
            entries.append(SubtitleEntry(index, start_sec, end_sec, text))
            index += 1
        elif line.startswith("Comment:"):
            continue

    if not entries:
        raise ValueError(f"ASS 解析失败: 文件中没有有效的 Dialogue 条目: {path}")
    # 统一重排 1-based 序号，保证下游时间轴与导出有序
    for i, e in enumerate(entries, 1):
        e.index = i
    return entries
