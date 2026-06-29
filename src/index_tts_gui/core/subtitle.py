"""字幕数据模型 — SubtitleItem, SubtitleTrack, SubtitleStyle"""

from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class SubtitleEntry:
    """兼容旧版合成流水线的轻量字幕条目"""

    index: int
    start_sec: float
    end_sec: float
    text: str


@dataclass
class SubtitleStyle:
    """字幕样式属性

    Attributes:
        font_name: 字体名称
        font_size: 字号（像素）
        primary_color: 主色（十六进制，如 #FFFFFF）
        outline_color: 描边色（十六进制，如 #000000）
        back_color: 背景色（十六进制，含透明度，如 #00000000）
        bold: 是否粗体
        italic: 是否斜体
        underline: 是否下划线
        outline_width: 描边宽度（像素）
        alignment: 对齐方式（1=下左, 2=下中, 3=下右, 4=中左, 5=中中,
                   6=中右, 7=上左, 8=上中, 9=上右）
        fade_in_ms: 淡入时长（毫秒），0 = 无淡入
        fade_out_ms: 淡出时长（毫秒），0 = 无淡出
    """

    font_name: str = "Microsoft YaHei"
    font_size: int = 90
    primary_color: str = "#FFFFFF"
    outline_color: str = "#000000"
    back_color: str = "#00000000"
    bold: bool = False
    italic: bool = False
    underline: bool = False
    outline_width: float = 1.0
    alignment: int = 2
    fade_in_ms: int = 200
    fade_out_ms: int = 200

    def copy(self) -> "SubtitleStyle":
        """创建样式的深拷贝"""
        return deepcopy(self)


@dataclass
class SubtitleItem:
    """单条字幕

    Attributes:
        index: 序号（1-based）
        start_time: 开始时间（秒）
        end_time: 结束时间（秒）
        text: 字幕文本
        style: 专属样式（None 则使用全局默认样式）
    """

    index: int
    start_time: float
    end_time: float
    text: str = ""
    style: Optional[SubtitleStyle] = None

    @property
    def duration(self) -> float:
        """字幕持续时间（秒），确保非负"""
        return max(0.0, self.end_time - self.start_time)

    def is_valid(self) -> bool:
        """检查字幕时间是否有效"""
        return self.start_time >= 0 and self.end_time > self.start_time

    def copy(self) -> "SubtitleItem":
        """创建字幕项的深拷贝"""
        return deepcopy(self)


class SubtitleTrack:
    """字幕轨道，管理一组有序的字幕项"""

    def __init__(self):
        self.items: List[SubtitleItem] = []
        self.default_style = SubtitleStyle()
        self.name: str = "Default"

    @classmethod
    def from_entries(cls, entries: List[SubtitleEntry]) -> "SubtitleTrack":
        """从旧版 SubtitleEntry 列表创建轨道"""
        track = cls()
        for e in entries:
            track.items.append(
                SubtitleItem(
                    index=e.index,
                    start_time=e.start_sec,
                    end_time=e.end_sec,
                    text=e.text,
                )
            )
        track.reindex()
        return track

    def to_entries(self) -> List[SubtitleEntry]:
        """导出为旧版 SubtitleEntry 列表"""
        return [
            SubtitleEntry(
                index=item.index,
                start_sec=item.start_time,
                end_sec=item.end_time,
                text=item.text,
            )
            for item in self.items
        ]

    def add_item(self, item: SubtitleItem) -> None:
        """添加字幕并保持按开始时间排序"""
        self.items.append(item)
        self.sort()
        self.reindex()

    def insert_item(self, index: int, item: SubtitleItem) -> None:
        """在指定位置插入字幕（0-based）"""
        if index < 0:
            index = 0
        if index > len(self.items):
            index = len(self.items)
        self.items.insert(index, item)
        self.reindex()

    def remove_item(self, index: int) -> None:
        """按序号删除字幕（1-based）"""
        idx_0based = index - 1
        if 0 <= idx_0based < len(self.items):
            del self.items[idx_0based]
            self.reindex()
        else:
            raise IndexError(f"Subtitle index {index} out of range (1-{len(self.items)})")

    def get_item_at_time(self, time: float) -> Optional[SubtitleItem]:
        """获取指定时间点的字幕（start <= time < end）"""
        for item in self.items:
            if item.start_time <= time < item.end_time:
                return item
        return None

    def get_item(self, index: int) -> Optional[SubtitleItem]:
        """按 1-based 序号获取字幕项"""
        for item in self.items:
            if item.index == index:
                return item
        return None

    def get_items_in_range(self, start: float, end: float) -> List[SubtitleItem]:
        """获取与 [start, end] 时间范围有交集的所有字幕"""
        result = []
        for item in self.items:
            if not (item.end_time < start or item.start_time > end):
                result.append(item)
        return result

    def sort(self) -> None:
        """按开始时间排序（开始时间相同则按结束时间排序）"""
        self.items.sort(key=lambda item: (item.start_time, item.end_time))

    def reindex(self) -> None:
        """重新生成 1-based 序号"""
        for i, item in enumerate(self.items):
            item.index = i + 1

    @property
    def count(self) -> int:
        """字幕数量"""
        return len(self.items)

    @property
    def total_duration(self) -> float:
        """最后一条字幕的结束时间，无字幕时返回 0"""
        if not self.items:
            return 0.0
        return max(item.end_time for item in self.items)

    def get_next_item(self, current_index: int) -> Optional[SubtitleItem]:
        """获取下一条字幕"""
        for item in self.items:
            if item.index == current_index + 1:
                return item
        return None

    def get_prev_item(self, current_index: int) -> Optional[SubtitleItem]:
        """获取上一条字幕"""
        for item in self.items:
            if item.index == current_index - 1:
                return item
        return None

    def split_item(self, index: int, split_time: float) -> None:
        """在指定时间拆分字幕为两条"""
        idx_0based = index - 1
        if not (0 <= idx_0based < len(self.items)):
            raise IndexError(f"Index {index} out of range (1-{len(self.items)})")
        item = self.items[idx_0based]
        if not (item.start_time < split_time < item.end_time):
            raise ValueError(
                f"Split time {split_time} must be between "
                f"{item.start_time} and {item.end_time}"
            )

        original_end_time = item.end_time
        duration = item.duration
        ratio = (split_time - item.start_time) / duration if duration > 0 else 0.5
        ratio = max(0.0, min(1.0, ratio))

        first_text, second_text = self.preview_split(item.text, ratio)

        item.text = first_text
        item.end_time = split_time

        new_item = SubtitleItem(
            index=0,
            start_time=split_time,
            end_time=original_end_time,
            text=second_text,
            style=item.style.copy() if item.style else None,
        )
        self.items.insert(idx_0based + 1, new_item)
        self.reindex()

    @staticmethod
    def preview_split(text: str, ratio: float) -> tuple[str, str]:
        """按比例预览切分文本，返回 (前半, 后半)。

        以字符数比例为基准，优先在空格或标点处切分，但只在边界位置
        不会导致比例严重偏离时才使用边界；否则回退到字符级切分，使
        前后视觉长度尽量接近目标比例。
        """
        ratio = max(0.0, min(1.0, ratio))
        if not text:
            return "", ""

        punct = "，。、；：！？,.;:!?"
        total = len(text)
        target = max(1, min(total - 1, round(total * ratio)))

        # 第一步：找离目标位置最近的空格/标点边界
        best_boundary_pos = -1
        best_boundary_dist = float("inf")
        for i, ch in enumerate(text):
            if i == 0 or i >= total:
                continue
            if ch != " " and ch not in punct:
                continue
            dist = abs(i - target)
            if dist < best_boundary_dist:
                best_boundary_dist = dist
                best_boundary_pos = i

        # 第二步：如果最近边界导致比例偏差不超过 8%，则使用边界
        BOUNDARY_RATIO_TOLERANCE = 0.08
        best_pos = target
        if best_boundary_pos > 0:
            boundary_ratio = best_boundary_pos / total
            if abs(boundary_ratio - ratio) <= BOUNDARY_RATIO_TOLERANCE:
                best_pos = best_boundary_pos

        first_text = text[:best_pos].rstrip(punct + " ")
        second_text = text[best_pos:].lstrip(punct + " ")
        return first_text, second_text

    @staticmethod
    def _merge_text(parts: list[str]) -> str:
        """拼接字幕文本。若全部为中文字符则直接拼接，否则用空格连接。"""
        stripped = [p.strip() for p in parts if p and p.strip()]
        if not stripped:
            return ""
        # 判断是否存在非中文/非标点/非空格的字符
        has_non_cjk = any(
            re.search(r"[a-zA-Z0-9]", part) for part in stripped
        )
        if has_non_cjk:
            return " ".join(stripped)
        return "".join(stripped)

    def merge_items(self, index1: int, index2: int) -> None:
        """合并两条字幕（按序号）"""
        idx1 = index1 - 1
        idx2 = index2 - 1
        if not (0 <= idx1 < len(self.items)):
            raise IndexError(f"Index {index1} out of range")
        if not (0 <= idx2 < len(self.items)):
            raise IndexError(f"Index {index2} out of range")
        if idx1 == idx2:
            raise ValueError("Cannot merge the same item with itself")

        if idx1 > idx2:
            idx1, idx2 = idx2, idx1

        item1 = self.items[idx1]
        item2 = self.items[idx2]

        item1.end_time = max(item1.end_time, item2.end_time)
        item1.text = self._merge_text([item1.text, item2.text])

        del self.items[idx2]
        self.reindex()

    def merge_multiple(self, indices: list[int]) -> int:
        """合并多条字幕，按时间顺序拼接文本，返回合并后项目的序号（1-based）。"""
        if len(indices) < 2:
            raise ValueError("Need at least two items to merge")
        valid = [i for i in indices if 1 <= i <= len(self.items)]
        if len(valid) < 2:
            raise ValueError("Not enough valid indices to merge")
        # 按开始时间排序，保证文本顺序
        sorted_items = sorted(
            ((i, self.items[i - 1]) for i in valid),
            key=lambda x: x[1].start_time,
        )
        first_idx, first_item = sorted_items[0]
        last_item = sorted_items[-1][1]

        text_parts = []
        for _, item in sorted_items:
            part = item.text.strip()
            if part:
                text_parts.append(part)
        first_item.end_time = last_item.end_time
        first_item.text = self._merge_text(text_parts)

        # 删除其余项目（从后往前避免索引变化）
        to_delete = sorted(
            (i for i, _ in sorted_items[1:]), reverse=True
        )
        for idx in to_delete:
            del self.items[idx - 1]
        self.reindex()
        return self.items.index(first_item) + 1

    def __len__(self) -> int:
        return len(self.items)

    def __iter__(self):
        return iter(self.items)

    def __getitem__(self, index: int) -> SubtitleItem:
        return self.items[index]


# ---- 时间格式工具 ----


def seconds_to_time_str(seconds: float, show_ms: bool = True) -> str:
    """格式化为 HH:MM:SS.mmm（与 SRT 兼容的逗号毫秒可在外层替换）"""
    seconds = max(0.0, seconds)
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    if show_ms:
        millis = int((seconds - int(seconds)) * 1000)
        return f"{hours:02d}:{minutes:02d}:{secs:02d}.{millis:03d}"
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def parse_time_str(timestr: str) -> float:
    """
    从时间字符串解析秒数。
    支持格式：HH:MM:SS.mmm, HH:MM:SS,MM:SS.mmm, SS.mmm, 纯秒数。
    失败返回 -1。
    """
    if not timestr:
        return -1.0
    s = timestr.strip().replace(",", ".")

    try:
        return float(s)
    except ValueError:
        pass

    parts = s.split(":")
    try:
        if len(parts) == 3:
            hours = float(parts[0])
            minutes = float(parts[1])
            seconds = float(parts[2])
            return hours * 3600 + minutes * 60 + seconds
        elif len(parts) == 2:
            minutes = float(parts[0])
            seconds = float(parts[1])
            return minutes * 60 + seconds
        elif len(parts) == 1:
            return float(parts[0])
    except ValueError:
        pass

    return -1.0
