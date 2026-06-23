"""
字幕编辑面板：表格视图 + 播放器 + 手动切分
"""
import os
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QLabel, QSlider, QFileDialog, QMessageBox,
    QSplitter, QAbstractItemView,
)
from PySide6.QtCore import Qt, Signal, QUrl
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput

from index_tts_gui.core.subtitler import (
    SubtitleEntry, generate_srt, entries_to_srt,
)
from index_tts_gui.core.merger import get_wav_duration


class SubtitlePanel(QWidget):
    """字幕编辑 + 音频播放"""

    def __init__(self):
        super().__init__()
        self._entries: list[SubtitleEntry] = []
        self._audio_path: str = ""
        self._get_manuscript_text: callable | None = None
        self._player = QMediaPlayer()
        self._audio_output = QAudioOutput()
        self._player.setAudioOutput(self._audio_output)
        self._player.positionChanged.connect(self._on_position_changed)

        self._setup_ui()

    # ── UI ──

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        # 音频加载 + 播放控制
        top = QHBoxLayout()

        self._btn_load_audio = QPushButton("📂 加载 full_dub.wav")
        self._btn_load_audio.clicked.connect(self._load_audio)
        top.addWidget(self._btn_load_audio)

        self._btn_play = QPushButton("▶ 播放")
        self._btn_play.setEnabled(False)
        self._btn_play.clicked.connect(self._toggle_play)
        top.addWidget(self._btn_play)

        self._time_label = QLabel("00:00 / 00:00")
        top.addWidget(self._time_label)

        # 进度条
        self._seek = QSlider(Qt.Horizontal)
        self._seek.setEnabled(False)
        self._seek.sliderMoved.connect(self._seek_to)
        top.addWidget(self._seek, 1)

        layout.addLayout(top)

        # 字幕表格
        self._table = QTableWidget()
        self._table.setColumnCount(4)
        self._table.setHorizontalHeaderLabels(["#", "开始", "结束", "文本"])
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setAlternatingRowColors(True)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeToContents
        )
        self._table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeToContents
        )
        self._table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeToContents
        )
        self._table.cellChanged.connect(self._on_cell_changed)
        self._table.itemSelectionChanged.connect(self._on_selection_changed)
        layout.addWidget(self._table)

        # 按钮行
        btn_row = QHBoxLayout()

        self._btn_split = QPushButton("✂ 切分选中行")
        self._btn_split.setToolTip("将选中的字幕行从中间切成两条")
        self._btn_split.setEnabled(False)
        self._btn_split.clicked.connect(self._split_selected)
        btn_row.addWidget(self._btn_split)

        btn_row.addStretch()

        self._btn_regenerate = QPushButton("🔄 重新生成字幕")
        self._btn_regenerate.setToolTip("从 output_tts/ 分句 WAV 重新分析生成")
        self._btn_regenerate.clicked.connect(self._regenerate)
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
        self._btn_export.clicked.connect(self._export_srt)
        btn_row.addWidget(self._btn_export)

        layout.addLayout(btn_row)

    # ── 音频 ──

    def _load_audio(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "加载完整音频", "", "WAV 文件 (*.wav)"
        )
        if path:
            self._audio_path = path
            self._player.setSource(QUrl.fromLocalFile(path))
            self._btn_play.setEnabled(True)
            self._seek.setEnabled(True)
            self._btn_load_audio.setText(f"📂 {os.path.basename(path)}")

    def _toggle_play(self):
        if self._player.playbackState() == QMediaPlayer.PlayingState:
            self._player.pause()
            self._btn_play.setText("▶ 播放")
        else:
            self._player.play()
            self._btn_play.setText("⏸ 暂停")

    def _seek_to(self, pos: int):
        self._player.setPosition(pos)

    def _on_position_changed(self, pos_ms: int):
        dur = self._player.duration()
        if dur > 0:
            self._seek.blockSignals(True)
            self._seek.setValue(pos_ms)
            self._seek.blockSignals(False)

            pos_s = pos_ms / 1000.0
            dur_s = dur / 1000.0
            self._time_label.setText(
                f"{_fmt(pos_s)} / {_fmt(dur_s)}"
            )

            # 高亮当前字幕行
            self._highlight_current(pos_s)

    def _highlight_current(self, pos_s: float):
        for row in range(self._table.rowCount()):
            start = _parse_time(self._table.item(row, 1).text())
            end = _parse_time(self._table.item(row, 2).text())
            if start <= pos_s <= end:
                self._table.selectRow(row)
                break

    # ── 字幕表格 ──

    def set_manuscript_source(self, get_text: callable):
        """设置获取当前文稿内容的回调。"""
        self._get_manuscript_text = get_text

    def load_entries(self, entries: list[SubtitleEntry]):
        """载入字幕条目到表格"""
        self._entries = entries
        self._table.blockSignals(True)
        self._table.setRowCount(0)

        for e in entries:
            row = self._table.rowCount()
            self._table.insertRow(row)

            self._table.setItem(row, 0, QTableWidgetItem(str(e.index)))
            self._table.setItem(row, 1, QTableWidgetItem(_fmt(e.start_sec)))
            self._table.setItem(row, 2, QTableWidgetItem(_fmt(e.end_sec)))
            self._table.setItem(row, 3, QTableWidgetItem(e.text))

        self._table.blockSignals(False)
        self._btn_export.setEnabled(True)

    def _on_cell_changed(self, row: int, col: int):
        """单元格编辑后同步回 entries"""
        if row >= len(self._entries):
            return
        e = self._entries[row]
        item = self._table.item(row, col)
        if not item:
            return

        if col == 1:
            e.start_sec = _parse_time(item.text())
        elif col == 2:
            e.end_sec = _parse_time(item.text())
        elif col == 3:
            e.text = item.text()

    def _on_selection_changed(self):
        self._btn_split.setEnabled(
            len(self._table.selectedItems()) > 0
        )

    def _split_selected(self):
        """将选中的字幕行从中间切分为两条"""
        rows = set()
        for item in self._table.selectedItems():
            rows.add(item.row())

        if not rows:
            return

        # 从后往前处理，避免索引偏移
        for row in sorted(rows, reverse=True):
            if row >= len(self._entries):
                continue

            e = self._entries[row]
            text = e.text
            if len(text) < 6:
                continue  # 太短不切

            # 在中间偏后的标点处切开
            mid = len(text) // 2
            best = mid
            for ch in ['，', '、', '。', '；', '：', '！', '？']:
                pos = text.rfind(ch, mid, min(len(text), mid + 10))
                if pos > best - 5:
                    best = pos + 1
                    break
            if best == mid:
                continue  # 找不到合适断点

            part1 = text[:best]
            part2 = text[best:].lstrip('，、；：。！？')

            mid_time = e.start_sec + (e.end_sec - e.start_sec) * (len(part1) / len(text))
            orig_end = e.end_sec

            # 修改当前行
            e.text = part1
            e.end_sec = round(mid_time, 3)

            # 插入新行
            new_entry = SubtitleEntry(0, round(mid_time, 3), orig_end, part2)
            self._entries.insert(row + 1, new_entry)

        # 重新编号 + 刷新
        for i, e in enumerate(self._entries):
            e.index = i + 1
        self.load_entries(self._entries)

    def _regenerate(self):
        """从 output_tts 重新生成字幕"""
        try:
            output_dir = "output_tts"
            text_path = "文稿.txt"

            # 优先使用当前文稿面板内容
            text = ""
            if self._get_manuscript_text is not None:
                text = self._get_manuscript_text()

            if not text and os.path.exists(text_path):
                with open(text_path, "r", encoding="utf-8") as f:
                    text = f.read()

            if not text:
                QMessageBox.warning(self, "错误", "文稿内容为空，请先导入或粘贴文稿")
                return

            # 找 wav 文件
            wavs = sorted([
                os.path.join(output_dir, f)
                for f in os.listdir(output_dir)
                if f.startswith("sentence_") and f.endswith(".wav")
            ])

            if not wavs:
                QMessageBox.warning(self, "错误", f"{output_dir}/ 下无分句 WAV")
                return

            entries = generate_srt("", text, wavs)
            self.load_entries(entries)

            QMessageBox.information(
                self, "完成", f"已重新生成 {len(entries)} 条字幕"
            )
        except Exception as e:
            QMessageBox.warning(self, "错误", str(e))

    def _export_srt(self):
        """导出 SRT 文件"""
        path, _ = QFileDialog.getSaveFileName(
            self, "导出字幕", "full_dub.srt", "SRT 文件 (*.srt)"
        )
        if path:
            srt = entries_to_srt(self._entries)
            with open(path, "w", encoding="utf-8") as f:
                f.write(srt)
            QMessageBox.information(
                self, "导出成功", f"已导出 {len(self._entries)} 条到 {path}"
            )


# ── 工具函数 ──

def _fmt(sec: float) -> str:
    """秒 → SRT 标准 hh:mm:ss,mmm"""
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = int(sec % 60)
    ms = int((sec - int(sec)) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _parse_time(text: str) -> float:
    """解析 mm:ss.mmm 或 hh:mm:ss,mmm → 秒"""
    text = text.strip().replace(",", ".")
    parts = text.split(":")
    if len(parts) == 2:
        return int(parts[0]) * 60 + float(parts[1])
    elif len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    return 0.0
