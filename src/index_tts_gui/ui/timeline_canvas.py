"""
TimelineCanvas - 时间轴画布

绘制层次（从上到下）：
1. 时间刻度尺（HEADER_HEIGHT=30px）
2. 音频波形区域（WAVEFORM_HEIGHT=80px）
3. 字幕轨道区域（TRACK_HEIGHT=50px）
4. 播放头 — 红色竖线贯穿全部区域+顶部三角形手柄

交互：
- 左键拖拽空白处 → 平移时间轴
- 左键拖拽字幕块 → 移动字幕（保持时长不变）
- 左键拖拽字幕块左/右边缘 → 调整起止时间
- 滚轮 → 缩放时间轴（以鼠标位置为中心）
- 左键点击空白处 → 移动播放头到该时间
- 双击字幕块 → 选中并发射 subtitle_selected
- 双击空白处 → 发射 double_click_time
- Delete/Backspace → 删除选中的字幕
- 方向键 ← → 微调播放头
"""

from __future__ import annotations

import math
from typing import Optional, Tuple

import numpy as np
from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, QSize, Qt, QElapsedTimer, QTimer, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetrics,
    QKeyEvent,
    QMouseEvent,
    QPaintEvent,
    QPainter,
    QPainterPath,
    QPen,
    QPolygonF,
    QWheelEvent,
)
from PySide6.QtWidgets import QSizePolicy, QWidget

from index_tts_gui.core.subtitle import SubtitleItem, SubtitleTrack
from index_tts_gui.ui.audio_engine import AudioEngine


class TimelineCanvas(QWidget):

    playhead_moved = Signal(float)       # 播放头被手动移动
    subtitle_selected = Signal(list)     # 选中字幕 index 列表
    subtitle_moved = Signal(int, float, float)  # 字幕移动（index, new_start, new_end）
    double_click_time = Signal(float)    # 双击时间点
    subtitle_deleted = Signal(list)      # 删除字幕 index 列表
    razor_split = Signal(int, float)     # 剃刀切分（index, split_time）

    HEADER_HEIGHT = 43
    WAVEFORM_HEIGHT = 200            # 频谱图高度
    TRACK_Y_OFFSET = 4*(HEADER_HEIGHT + WAVEFORM_HEIGHT)/5  # 紧贴波形下边缘
    TRACK_HEIGHT = 55                # 字幕块区域高度
    BLOCK_PADDING = 4
    HANDLE_WIDTH = 5

    COLOR_BG = QColor(37, 37, 37)
    COLOR_HEADER = QColor(51, 51, 51)
    COLOR_TICK = QColor(85, 85, 85)
    COLOR_TICK_TEXT = QColor(170, 170, 170)
    COLOR_WAVEFORM = QColor(82, 165, 235)
    COLOR_WAVEFORM_ALPHA = 150
    COLOR_PLAYHEAD = QColor(255, 50, 50)
    COLOR_SELECTION = QColor(255, 215, 0)
    COLOR_BLOCK_TEXT = QColor(255, 255, 255)
    COLOR_HANDLE = QColor(255, 255, 255, 128)

    BLOCK_COLORS = [
        QColor(100, 150, 255),
        QColor(100, 220, 150),
        QColor(255, 180, 100),
        QColor(220, 140, 255),
        QColor(255, 220, 100),
        QColor(255, 130, 130),
        QColor(130, 220, 220),
        QColor(200, 200, 200),
    ]

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setMinimumHeight(self.TRACK_Y_OFFSET + self.TRACK_HEIGHT + 20)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)

        self.zoom = 50.0
        self.offset = 0.0
        self.playhead_time = 0.0

        self.audio_engine: Optional[AudioEngine] = None
        self.subtitle_track: Optional[SubtitleTrack] = None
        self.duration = 300.0
        self.waveform_bars: Optional[np.ndarray] = None
        self.waveform_region: Tuple[float, float] = (0.0, 0.0)  # bars 覆盖的时间区间

        self.dragging = False
        self.drag_mode: Optional[str] = None
        self.drag_start_x = 0
        self.drag_start_y = 0
        self.drag_start_offset = 0.0
        self.drag_subtitle_index = -1
        self.drag_original_start = 0.0
        self.drag_original_end = 0.0
        self.drag_delta_time = 0.0
        self._has_dragged = False

        self.selected_index = -1
        self.selected_indices: set[int] = set()
        self.hover_index = -1
        self.hover_edge: Optional[str] = None
        self.waveform_visible = True
        self.snap_enabled = True
        self.SNAP_THRESHOLD = 0.15
        self.tool_mode = "select"  # "select" | "razor"

        # 视野滚动过渡动画：seek/播放开始时丝滑滑向跟随位置，而不是跳变
        self.PLAYHEAD_ANCHOR = 0.85  # 跟随锚点：播放头在视野中的目标位置比例
        self.SCROLL_ANIM_MS = 250
        self.SCROLL_ANIM_THRESHOLD = 0.25  # offset 偏差超过该秒数才启动动画
        self._scroll_anim_start = 0.0
        self._scroll_anim_clock = QElapsedTimer()
        self._scroll_anim_timer = QTimer(self)
        self._scroll_anim_timer.setInterval(16)
        self._scroll_anim_timer.timeout.connect(self._on_scroll_anim)

        # 剃刀工具悬浮预览
        self._razor_preview_pos: Optional[QPoint] = None
        self._razor_preview_text: tuple[str, str] = ("", "")

        self._font = QFont("Consolas", 13)
        self._block_font = QFont("得意黑", 18)
        self._tooltip_font = QFont("文悦新青年体 (须授权)", 20)

    # ---- 数据设置 ----

    def set_audio_engine(self, engine: Optional[AudioEngine]):
        self.audio_engine = engine
        self.refresh_waveform()
        self.update()

    def set_subtitle_track(self, track: Optional[SubtitleTrack]):
        self.subtitle_track = track
        if track and track.total_duration > 0:
            self.duration = max(self.duration, track.total_duration)
        self.update()

    def set_duration(self, duration: float):
        self.duration = max(duration, 1.0)
        self.update()

    def set_playhead(self, time: float):
        self.playhead_time = max(0.0, min(time, self.duration))
        self._ensure_playhead_visible()
        self.update()

    def _ensure_playhead_visible(self):
        """视野跟随：播放头在视野内且未越过锚点（85% 处）时视野不动；
        越过锚点后钉在锚点上，波形在其下滚动。

        正常播放时目标每帧只微移，直接钉住（缓动逼近会让播放头晃动）；
        seek/播放开始等大跳转用短动画丝滑过渡。用户拖拽平移时不触发。
        """
        if self.dragging and self.drag_mode == "pan":
            return
        vis_start, vis_end = self._get_visible_time_range()
        vis_duration = vis_end - vis_start
        if vis_duration <= 0:
            return
        if self._scroll_anim_timer.isActive():
            return  # 过渡动画进行中，由定时器驱动 offset
        anchor = self.offset + vis_duration * self.PLAYHEAD_ANCHOR
        if self.offset <= self.playhead_time < anchor:
            return  # 播放头在锚点左侧的视野内，视野保持不动
        target = max(0.0, self.playhead_time - vis_duration * self.PLAYHEAD_ANCHOR)
        if abs(target - self.offset) > self.SCROLL_ANIM_THRESHOLD:
            self._start_scroll_anim()
        else:
            self.offset = target

    def _start_scroll_anim(self):
        self._scroll_anim_start = self.offset
        self._scroll_anim_clock.start()
        self._scroll_anim_timer.start()

    def _on_scroll_anim(self):
        if self.dragging and self.drag_mode == "pan":
            self._scroll_anim_timer.stop()
            return
        vis_start, vis_end = self._get_visible_time_range()
        vis_duration = vis_end - vis_start
        if vis_duration <= 0:
            self._scroll_anim_timer.stop()
            return
        # 目标随播放头实时重算：动画中再次 seek 不需要重启动画
        target = max(0.0, self.playhead_time - vis_duration * self.PLAYHEAD_ANCHOR)
        t = self._scroll_anim_clock.elapsed() / self.SCROLL_ANIM_MS
        if t >= 1.0:
            self._scroll_anim_timer.stop()
            self.offset = target
        else:
            # smoothstep 缓动，末端精确落在目标上，无残差晃动
            e = t * t * (3.0 - 2.0 * t)
            self.offset = self._scroll_anim_start + (target - self._scroll_anim_start) * e
        self.offset = max(0.0, self.offset)
        self.update()

    def refresh_waveform(self):
        """使波形缓存失效（换音频/窗口尺寸变化时调用）。

        实际提取推迟到绘制时按可见区域进行，这里不做任何重计算。
        """
        self.waveform_bars = None
        self.update()

    # ---- 坐标转换 ----

    def _time_to_x(self, time: float) -> float:
        return (time - self.offset) * self.zoom

    def _x_to_time(self, x: float) -> float:
        return x / self.zoom + self.offset

    # ---- 绘制 ----

    def paintEvent(self, event: QPaintEvent):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        painter.fillRect(self.rect(), self.COLOR_BG)
        self._draw_time_ruler(painter)
        if self.waveform_visible:
            self._draw_waveform(painter)
        self._draw_subtitle_blocks(painter)
        self._draw_playhead(painter)
        self._draw_razor_preview(painter)

        painter.end()

    def _draw_time_ruler(self, painter: QPainter):
        if self.zoom >= 200:
            small_step, medium_step, large_step = 0.1, 0.5, 1.0
        elif self.zoom >= 100:
            small_step, medium_step, large_step = 0.5, 1.0, 5.0
        elif self.zoom >= 50:
            small_step, medium_step, large_step = 1.0, 5.0, 10.0
        elif self.zoom >= 10:
            small_step, medium_step, large_step = 5.0, 10.0, 30.0
        else:
            small_step, medium_step, large_step = 10.0, 30.0, 60.0

        header_rect = QRect(0, 0, self.width(), self.HEADER_HEIGHT)
        painter.fillRect(header_rect, self.COLOR_HEADER)

        vis_start = max(0.0, self._x_to_time(-10))
        vis_end = self._x_to_time(self.width() + 10)
        t_start = math.floor(vis_start / small_step) * small_step
        t_end = math.ceil(vis_end / small_step) * small_step

        pen_small = QPen(self.COLOR_TICK)
        pen_small.setWidth(1)
        pen_medium = QPen(QColor(119, 119, 119))
        pen_medium.setWidth(1)
        pen_large = QPen(QColor(153, 153, 153))
        pen_large.setWidth(1)

        painter.setFont(self._font)
        fm = QFontMetrics(self._font)
        text_height = fm.height()

        i = 0
        while True:
            t = t_start + i * small_step
            if t > t_end:
                break
            i += 1
            x = self._time_to_x(t)
            if 0 <= x <= self.width():
                mod_large = t % large_step
                mod_medium = t % medium_step
                is_large = (
                    abs(mod_large) < small_step * 0.5
                    or abs(mod_large - large_step) < small_step * 0.5
                )
                is_medium = not is_large and (
                    abs(mod_medium) < small_step * 0.5
                    or abs(mod_medium - medium_step) < small_step * 0.5
                )

                if is_large:
                    painter.setPen(pen_large)
                    tick_height = 16
                    label = self._format_time(t, self.zoom >= 100)
                    text_rect = fm.boundingRect(label)
                    label_x = int(x) - text_rect.width() // 2
                    label_y = self.HEADER_HEIGHT - tick_height - text_height
                    painter.setPen(self.COLOR_TICK_TEXT)
                    painter.drawText(
                        label_x, label_y, text_rect.width(), text_height,
                        Qt.AlignCenter, label,
                    )
                    painter.setPen(pen_large)
                elif is_medium:
                    painter.setPen(pen_medium)
                    tick_height = 12
                else:
                    painter.setPen(pen_small)
                    tick_height = 8

                painter.drawLine(
                    int(x), self.HEADER_HEIGHT - tick_height,
                    int(x), self.HEADER_HEIGHT,
                )

    def _format_time(self, t: float, show_ms: bool = False) -> str:
        t = max(0.0, t)
        minutes = int(t // 60)
        seconds = int(t % 60)
        if show_ms or minutes == 0:
            millis = int((t - int(t)) * 1000)
            if minutes > 0:
                return f"{minutes:02d}:{seconds:02d}.{millis:03d}"
            return f"{seconds}.{millis:03d}"
        return f"{minutes:02d}:{seconds:02d}"

    def _draw_waveform(self, painter: QPainter):
        if self.audio_engine is None or not self.audio_engine.is_loaded() \
                or self.audio_engine.duration <= 0:
            painter.setPen(self.COLOR_TICK_TEXT)
            painter.setFont(self._font)
            fm = QFontMetrics(self._font)
            text = "未加载音频"
            text_rect = fm.boundingRect(text)
            text_x = (self.width() - text_rect.width()) // 2
            text_y = self.HEADER_HEIGHT + (self.WAVEFORM_HEIGHT - text_rect.height()) // 2
            painter.drawText(
                text_x, text_y, text_rect.width(), text_rect.height(),
                Qt.AlignCenter, text,
            )
            return

        total_duration = self.audio_engine.duration
        vis_start = max(0.0, self.offset)
        vis_end = self._x_to_time(self.width())
        if vis_end <= vis_start:
            return

        # 只按可见区域提取（两侧各留一屏余量）：任何缩放级别都保持约 2 条/像素，
        # 滚动时余量兜住，不用每帧重算；提取本身是向量化的，开销在毫秒级。
        need_extract = self.waveform_bars is None or len(self.waveform_bars) == 0
        if not need_extract:
            r_start, r_end = self.waveform_region
            region_px = max(1e-9, (r_end - r_start) * self.zoom)
            bpp = len(self.waveform_bars) / region_px
            if (
                vis_start < r_start - 1e-6
                or vis_end > r_end + 1e-6
                or bpp < 1.0
                or bpp > 8.0
            ):
                need_extract = True
        if need_extract:
            pad = vis_end - vis_start
            r_start = max(0.0, min(vis_start - pad, total_duration))
            r_end = min(total_duration, vis_end + pad)
            if r_end - r_start <= 0:
                self.waveform_bars = None
                return
            desired = max(500, min(8000, int((r_end - r_start) * self.zoom * 2)))
            self.waveform_bars = self.audio_engine.extract_waveform(
                desired, r_start, r_end
            )
            self.waveform_region = (r_start, r_end)

        bars = self.waveform_bars
        if bars is None or len(bars) == 0:
            return
        num_bars = len(bars)
        r_start, r_end = self.waveform_region
        region_dur = r_end - r_start
        bar_time_width = region_dur / num_bars
        pixel_per_bar = bar_time_width * self.zoom

        bar_idx_start = max(0, int((vis_start - r_start) / bar_time_width))
        bar_idx_end = min(num_bars, int((vis_end - r_start) / bar_time_width) + 1)
        if bar_idx_start >= bar_idx_end:
            return

        center_y = self.HEADER_HEIGHT + self.WAVEFORM_HEIGHT / 2
        half_height = self.WAVEFORM_HEIGHT / 2 - 1

        # 绘制背景
        bg_rect = QRect(0, self.HEADER_HEIGHT, self.width(), self.WAVEFORM_HEIGHT)
        painter.fillRect(bg_rect, QColor(30, 30, 35))

        # 绘制中心线
        center_pen = QPen(QColor(60, 60, 70), 1, Qt.DashLine)
        painter.setPen(center_pen)
        painter.drawLine(0, int(center_y), self.width(), int(center_y))

        # 构建波形路径（上半轮廓 → 下半轮廓 → 闭合）
        points_upper = []
        points_lower = []

        for i in range(bar_idx_start, bar_idx_end):
            bar_time = r_start + i * bar_time_width
            x = self._time_to_x(bar_time)
            bar_w = max(1.0, pixel_per_bar)
            if x + bar_w < 0 or x > self.width():
                continue
            cx = x + bar_w / 2
            min_p = bars[i, 0]
            max_p = bars[i, 1]
            points_upper.append((cx, center_y + min_p * half_height))
            points_lower.append((cx, center_y + max_p * half_height))

        if not points_upper:
            return

        # 闭合路径：上轮廓从左到右 → 下轮廓从右到左
        full_path = QPainterPath()
        full_path.moveTo(points_upper[0][0], center_y)
        for cx, y in points_upper:
            full_path.lineTo(cx, y)
        # 过渡到下半轮廓
        full_path.lineTo(points_upper[-1][0], center_y)
        for cx, y in reversed(points_lower):
            full_path.lineTo(cx, y)
        full_path.closeSubpath()

        # 渐变色填充
        from PySide6.QtGui import QLinearGradient
        gradient = QLinearGradient(0, self.HEADER_HEIGHT, 0, self.HEADER_HEIGHT + self.WAVEFORM_HEIGHT)
        mid_color = QColor(
            self.COLOR_WAVEFORM.red(),
            self.COLOR_WAVEFORM.green(),
            self.COLOR_WAVEFORM.blue(),
            200,
        )
        edge_color = QColor(
            self.COLOR_WAVEFORM.red(),
            self.COLOR_WAVEFORM.green(),
            self.COLOR_WAVEFORM.blue(),
            60,
        )
        gradient.setColorAt(0.0, edge_color)
        gradient.setColorAt(0.5, mid_color)
        gradient.setColorAt(1.0, edge_color)

        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(gradient))
        painter.drawPath(full_path)

        # 波峰描边线：只画上、下两条轮廓。
        # 描整个闭合路径会把左右边缘的竖线也画出来，造成可视区边缘伪影。
        outline_pen = QPen(QColor(
            self.COLOR_WAVEFORM.red(),
            self.COLOR_WAVEFORM.green(),
            self.COLOR_WAVEFORM.blue(),
            255,
        ), 1)
        painter.setPen(outline_pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawPolyline(QPolygonF([QPointF(cx, y) for cx, y in points_upper]))
        painter.drawPolyline(QPolygonF([QPointF(cx, y) for cx, y in points_lower]))

    def _draw_subtitle_blocks(self, painter: QPainter):
        if self.subtitle_track is None:
            return

        vis_start, vis_end = self._get_visible_time_range()
        vis_start -= 1.0
        vis_end += 1.0

        items = self.subtitle_track.get_items_in_range(vis_start, vis_end)
        track_y = self.TRACK_Y_OFFSET + self.BLOCK_PADDING
        track_height = self.TRACK_HEIGHT - 2 * self.BLOCK_PADDING

        painter.setFont(self._block_font)
        fm = QFontMetrics(self._block_font)
        text_height = fm.height()

        for item in items:
            idx = item.index
            start = item.start_time
            end = item.end_time

            if self.dragging and self.drag_mode == "move_subtitle" and idx == self.drag_subtitle_index:
                start = self.drag_original_start + self.drag_delta_time
                end = self.drag_original_end + self.drag_delta_time
            elif self.dragging and self.drag_mode == "resize_left" and idx == self.drag_subtitle_index:
                start = self.drag_original_start + self.drag_delta_time
                start = min(start, self.drag_original_end - 0.1)
                start = max(0.0, start)
            elif self.dragging and self.drag_mode == "resize_right" and idx == self.drag_subtitle_index:
                end = self.drag_original_end + self.drag_delta_time
                end = max(end, self.drag_original_start + 0.1)

            duration = end - start
            x = self._time_to_x(start)
            w = duration * self.zoom
            if w < 2.0:
                continue

            block_rect = QRect(int(x), track_y, int(w), track_height)
            color = self.BLOCK_COLORS[idx % len(self.BLOCK_COLORS)]
            if self.dragging and idx == self.drag_subtitle_index:
                color = QColor(
                    min(255, color.red() + 30),
                    min(255, color.green() + 30),
                    min(255, color.blue() + 30),
                )
            painter.setBrush(QBrush(color))

            if idx in self.selected_indices:
                pen = QPen(self.COLOR_SELECTION)
                pen.setWidth(2)
                painter.setPen(pen)
            elif idx == self.hover_index:
                pen = QPen(QColor(200, 200, 200))
                pen.setWidth(1)
                painter.setPen(pen)
            else:
                pen = QPen(color.darker(120))
                pen.setWidth(1)
                painter.setPen(pen)

            painter.drawRoundedRect(block_rect, 3, 3)

            if w > 30:
                text = item.text[:15] if len(item.text) <= 15 else item.text[:14] + "..."
                text_x = int(x) + 4
                text_y = track_y + (track_height - text_height) // 2
                text_w = int(w) - 8
                text_rect = QRect(text_x, text_y, text_w, text_height)

                # 文字描边：先画黑色偏移轮廓，再画白色填充
                outline_pen = QPen(QColor(0, 0, 0, 180), 2)
                painter.setPen(outline_pen)
                painter.drawText(text_rect, Qt.AlignLeft | Qt.AlignVCenter, text)

                painter.setPen(self.COLOR_BLOCK_TEXT)
                painter.drawText(text_rect, Qt.AlignLeft | Qt.AlignVCenter, text)

            if idx in self.selected_indices or idx == self.hover_index:
                painter.setPen(Qt.NoPen)
                painter.setBrush(QBrush(self.COLOR_HANDLE))
                left_handle = QRect(int(x), track_y + 2, self.HANDLE_WIDTH, track_height - 4)
                painter.drawRect(left_handle)
                right_handle = QRect(
                    int(x + w) - self.HANDLE_WIDTH, track_y + 2,
                    self.HANDLE_WIDTH, track_height - 4,
                )
                painter.drawRect(right_handle)

    def _draw_playhead(self, painter: QPainter):
        x = self._time_to_x(self.playhead_time)
        if x < -2 or x > self.width() + 2:
            return

        ph_color = self.COLOR_PLAYHEAD
        pen = QPen(ph_color)
        pen.setWidth(2)
        painter.setPen(pen)
        painter.drawLine(QPointF(x, self.HEADER_HEIGHT), QPointF(x, self.height()))

        triangle_size = 8
        triangle = QPainterPath()
        triangle.moveTo(x, self.HEADER_HEIGHT)
        triangle.lineTo(x - triangle_size, self.HEADER_HEIGHT - triangle_size)
        triangle.lineTo(x + triangle_size, self.HEADER_HEIGHT - triangle_size)
        triangle.closeSubpath()
        painter.setBrush(QBrush(ph_color))
        painter.setPen(Qt.NoPen)
        painter.drawPath(triangle)

        painter.setFont(self._font)
        fm = QFontMetrics(self._font)
        time_str = self._format_time(self.playhead_time, True)
        text_rect = fm.boundingRect(time_str)
        label_x = x - text_rect.width() / 2
        label_y = self.HEADER_HEIGHT - triangle_size - text_rect.height() - 2
        painter.setPen(ph_color)
        painter.drawText(
            QRectF(label_x, label_y, text_rect.width(), text_rect.height()),
            Qt.AlignCenter, time_str,
        )

    def _draw_razor_preview(self, painter: QPainter):
        """剃刀工具悬浮预览：单行显示切分后的前后文本。"""
        if (
            self.tool_mode != "razor"
            or not self._razor_preview_text[0]
            or self._razor_preview_pos is None
        ):
            return

        first_text, second_text = self._razor_preview_text
        first_display = first_text[:25] + ("..." if len(first_text) > 25 else "")
        second_display = second_text[:25] + ("..." if len(second_text) > 25 else "")
        preview_text = f"{first_display}  │  {second_display}"

        painter.setFont(self._tooltip_font)
        fm = QFontMetrics(self._tooltip_font)
        padding_h = 12
        padding_v = 8
        text_width = fm.horizontalAdvance(preview_text)
        box_width = text_width + padding_h * 2
        box_height = fm.height() + padding_v * 2

        px = self._razor_preview_pos.x() + 16
        py = self._razor_preview_pos.y() + 16
        # 避免超出右边界
        if px + box_width > self.width():
            px = self.width() - box_width - 8
        if py + box_height > self.height():
            py = self.height() - box_height - 8

        bg_rect = QRect(px, py, box_width, box_height)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(QColor(250, 250, 250, 240)))
        painter.drawRoundedRect(bg_rect, 6, 6)

        painter.setPen(QPen(QColor(30, 30, 30)))
        painter.drawText(
            px + padding_h, py + padding_v,
            text_width, fm.height(),
            Qt.AlignLeft | Qt.AlignVCenter, preview_text
        )

    # ---- 鼠标交互 ----

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() != Qt.LeftButton:
            return

        self.setFocus()
        x = int(event.position().x())
        y = int(event.position().y())

        self.drag_start_x = x
        self.drag_start_y = y
        self.drag_start_offset = self.offset
        self.drag_delta_time = 0.0
        self._has_dragged = False

        hit_index, hit_edge = -1, None
        if self.TRACK_Y_OFFSET <= y < self.TRACK_Y_OFFSET + self.TRACK_HEIGHT:
            hit_index, hit_edge = self._hit_test_subtitle(x, y)

        # 剃刀工具：点击字幕块直接切分
        if self.tool_mode == "razor" and hit_index >= 0:
            click_time = self._x_to_time(x)
            item = self.subtitle_track[hit_index - 1]
            if item and item.duration > 0:
                # 避免点击在边缘导致 split_time 刚好等于起止时间
                epsilon = min(0.05, item.duration * 0.05)
                click_time = max(item.start_time + epsilon, min(item.end_time - epsilon, click_time))
                self.razor_split.emit(hit_index, click_time)
            return

        if hit_index >= 0:
            ctrl_pressed = event.modifiers() & Qt.ControlModifier
            if ctrl_pressed:
                # Ctrl+点击：多选切换
                if hit_index in self.selected_indices:
                    self.selected_indices.discard(hit_index)
                    if self.selected_index == hit_index:
                        self.selected_index = max(self.selected_indices) if self.selected_indices else -1
                else:
                    self.selected_indices.add(hit_index)
                    self.selected_index = hit_index
            else:
                self.selected_index = hit_index
                self.selected_indices = {hit_index}

            self.subtitle_selected.emit(sorted(self.selected_indices))
            item = self.subtitle_track[hit_index - 1]
            if item and not ctrl_pressed:
                self.drag_subtitle_index = hit_index
                self.drag_original_start = item.start_time
                self.drag_original_end = item.end_time
                if hit_edge == "left":
                    self.drag_mode = "resize_left"
                elif hit_edge == "right":
                    self.drag_mode = "resize_right"
                else:
                    self.drag_mode = "move_subtitle"
                self.dragging = True
                self.grabMouse()
            self.update()
        else:
            # 点击空白处：清除选择并开始平移
            self.selected_index = -1
            self.selected_indices.clear()
            self.subtitle_selected.emit([])
            self.drag_mode = "pan"
            self.dragging = True
            self.grabMouse()

    def mouseMoveEvent(self, event: QMouseEvent):
        x = int(event.position().x())
        y = int(event.position().y())

        if self.dragging and not self._has_dragged:
            if abs(x - self.drag_start_x) > 3 or abs(y - self.drag_start_y) > 3:
                self._has_dragged = True

        if self.dragging:
            if self.drag_mode == "pan":
                dx = x - self.drag_start_x
                new_offset = self.drag_start_offset - dx / self.zoom
                self.offset = max(0.0, new_offset)
                self.update()
            elif self.drag_mode == "move_subtitle":
                dx = x - self.drag_start_x
                delta = dx / self.zoom
                duration = self.drag_original_end - self.drag_original_start
                raw_start = self.drag_original_start + delta
                raw_end = raw_start + duration
                # 左右边缘都参与磁吸，两边同时命中时取吸附量更小的一边
                snapped_start = self._find_snap_time(raw_start, self.drag_subtitle_index)
                snapped_end = self._find_snap_time(raw_end, self.drag_subtitle_index)
                start_snapped = snapped_start != raw_start
                end_snapped = snapped_end != raw_end
                if start_snapped and (
                    not end_snapped
                    or abs(snapped_start - raw_start) <= abs(snapped_end - raw_end)
                ):
                    delta = snapped_start - self.drag_original_start
                elif end_snapped:
                    delta = snapped_end - duration - self.drag_original_start
                if self.drag_original_start + delta < 0:
                    delta = -self.drag_original_start
                self.drag_delta_time = delta
                self.update()
            elif self.drag_mode == "resize_left":
                dx = x - self.drag_start_x
                delta = dx / self.zoom
                raw_start = self.drag_original_start + delta
                snapped_start = self._find_snap_time(raw_start, self.drag_subtitle_index)
                if snapped_start != raw_start:
                    delta = snapped_start - self.drag_original_start
                max_delta = self.drag_original_end - self.drag_original_start - 0.1
                delta = min(delta, max_delta)
                delta = max(-self.drag_original_start, delta)
                self.drag_delta_time = delta
                self.update()
            elif self.drag_mode == "resize_right":
                dx = x - self.drag_start_x
                delta = dx / self.zoom
                raw_end = self.drag_original_end + delta
                snapped_end = self._find_snap_time(raw_end, self.drag_subtitle_index)
                if snapped_end != raw_end:
                    delta = snapped_end - self.drag_original_end
                min_delta = self.drag_original_start - self.drag_original_end + 0.1
                delta = max(delta, min_delta)
                self.drag_delta_time = delta
                self.update()
        else:
            if self.tool_mode == "razor":
                self.setCursor(Qt.CrossCursor)
                if self.TRACK_Y_OFFSET <= y < self.TRACK_Y_OFFSET + self.TRACK_HEIGHT:
                    hit_index, _ = self._hit_test_subtitle(x, y)
                    if hit_index >= 0:
                        item = self.subtitle_track[hit_index - 1]
                        split_time = self._x_to_time(x)
                        if item.start_time < split_time < item.end_time:
                            ratio = (split_time - item.start_time) / item.duration
                            first, second = SubtitleTrack.preview_split(item.text, ratio)
                            self._razor_preview_pos = QPoint(x, y)
                            self._razor_preview_text = (first, second)
                            self.update()
                            return
                self._razor_preview_pos = None
                self._razor_preview_text = ("", "")
                self.update()
            elif self.TRACK_Y_OFFSET <= y < self.TRACK_Y_OFFSET + self.TRACK_HEIGHT:
                hit_index, hit_edge = self._hit_test_subtitle(x, y)
                self.hover_index = hit_index
                self.hover_edge = hit_edge
                if hit_index >= 0:
                    if hit_edge in ("left", "right"):
                        self.setCursor(Qt.SizeHorCursor)
                    else:
                        self.setCursor(Qt.OpenHandCursor)
                else:
                    self.setCursor(Qt.ArrowCursor)
            else:
                self.hover_index = -1
                self.hover_edge = None
                self.setCursor(Qt.ArrowCursor)

    def mouseReleaseEvent(self, event: QMouseEvent):
        if not self.dragging:
            return

        if event.button() == Qt.LeftButton:
            try:
                self.releaseMouse()
            except Exception:
                pass

            if not self._has_dragged and self.drag_mode == "pan":
                x = int(event.position().x())
                click_time = self._x_to_time(x)
                click_time = max(0.0, min(click_time, self.duration))
                self.playhead_time = click_time
                self.playhead_moved.emit(click_time)
                self.update()
            elif self.drag_mode in ("move_subtitle", "resize_left", "resize_right"):
                if self.drag_subtitle_index >= 0:
                    if self.drag_mode == "move_subtitle":
                        new_start = self.drag_original_start + self.drag_delta_time
                        new_end = self.drag_original_end + self.drag_delta_time
                    elif self.drag_mode == "resize_left":
                        new_start = self.drag_original_start + self.drag_delta_time
                        new_end = self.drag_original_end
                    else:
                        new_start = self.drag_original_start
                        new_end = self.drag_original_end + self.drag_delta_time

                    new_start = max(0.0, new_start)
                    new_end = max(new_start + 0.1, new_end)
                    self.subtitle_moved.emit(self.drag_subtitle_index, new_start, new_end)

            self.dragging = False
            self.drag_mode = None
            self.drag_subtitle_index = -1
            self.drag_delta_time = 0.0
            self._has_dragged = False
            self.update()

    def mouseDoubleClickEvent(self, event: QMouseEvent):
        x = int(event.position().x())
        y = int(event.position().y())
        time = self._x_to_time(x)

        if self.TRACK_Y_OFFSET <= y < self.TRACK_Y_OFFSET + self.TRACK_HEIGHT:
            hit_index, _ = self._hit_test_subtitle(x, y)
            if hit_index >= 0:
                self.selected_index = hit_index
                self.subtitle_selected.emit([hit_index])
                return

        self.double_click_time.emit(max(0.0, time))

    def wheelEvent(self, event: QWheelEvent):
        mouse_x = int(event.position().x())
        old_time = self._x_to_time(mouse_x)
        delta = event.angleDelta().y()
        if delta > 0:
            new_zoom = self.zoom * 1.15
        else:
            new_zoom = self.zoom / 1.15

        new_zoom = max(1.0, min(5000.0, new_zoom))
        self.zoom = new_zoom
        self.offset = old_time - mouse_x / self.zoom
        self.offset = max(0.0, self.offset)
        self.update()

    def keyPressEvent(self, event: QKeyEvent):
        key = event.key()

        if key == Qt.Key_V:
            self.tool_mode = "select"
            self._razor_preview_pos = None
            self._razor_preview_text = ("", "")
            self.setCursor(Qt.ArrowCursor)
            self.update()
        elif key == Qt.Key_C:
            self.tool_mode = "razor"
            self.setCursor(Qt.CrossCursor)
            self.update()
        elif key == Qt.Key_Delete or key == Qt.Key_Backspace:
            deleted = self.delete_selected_subtitle()
            if deleted:
                self.subtitle_deleted.emit(deleted)
        elif key == Qt.Key_Left:
            step = max(0.01, 1.0 / self.zoom)
            self.playhead_time = max(0.0, self.playhead_time - step)
            self.playhead_moved.emit(self.playhead_time)
            self._ensure_playhead_visible()
            self.update()
        elif key == Qt.Key_Right:
            step = max(0.01, 1.0 / self.zoom)
            self.playhead_time = min(self.duration, self.playhead_time + step)
            self.playhead_moved.emit(self.playhead_time)
            self._ensure_playhead_visible()
            self.update()
        elif key == Qt.Key_0 and event.modifiers() & Qt.ControlModifier:
            self.zoom = 50.0
            self.offset = 0.0
            self.update()
        elif key == Qt.Key_Home:
            self.playhead_time = 0.0
            self.playhead_moved.emit(0.0)
            self.update()
        elif key == Qt.Key_End:
            self.playhead_time = self.duration
            self.playhead_moved.emit(self.duration)
            self.update()
        else:
            super().keyPressEvent(event)

    def enterEvent(self, event):
        self.setFocus()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._razor_preview_pos = None
        self._razor_preview_text = ("", "")
        self.update()
        super().leaveEvent(event)

    # ---- 辅助方法 ----

    def _hit_test_subtitle(self, x: int, y: int) -> Tuple[int, Optional[str]]:
        if y < self.TRACK_Y_OFFSET or y >= self.TRACK_Y_OFFSET + self.TRACK_HEIGHT:
            return -1, None
        if self.subtitle_track is None:
            return -1, None

        vis_start, vis_end = self._get_visible_time_range()
        items = self.subtitle_track.get_items_in_range(vis_start - 1, vis_end + 1)
        for item in items:
            start = item.start_time
            end = item.end_time
            block_x = self._time_to_x(start)
            block_w = (end - start) * self.zoom
            if block_w < 2.0:
                continue
            if block_x <= x <= block_x + block_w:
                if abs(x - block_x) <= self.HANDLE_WIDTH:
                    return item.index, "left"
                if abs(x - (block_x + block_w)) <= self.HANDLE_WIDTH:
                    return item.index, "right"
                return item.index, None
        return -1, None

    def _find_snap_time(self, target_time: float, exclude_index: int = -1) -> float:
        if not self.snap_enabled or self.subtitle_track is None:
            return target_time

        best_snap = target_time
        min_dist = self.SNAP_THRESHOLD
        for item in self.subtitle_track.items:
            if item.index == exclude_index:
                continue
            for t in (item.start_time, item.end_time):
                dist = abs(target_time - t)
                if dist < min_dist:
                    min_dist = dist
                    best_snap = t
        return best_snap

    def _get_visible_time_range(self) -> Tuple[float, float]:
        return self.offset, self._x_to_time(self.width())

    def resizeEvent(self, event):
        self.refresh_waveform()
        super().resizeEvent(event)

    def delete_selected_subtitle(self) -> list[int]:
        """删除所有选中的字幕，返回被删除的 index 列表。"""
        deleted = []
        if not self.subtitle_track:
            return deleted
        # 从大到小删除，避免索引变化
        for idx in sorted(self.selected_indices, reverse=True):
            try:
                self.subtitle_track.remove_item(idx)
                deleted.append(idx)
            except (IndexError, ValueError):
                pass
        self.selected_index = -1
        self.selected_indices.clear()
        self.update()
        return deleted

    def get_selected_subtitle_index(self) -> int:
        return self.selected_index

    def get_selected_indices(self) -> list[int]:
        return sorted(self.selected_indices)

    def select_subtitle(self, index: int):
        self.selected_index = index
        if index >= 0:
            self.selected_indices = {index}
        else:
            self.selected_indices.clear()
        self.update()

    def clear_selection(self):
        self.selected_index = -1
        self.selected_indices.clear()
        self.update()
