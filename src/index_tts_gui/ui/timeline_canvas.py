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
from PySide6.QtCore import QPoint, QRect, QSize, Qt, QTimer, Signal
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
    QWheelEvent,
)
from PySide6.QtWidgets import QSizePolicy, QWidget

from index_tts_gui.core.subtitle import SubtitleItem, SubtitleTrack
from index_tts_gui.ui.audio_engine import AudioEngine


class TimelineCanvas(QWidget):

    playhead_moved = Signal(float)       # 播放头被手动移动
    subtitle_selected = Signal(int)      # 选中字幕（index）
    subtitle_moved = Signal(int, float, float)  # 字幕移动（index, new_start, new_end）
    double_click_time = Signal(float)    # 双击时间点
    subtitle_deleted = Signal(int)       # 删除字幕（index）

    HEADER_HEIGHT = 30
    WAVEFORM_HEIGHT = 80
    TRACK_Y_OFFSET = HEADER_HEIGHT + WAVEFORM_HEIGHT
    TRACK_HEIGHT = 50
    BLOCK_PADDING = 4
    HANDLE_WIDTH = 5

    COLOR_BG = QColor(37, 37, 37)
    COLOR_HEADER = QColor(51, 51, 51)
    COLOR_TICK = QColor(85, 85, 85)
    COLOR_TICK_TEXT = QColor(170, 170, 170)
    COLOR_WAVEFORM = QColor(76, 175, 80)
    COLOR_WAVEFORM_ALPHA = 153
    COLOR_PLAYHEAD = QColor(255, 50, 50)
    COLOR_SELECTION = QColor(255, 215, 0)
    COLOR_BLOCK_TEXT = QColor(238, 238, 238)
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
        self.hover_index = -1
        self.hover_edge: Optional[str] = None
        self.waveform_visible = True
        self.snap_enabled = True
        self.SNAP_THRESHOLD = 0.15

        self._font = QFont("Consolas", 8)
        self._block_font = QFont("Microsoft YaHei", 8)

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
        self.update()

    def refresh_waveform(self):
        if self.audio_engine and self.audio_engine.is_loaded():
            width = self.width()
            if width > 0:
                num_bars = min(width, 2000)
                self.waveform_bars = self.audio_engine.extract_waveform(num_bars)
        else:
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
                    label_y = self.HEADER_HEIGHT - tick_height - 2 - text_height
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
        if self.waveform_bars is None or len(self.waveform_bars) == 0:
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

        if self.audio_engine is None or self.audio_engine.duration <= 0:
            return

        total_duration = self.audio_engine.duration
        num_bars = len(self.waveform_bars)
        vis_start = max(0.0, self.offset)
        vis_end = self._x_to_time(self.width())

        bar_idx_start = max(0, int(vis_start / total_duration * num_bars))
        bar_idx_end = min(num_bars, int(vis_end / total_duration * num_bars) + 1)
        if bar_idx_start >= bar_idx_end:
            return

        center_y = self.HEADER_HEIGHT + self.WAVEFORM_HEIGHT / 2
        half_height = self.WAVEFORM_HEIGHT / 2 - 2
        bar_time_width = total_duration / num_bars
        pixel_per_bar = bar_time_width * self.zoom

        wf_color = QColor(
            self.COLOR_WAVEFORM.red(),
            self.COLOR_WAVEFORM.green(),
            self.COLOR_WAVEFORM.blue(),
            self.COLOR_WAVEFORM_ALPHA,
        )
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(wf_color))

        for i in range(bar_idx_start, bar_idx_end):
            bar_time = i * bar_time_width
            x = self._time_to_x(bar_time)
            bar_w = max(1.0, pixel_per_bar)
            if x + bar_w < 0 or x > self.width():
                continue
            min_peak = self.waveform_bars[i, 0]
            max_peak = self.waveform_bars[i, 1]
            y_top = center_y + min_peak * half_height
            y_bottom = center_y + max_peak * half_height
            painter.drawRect(
                int(x), int(y_top), int(bar_w) + 1, int(y_bottom - y_top) + 1
            )

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

            if idx == self.selected_index:
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
                painter.setPen(self.COLOR_BLOCK_TEXT)
                text_x = int(x) + 4
                text_y = track_y + (track_height - text_height) // 2
                text_w = int(w) - 8
                painter.drawText(
                    text_x, text_y, text_w, text_height,
                    Qt.AlignLeft | Qt.AlignVCenter, text,
                )

            if idx == self.selected_index or idx == self.hover_index:
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
        x = int(self._time_to_x(self.playhead_time))
        if x < -2 or x > self.width() + 2:
            return

        ph_color = self.COLOR_PLAYHEAD
        pen = QPen(ph_color)
        pen.setWidth(2)
        painter.setPen(pen)
        painter.drawLine(x, 0, x, self.height())

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
        label_x = x - text_rect.width() // 2
        label_y = self.HEADER_HEIGHT - triangle_size - text_rect.height() - 2
        painter.setPen(ph_color)
        painter.drawText(
            label_x, label_y, text_rect.width(), text_rect.height(),
            Qt.AlignCenter, time_str,
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

        if hit_index >= 0:
            self.selected_index = hit_index
            self.subtitle_selected.emit(hit_index)
            item = self.subtitle_track[hit_index - 1]
            if item:
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
                raw_start = self.drag_original_start + delta
                snapped_start = self._find_snap_time(raw_start, self.drag_subtitle_index)
                if snapped_start != raw_start:
                    delta = snapped_start - self.drag_original_start
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
            if self.TRACK_Y_OFFSET <= y < self.TRACK_Y_OFFSET + self.TRACK_HEIGHT:
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
                self.subtitle_selected.emit(hit_index)
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

        if key == Qt.Key_Delete or key == Qt.Key_Backspace:
            idx = self.delete_selected_subtitle()
            if idx >= 0:
                self.subtitle_deleted.emit(idx)
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

    def _ensure_playhead_visible(self):
        vis_start, vis_end = self._get_visible_time_range()
        margin = (vis_end - vis_start) * 0.1
        if self.playhead_time < vis_start + margin:
            self.offset = max(0.0, self.playhead_time - margin)
            self.update()
        elif self.playhead_time > vis_end - margin:
            self.offset = self.playhead_time - (vis_end - vis_start) + margin
            self.update()

    def resizeEvent(self, event):
        self.refresh_waveform()
        super().resizeEvent(event)

    def delete_selected_subtitle(self) -> int:
        idx = self.selected_index
        if idx >= 0 and self.subtitle_track:
            try:
                self.subtitle_track.remove_item(idx)
            except (IndexError, ValueError):
                pass
            self.selected_index = -1
            self.update()
        return idx

    def get_selected_subtitle_index(self) -> int:
        return self.selected_index

    def select_subtitle(self, index: int):
        self.selected_index = index
        self.update()

    def clear_selection(self):
        self.selected_index = -1
        self.update()
