"""
字幕编辑面板：时间轴 + 表格 + 文本编辑器 + 音频播放

参照 Kimi_Agent_sub/project 的 subtitle_edit.py / timeline_canvas.py 重构。
"""

from __future__ import annotations

import os
from typing import Callable, List, Optional

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QFont, QColor
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFontComboBox,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QSlider,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from index_tts_gui.core.project import Project
from index_tts_gui.core.subtitle import (
    SubtitleEntry,
    SubtitleItem,
    SubtitleStyle,
    SubtitleTrack,
    parse_time_str,
    seconds_to_time_str,
)
from index_tts_gui.core.subtitler import (
    generate_srt_from_sentences,
    generate_srt_from_sentences_with_pauses,
    entries_to_srt,
)
from index_tts_gui.ui.audio_engine import AudioEngine
from index_tts_gui.ui.timeline_canvas import TimelineCanvas


class SubtitlePanel(QWidget):
    """字幕编辑 + 音频播放 + 时间轴"""

    def __init__(self, project: Optional[Project] = None):
        super().__init__()
        self._project: Optional[Project] = project
        self._track = SubtitleTrack()
        self._audio_path: str = ""
        self._get_manuscript_text: Optional[Callable[[], str]] = None
        self._block_signals = False
        self._current_edit_index = -1

        # 音频
        self._player = QMediaPlayer()
        self._audio_output = QAudioOutput()
        self._player.setAudioOutput(self._audio_output)
        self._player.positionChanged.connect(self._on_position_changed)
        self._player.durationChanged.connect(self._on_duration_changed)

        # 波形引擎
        self._audio_engine = AudioEngine()

        self._setup_ui()
        self._connect_signals()
        self._refresh_project_audio()

    # ── UI ──

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # 顶部音频控制条
        top = QHBoxLayout()
        self._btn_load_audio = QPushButton("📂 加载音频")
        self._btn_load_audio.setToolTip("默认会自动加载工程目录下的 full_dub.wav")
        top.addWidget(self._btn_load_audio)

        self._btn_play = QPushButton("▶ 播放")
        self._btn_play.setEnabled(False)
        top.addWidget(self._btn_play)

        self._btn_stop = QPushButton("⏹ 停止")
        self._btn_stop.setEnabled(False)
        top.addWidget(self._btn_stop)

        self._time_label = QLabel("00:00:00.000 / 00:00:00.000")
        self._time_label.setStyleSheet("font-family: Consolas, monospace;")
        top.addWidget(self._time_label)

        self._seek = QSlider(Qt.Horizontal)
        self._seek.setEnabled(False)
        top.addWidget(self._seek, 1)

        layout.addLayout(top)

        # 时间轴画布
        self._timeline = TimelineCanvas()
        self._timeline.setMinimumHeight(220)
        layout.addWidget(self._timeline, 1)

        # 下部：表格 + 编辑器
        self._splitter = QSplitter(Qt.Vertical)

        # 表格
        self._table = QTableWidget()
        self._table.setColumnCount(5)
        self._table.setHorizontalHeaderLabels(["序号", "开始时间", "结束时间", "时长", "文本"])
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._table.setAlternatingRowColors(True)
        self._table.setSortingEnabled(True)

        self._table.setColumnWidth(0, 60)
        self._table.setColumnWidth(1, 110)
        self._table.setColumnWidth(2, 110)
        self._table.setColumnWidth(3, 90)
        self._table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Fixed)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Fixed)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Fixed)
        self._table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Fixed)
        self._splitter.addWidget(self._table)

        # 编辑器 + 样式
        editor_group = QGroupBox("字幕编辑")
        editor_layout = QVBoxLayout(editor_group)
        editor_layout.setContentsMargins(8, 12, 8, 8)
        editor_layout.setSpacing(6)

        self._text_edit = QTextEdit()
        self._text_edit.setAcceptRichText(False)
        self._text_edit.setMinimumHeight(80)
        self._text_edit.setPlaceholderText("选中字幕后在此编辑文本...")
        editor_layout.addWidget(self._text_edit)

        # 样式工具栏
        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)

        self._bold_check = QCheckBox("B")
        self._bold_check.setToolTip("粗体")
        toolbar.addWidget(self._bold_check)

        self._italic_check = QCheckBox("I")
        self._italic_check.setToolTip("斜体")
        toolbar.addWidget(self._italic_check)

        self._underline_check = QCheckBox("U")
        self._underline_check.setToolTip("下划线")
        toolbar.addWidget(self._underline_check)

        toolbar.addSpacing(12)

        toolbar.addWidget(QLabel("字体:"))
        self._font_combo = QFontComboBox()
        toolbar.addWidget(self._font_combo)

        toolbar.addWidget(QLabel("字号:"))
        self._size_spin = QSpinBox()
        self._size_spin.setRange(8, 200)
        self._size_spin.setValue(24)
        toolbar.addWidget(self._size_spin)

        self._color_btn = QPushButton()
        self._color_btn.setFixedSize(28, 28)
        self._color_btn.setToolTip("选择主色")
        self._update_color_btn_icon("#FFFFFF")
        toolbar.addWidget(self._color_btn)

        toolbar.addWidget(QLabel("描边:"))
        self._outline_spin = QDoubleSpinBox()
        self._outline_spin.setRange(0, 5)
        self._outline_spin.setSingleStep(0.1)
        self._outline_spin.setValue(1.0)
        toolbar.addWidget(self._outline_spin)

        toolbar.addWidget(QLabel("对齐:"))
        self._align_combo = QComboBox()
        self._align_combo.addItems([
            "下中", "下左", "下右", "中中", "中左", "中右", "上中", "上左", "上右"
        ])
        self._align_combo.setCurrentIndex(0)
        toolbar.addWidget(self._align_combo)

        toolbar.addStretch()
        editor_layout.addLayout(toolbar)

        self._info_label = QLabel("#0 | --:--:--.--- --> --:--:--.--- | -")
        self._info_label.setStyleSheet("color: #666;")
        editor_layout.addWidget(self._info_label)

        self._splitter.addWidget(editor_group)
        self._splitter.setSizes([320, 240])
        layout.addWidget(self._splitter, 2)

        # 底部按钮
        btn_row = QHBoxLayout()

        self._btn_split = QPushButton("✂ 切分")
        self._btn_split.setToolTip("将选中的字幕从中间时间切分为两条")
        self._btn_split.setEnabled(False)
        btn_row.addWidget(self._btn_split)

        self._btn_merge = QPushButton("⛓ 合并")
        self._btn_merge.setToolTip("合并选中的连续字幕")
        self._btn_merge.setEnabled(False)
        btn_row.addWidget(self._btn_merge)

        self._btn_delete = QPushButton("🗑 删除")
        self._btn_delete.setToolTip("删除选中的字幕")
        self._btn_delete.setEnabled(False)
        btn_row.addWidget(self._btn_delete)

        btn_row.addStretch()

        self._btn_regenerate = QPushButton("🔄 重新生成")
        self._btn_regenerate.setToolTip("从 output_tts/ 分句 WAV 重新分析生成")
        btn_row.addWidget(self._btn_regenerate)

        self._btn_export = QPushButton("📤 导出 SRT")
        self._btn_export.setEnabled(False)
        self._btn_export.setStyleSheet("""
            QPushButton {
                background: #4caf50; color: white;
                padding: 8px 16px; border-radius: 4px;
                font-weight: bold;
            }
            QPushButton:hover { background: #388e3c; }
            QPushButton:disabled { background: #ccc; }
        """)
        btn_row.addWidget(self._btn_export)

        layout.addLayout(btn_row)

    def _connect_signals(self):
        # 音频控制
        self._btn_load_audio.clicked.connect(self._load_audio)
        self._btn_play.clicked.connect(self._toggle_play)
        self._btn_stop.clicked.connect(self._stop)
        self._seek.sliderMoved.connect(self._seek_to)
        self._seek.sliderReleased.connect(self._on_slider_released)

        # 表格
        self._table.itemSelectionChanged.connect(self._on_table_selection_changed)
        self._table.itemChanged.connect(self._on_time_cell_changed)

        # 文本编辑器
        self._text_edit.textChanged.connect(self._on_text_changed)

        # 样式
        self._bold_check.toggled.connect(self._on_style_changed)
        self._italic_check.toggled.connect(self._on_style_changed)
        self._underline_check.toggled.connect(self._on_style_changed)
        self._font_combo.currentFontChanged.connect(self._on_style_changed)
        self._size_spin.valueChanged.connect(self._on_style_changed)
        self._color_btn.clicked.connect(self._choose_color)
        self._outline_spin.valueChanged.connect(self._on_style_changed)
        self._align_combo.currentIndexChanged.connect(self._on_style_changed)

        # 按钮
        self._btn_split.clicked.connect(self._split_selected)
        self._btn_merge.clicked.connect(self._merge_selected)
        self._btn_delete.clicked.connect(self._delete_selected)
        self._btn_regenerate.clicked.connect(self._regenerate)
        self._btn_export.clicked.connect(self._export_srt)

        # 时间轴
        self._timeline.playhead_moved.connect(self._on_timeline_playhead_moved)
        self._timeline.subtitle_selected.connect(self._on_timeline_subtitle_selected)
        self._timeline.subtitle_moved.connect(self._on_timeline_subtitle_moved)
        self._timeline.double_click_time.connect(self._on_timeline_double_click)
        self._timeline.subtitle_deleted.connect(self._on_timeline_subtitle_deleted)

    # ── 音频 ──

    def _refresh_project_audio(self):
        """切换工程后自动尝试加载 full_dub.wav"""
        self._stop()
        self._audio_path = ""
        self._audio_engine.clear()
        self._timeline.set_audio_engine(None)

        output_dir = self._output_dir()
        auto_path = os.path.join(output_dir, "full_dub.wav")
        if os.path.exists(auto_path):
            self._load_audio_path(auto_path)
        else:
            self._btn_load_audio.setText("📂 加载音频")
            self._update_time_label(0, 0)
            self._seek.setEnabled(False)
            self._btn_play.setEnabled(False)
            self._btn_stop.setEnabled(False)

    def _load_audio(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "加载音频", self._output_dir(), "音频文件 (*.wav *.mp3 *.flac)"
        )
        if path:
            self._load_audio_path(path)

    def _load_audio_path(self, path: str):
        self._audio_path = path
        self._player.setSource(QUrl.fromLocalFile(path))

        # 后台加载波形
        success = self._audio_engine.load_audio(path)
        if success:
            self._timeline.set_audio_engine(self._audio_engine)
            self._timeline.set_duration(self._audio_engine.duration)
        else:
            self._audio_engine.clear()
            self._timeline.set_audio_engine(None)

        self._btn_load_audio.setText(f"📂 {os.path.basename(path)}")
        self._btn_play.setEnabled(True)
        self._btn_stop.setEnabled(True)
        self._seek.setEnabled(True)

    def _toggle_play(self):
        if self._player.playbackState() == QMediaPlayer.PlayingState:
            self._player.pause()
            self._btn_play.setText("▶ 播放")
        else:
            self._player.play()
            self._btn_play.setText("⏸ 暂停")

    def _stop(self):
        self._player.stop()
        self._btn_play.setText("▶ 播放")

    def _seek_to(self, pos_ms: int):
        self._player.setPosition(pos_ms)

    def _on_slider_released(self):
        self._player.setPosition(self._seek.value())

    def _on_position_changed(self, pos_ms: int):
        dur = self._player.duration()
        if dur > 0:
            self._seek.blockSignals(True)
            self._seek.setRange(0, dur)
            self._seek.setValue(pos_ms)
            self._seek.blockSignals(False)

            pos_s = pos_ms / 1000.0
            dur_s = dur / 1000.0
            self._update_time_label(pos_s, dur_s)
            self._timeline.set_playhead(pos_s)

            # 高亮当前字幕
            self._highlight_current(pos_s)

    def _on_duration_changed(self, dur_ms: int):
        self._seek.setRange(0, max(1, dur_ms))
        dur_s = dur_ms / 1000.0
        self._timeline.set_duration(dur_s)
        self._update_time_label(self._player.position() / 1000.0, dur_s)

    def _update_time_label(self, pos_s: float, dur_s: float):
        self._time_label.setText(
            f"{seconds_to_time_str(pos_s)} / {seconds_to_time_str(dur_s)}"
        )

    def _highlight_current(self, pos_s: float):
        item = self._track.get_item_at_time(pos_s)
        if item:
            self._timeline.select_subtitle(item.index)
            self.select_row(item.index)

    # ── 时间轴回调 ──

    def _on_timeline_playhead_moved(self, time: float):
        dur_ms = self._player.duration()
        if dur_ms > 0:
            self._player.setPosition(int(time * 1000))
        self._timeline.set_playhead(time)

    def _on_timeline_subtitle_selected(self, index: int):
        self.select_row(index)

    def _on_timeline_subtitle_moved(self, index: int, start: float, end: float):
        item = self._track.get_item(index)
        if item is None:
            return
        item.start_time = start
        item.end_time = end
        self._track.sort()
        self._track.reindex()
        self.refresh_table()
        self.select_row(item.index)
        self._timeline.set_subtitle_track(self._track)

    def _on_timeline_double_click(self, time: float):
        """双击空白处：在当前时间添加一条空字幕"""
        end_time = min(time + 2.0, self._timeline.duration)
        new_item = SubtitleItem(0, time, end_time, "")
        self._track.add_item(new_item)
        self.refresh_table()
        self._timeline.set_subtitle_track(self._track)
        self.select_row(new_item.index)

    def _on_timeline_subtitle_deleted(self, index: int):
        self._track.remove_item(index)
        self.refresh_table()
        self._timeline.set_subtitle_track(self._track)
        self._update_button_states()

    # ── 工程与数据加载 ──

    def set_manuscript_source(self, get_text: Callable[[], str]):
        self._get_manuscript_text = get_text

    def set_project(self, project: Project):
        self._project = project
        self._track = SubtitleTrack()
        self._current_edit_index = -1
        self._text_edit.clear()
        self._timeline.set_subtitle_track(self._track)
        self._timeline.set_duration(0)
        self._timeline.set_playhead(0)
        self.refresh_table()
        self._refresh_project_audio()
        self._update_button_states()

    def _output_dir(self) -> str:
        if self._project:
            return self._project.output_dir
        return "output_tts"

    def load_entries(self, entries: List[SubtitleEntry]):
        """载入字幕条目（与合成流水线兼容），并刷新 full_dub.wav 音频。"""
        self._track = SubtitleTrack.from_entries(entries)
        self._timeline.set_subtitle_track(self._track)

        # 合并完成后 full_dub.wav 可能被覆盖，强制重新加载以同步波形和播放器
        full_dub = os.path.join(self._output_dir(), "full_dub.wav")
        if os.path.exists(full_dub):
            self._load_audio_path(full_dub)
        elif self._audio_engine.is_loaded():
            self._timeline.set_duration(self._audio_engine.duration)
        elif self._track.total_duration > 0:
            self._timeline.set_duration(self._track.total_duration)

        self.refresh_table()
        self._update_button_states()

    def get_entries(self) -> List[SubtitleEntry]:
        return self._track.to_entries()

    # ── 表格 ──

    def refresh_table(self):
        self._block_signals = True
        try:
            self._table.setSortingEnabled(False)
            self._table.setRowCount(0)
            for item in self._track.items:
                self._add_subtitle_row(item)
            self._table.setSortingEnabled(True)
        finally:
            self._block_signals = False

    def _add_subtitle_row(self, item: SubtitleItem):
        row = self._table.rowCount()
        self._table.insertRow(row)

        idx_item = QTableWidgetItem(str(item.index))
        idx_item.setTextAlignment(Qt.AlignCenter)
        idx_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
        self._table.setItem(row, 0, idx_item)

        start_item = QTableWidgetItem(seconds_to_time_str(item.start_time))
        start_item.setTextAlignment(Qt.AlignCenter)
        self._table.setItem(row, 1, start_item)

        end_item = QTableWidgetItem(seconds_to_time_str(item.end_time))
        end_item.setTextAlignment(Qt.AlignCenter)
        self._table.setItem(row, 2, end_item)

        dur_item = QTableWidgetItem(f"{item.duration:.3f} s")
        dur_item.setTextAlignment(Qt.AlignCenter)
        dur_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
        self._table.setItem(row, 3, dur_item)

        text_display = item.text.replace("\n", " ")[:80]
        text_item = QTableWidgetItem(text_display)
        self._table.setItem(row, 4, text_item)

    def select_row(self, index: int):
        if index < 1:
            return
        target_row = -1
        for row in range(self._table.rowCount()):
            idx_item = self._table.item(row, 0)
            if idx_item and int(idx_item.text()) == index:
                target_row = row
                break
        if target_row < 0:
            return

        self._block_signals = True
        try:
            self._table.selectRow(target_row)
            self._table.scrollToItem(
                self._table.item(target_row, 0),
                QAbstractItemView.PositionAtCenter,
            )
            item = self._track.get_item(index)
            if item is not None:
                self._current_edit_index = index
                self._text_edit.setPlainText(item.text)
                self._load_style_controls(item)
                self._update_info_label()
        finally:
            self._block_signals = False

    def _on_table_selection_changed(self):
        if self._block_signals:
            return
        selected = self._table.selectedItems()
        if not selected:
            self._current_edit_index = -1
            self._update_button_states()
            return

        row = selected[0].row()
        idx_item = self._table.item(row, 0)
        if idx_item is None:
            return
        try:
            index = int(idx_item.text())
        except ValueError:
            return

        self._current_edit_index = index
        item = self._track.get_item(index)
        if item is not None:
            self._text_edit.setPlainText(item.text)
            self._load_style_controls(item)
            self._update_info_label()

        self._timeline.select_subtitle(index)
        self._update_button_states()

    def _on_time_cell_changed(self, item: QTableWidgetItem):
        if self._block_signals:
            return
        if item is None:
            return
        row = item.row()
        column = item.column()
        if column not in (1, 2):
            return

        parsed = parse_time_str(item.text())
        if parsed < 0:
            self._restore_time_cell(row, column)
            return

        idx_item = self._table.item(row, 0)
        if idx_item is None:
            return
        try:
            index = int(idx_item.text())
        except ValueError:
            return

        target = self._track.get_item(index)
        if target is None:
            return

        if column == 1:
            target.start_time = parsed
        else:
            target.end_time = parsed

        if target.end_time <= target.start_time:
            target.end_time = target.start_time + 0.1

        self._track.sort()
        self._track.reindex()
        self.refresh_table()
        self.select_row(target.index)
        self._timeline.set_subtitle_track(self._track)

    def _restore_time_cell(self, row: int, column: int):
        idx_item = self._table.item(row, 0)
        if idx_item is None:
            return
        try:
            index = int(idx_item.text())
        except ValueError:
            return
        item = self._track.get_item(index)
        if item is None:
            return
        self._block_signals = True
        try:
            if column == 1:
                self._table.item(row, 1).setText(seconds_to_time_str(item.start_time))
            else:
                self._table.item(row, 2).setText(seconds_to_time_str(item.end_time))
        finally:
            self._block_signals = False

    # ── 文本编辑 ──

    def _on_text_changed(self):
        if self._block_signals or self._current_edit_index < 0:
            return
        text = self._text_edit.toPlainText()
        item = self._track.get_item(self._current_edit_index)
        if item is None:
            return
        item.text = text
        for row in range(self._table.rowCount()):
            idx_item = self._table.item(row, 0)
            if idx_item and int(idx_item.text()) == self._current_edit_index:
                self._block_signals = True
                try:
                    self._table.item(row, 4).setText(text.replace("\n", " ")[:80])
                finally:
                    self._block_signals = False
                break
        self._timeline.update()

    # ── 样式 ──

    def _load_style_controls(self, item: SubtitleItem):
        style = item.style if item.style else self._track.default_style
        if style is None:
            return
        self._block_signals = True
        try:
            self._bold_check.setChecked(style.bold)
            self._italic_check.setChecked(style.italic)
            self._underline_check.setChecked(style.underline)
            self._font_combo.setCurrentFont(QFont(style.font_name))
            self._size_spin.setValue(style.font_size)
            self._update_color_btn_icon(style.primary_color)
            self._outline_spin.setValue(style.outline_width)
            align_map = {2: 0, 1: 1, 3: 2, 5: 3, 4: 4, 6: 5, 8: 6, 7: 7, 9: 8}
            self._align_combo.setCurrentIndex(align_map.get(style.alignment, 0))
        finally:
            self._block_signals = False

    def _on_style_changed(self):
        if self._block_signals or self._current_edit_index < 0:
            return
        item = self._track.get_item(self._current_edit_index)
        if item is None:
            return
        if item.style is None:
            item.style = SubtitleStyle()
        style = item.style
        style.bold = self._bold_check.isChecked()
        style.italic = self._italic_check.isChecked()
        style.underline = self._underline_check.isChecked()
        style.font_name = self._font_combo.currentFont().family()
        style.font_size = self._size_spin.value()
        style.outline_width = self._outline_spin.value()
        align_values = [2, 1, 3, 5, 4, 6, 8, 7, 9]
        style.alignment = align_values[self._align_combo.currentIndex()]

    def _choose_color(self):
        current = QColor("#FFFFFF")
        if self._current_edit_index >= 0:
            item = self._track.get_item(self._current_edit_index)
            if item is not None:
                style = item.style if item.style else self._track.default_style
                if style:
                    current = QColor(style.primary_color)
        color = QColorDialog.getColor(current, self, "选择主色")
        if color.isValid():
            color_str = color.name()
            self._update_color_btn_icon(color_str)
            if self._current_edit_index >= 0:
                item = self._track.get_item(self._current_edit_index)
                if item is not None:
                    if item.style is None:
                        item.style = SubtitleStyle()
                    item.style.primary_color = color_str

    def _update_color_btn_icon(self, color_str: str):
        try:
            color = QColor(color_str)
        except Exception:
            color = QColor("#FFFFFF")
        self._color_btn.setStyleSheet(
            f"background-color: {color.name()}; border: 1px solid #888888;"
        )

    # ── 信息标签 ──

    def _update_info_label(self):
        item = self._track.get_item(self._current_edit_index)
        if item is None:
            self._info_label.setText("#0 | --:--:--.--- --> --:--:--.--- | -")
            return
        self._info_label.setText(
            f"#{item.index} | {seconds_to_time_str(item.start_time)} --> "
            f"{seconds_to_time_str(item.end_time)} | {item.duration:.3f}s"
        )

    # ── 切分/合并/删除 ──

    def _split_selected(self):
        if self._current_edit_index < 0:
            return
        item = self._track.get_item(self._current_edit_index)
        if item is None:
            return
        split_time = (item.start_time + item.end_time) / 2.0
        try:
            self._track.split_item(self._current_edit_index, split_time)
        except (IndexError, ValueError):
            return
        self.refresh_table()
        self._timeline.set_subtitle_track(self._track)
        self.select_row(self._current_edit_index)

    def _merge_selected(self):
        rows = set()
        for item in self._table.selectedItems():
            rows.add(item.row())
        if len(rows) < 2:
            return
        indices = []
        for row in rows:
            idx_item = self._table.item(row, 0)
            if idx_item:
                try:
                    indices.append(int(idx_item.text()))
                except ValueError:
                    pass
        if len(indices) < 2:
            return
        indices.sort()
        # 两两合并（从后往前避免索引变化）
        first = indices[0]
        for idx in reversed(indices[1:]):
            try:
                self._track.merge_items(first, idx)
            except (IndexError, ValueError):
                continue
        self.refresh_table()
        self._timeline.set_subtitle_track(self._track)
        self.select_row(first)

    def _delete_selected(self):
        if self._current_edit_index < 0:
            return
        try:
            self._track.remove_item(self._current_edit_index)
        except (IndexError, ValueError):
            return
        self._current_edit_index = -1
        self.refresh_table()
        self._timeline.set_subtitle_track(self._track)
        self._update_button_states()

    def _update_button_states(self):
        has_selection = self._current_edit_index >= 0
        self._btn_split.setEnabled(has_selection)
        self._btn_delete.setEnabled(has_selection)

        selected_rows = set(item.row() for item in self._table.selectedItems())
        self._btn_merge.setEnabled(len(selected_rows) >= 2)
        self._btn_export.setEnabled(self._track.count > 0)

    # ── 重新生成 / 导出 ──

    def _regenerate(self):
        try:
            output_dir = self._output_dir()
            sentences = []
            if self._project:
                sentences = self._project.sentences

            if not sentences and self._get_manuscript_text is not None:
                text = self._get_manuscript_text()
                if text:
                    from index_tts_gui.core.subtitler import _split_manuscript
                    sentences = _split_manuscript(text)

            if not sentences:
                from PySide6.QtWidgets import QMessageBox
                QMessageBox.warning(self, "错误", "没有可用句子，请先拆分文稿")
                return

            wavs = sorted([
                os.path.join(output_dir, f)
                for f in os.listdir(output_dir)
                if f.startswith("sentence_") and f.endswith(".wav")
            ])

            if not wavs:
                from PySide6.QtWidgets import QMessageBox
                QMessageBox.warning(self, "错误", f"{output_dir}/ 下无分句 WAV")
                return

            if len(sentences) != len(wavs):
                from PySide6.QtWidgets import QMessageBox
                QMessageBox.warning(
                    self, "错误",
                    f"句子数（{len(sentences)}）与音频数（{len(wavs)}）不一致"
                )
                return

            pauses = self._project.pauses if self._project else []
            if pauses and len(pauses) == len(sentences):
                entries = generate_srt_from_sentences_with_pauses(
                    sentences, wavs, pauses
                )
                self._log.appendPlainText(f"📐 使用工程保存的停顿重新生成字幕: {pauses}")
            else:
                entries = generate_srt_from_sentences("", sentences, wavs)
                self._log.appendPlainText(
                    "⚠ 未找到工程停顿，按无停顿生成字幕，可能与 full_dub.wav 对不齐"
                )

            self.load_entries(entries)
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.information(self, "完成", f"已重新生成 {len(entries)} 条字幕")
        except Exception as e:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "错误", str(e))

    def _export_srt(self):
        default_path = os.path.join(self._output_dir(), "full_dub.srt")
        path, _ = QFileDialog.getSaveFileName(
            self, "导出字幕", default_path, "SRT 文件 (*.srt)"
        )
        if path:
            srt = entries_to_srt(self._track.to_entries())
            with open(path, "w", encoding="utf-8") as f:
                f.write(srt)
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.information(
                self, "导出成功", f"已导出 {self._track.count} 条到 {path}"
            )
