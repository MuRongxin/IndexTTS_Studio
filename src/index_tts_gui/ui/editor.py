"""文稿编辑面板"""
import os
from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QPlainTextEdit,
    QPushButton, QLabel, QFileDialog, QMessageBox,
    QSplitter, QComboBox, QSpinBox, QFrame,
    QTableWidget, QTableWidgetItem, QStyledItemDelegate,
    QLineEdit, QAbstractItemView, QHeaderView,
)
from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QKeyEvent

from index_tts_gui.ui.split_worker import SplitWorker
from index_tts_gui.core.project import Project


PUNCT = '。！？，、；：'


class SentenceLineEdit(QLineEdit):
    """
    拆分结果单元格编辑器。

    - Enter（光标不在行首/行尾）：从光标处切开，后半部分作为新行
    - Backspace（光标在行首）：与上一行合并，必要时补标点
    """

    split_requested = Signal(int)      # 在 cursor_pos 处切分
    merge_requested = Signal()         # 与上一行合并

    def __init__(self, parent=None):
        super().__init__(parent)

    def keyPressEvent(self, event: QKeyEvent):
        key = event.key()
        modifiers = event.modifiers()
        pos = self.cursorPosition()
        length = len(self.text())

        if key in (Qt.Key_Return, Qt.Key_Enter) and modifiers == Qt.NoModifier:
            if 0 < pos < length:
                self.split_requested.emit(pos)
                return

        if key == Qt.Key_Backspace and modifiers == Qt.NoModifier:
            if pos == 0:
                self.merge_requested.emit()
                return

        super().keyPressEvent(event)


class SentenceDelegate(QStyledItemDelegate):
    """为拆分结果列提供自定义行编辑器。"""

    split_requested = Signal(int)   # cursor_pos
    merge_requested = Signal()      # 行号由 ManuscriptPanel 通过 currentItem 获取

    def createEditor(self, parent, option, index):
        editor = SentenceLineEdit(parent)
        editor.split_requested.connect(self._on_split)
        editor.merge_requested.connect(self._on_merge)
        return editor

    def _on_split(self, cursor_pos: int):
        editor = self.sender()
        if editor is None:
            return
        # 通过 editor 找到对应 QModelIndex
        # QStyledItemDelegate 没有直接方法，这里通过 focus 间接处理
        # 实际由 ManuscriptPanel 在 setEditorData 时绑定
        self.split_requested.emit(cursor_pos)

    def _on_merge(self):
        editor = self.sender()
        if editor is None:
            return
        self.merge_requested.emit()


class ManuscriptPanel(QWidget):
    """文稿编辑：左侧文本框 + 右侧句子预览表格"""

    text_changed = Signal(str)          # 原文变更时发射
    sentences_ready = Signal(list)      # 拆分完成时发射句子列表

    def __init__(self, project: Project):
        super().__init__()
        self._project = project
        self._sentences: list[str] = []
        self._llm_cfg: dict = {}
        self._worker: SplitWorker | None = None
        self._editing_row: int = -1
        self._setup_ui()
        self._load_from_project()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)

        # ── 顶部工具栏 ──
        toolbar = QFrame()
        toolbar.setStyleSheet("""
            QFrame {
                background: #fafafa;
                border: 1px solid #e0e0e0;
                border-radius: 6px;
            }
        """)
        tb_layout = QHBoxLayout(toolbar)
        tb_layout.setContentsMargins(10, 8, 10, 8)
        tb_layout.setSpacing(12)

        btn_open = QPushButton("📂 打开")
        btn_open.setToolTip("打开 .txt / .md")
        btn_open.clicked.connect(self._open_file)
        tb_layout.addWidget(btn_open)

        btn_save = QPushButton("💾 保存")
        btn_save.setToolTip("保存当前文稿")
        btn_save.clicked.connect(self._save_file)
        tb_layout.addWidget(btn_save)

        btn_clear = QPushButton("🗑 清空")
        btn_clear.setToolTip("清空编辑区")
        btn_clear.clicked.connect(self._clear_text)
        tb_layout.addWidget(btn_clear)

        tb_layout.addSpacing(16)

        tb_layout.addWidget(QLabel("模式:"))
        self._mode_combo = QComboBox()
        self._mode_combo.addItems(["LLM", "自动", "规则"])
        self._mode_combo.setToolTip(
            "LLM：大模型语义拆分\n"
            "自动：优先 LLM，失败回退规则\n"
            "规则：按标点 + 句长拆分"
        )
        tb_layout.addWidget(self._mode_combo)

        tb_layout.addWidget(QLabel("句长:"))
        self._max_len_spin = QSpinBox()
        self._max_len_spin.setRange(0, 200)
        self._max_len_spin.setValue(30)
        self._max_len_spin.setSuffix(" 字")
        self._max_len_spin.setToolTip("单句最大字数，0 表示不限制")
        tb_layout.addWidget(self._max_len_spin)

        tb_layout.addStretch()

        self._btn_split = QPushButton("🔍 拆分预览")
        self._btn_split.setToolTip("开始拆分文稿")
        self._btn_split.setStyleSheet("""
            QPushButton {
                background: #2979ff; color: white;
                padding: 6px 18px; border-radius: 4px;
                font-weight: bold;
            }
            QPushButton:hover { background: #1565c0; }
            QPushButton:disabled { background: #ccc; }
        """)
        self._btn_split.clicked.connect(self._do_split)
        tb_layout.addWidget(self._btn_split)

        layout.addWidget(toolbar)

        # ── 统计栏 ──
        stats = QFrame()
        stats.setStyleSheet("""
            QFrame { background: #f5f5f5; border-radius: 4px; }
            QLabel { color: #555; font-size: 12px; }
        """)
        stats_layout = QHBoxLayout(stats)
        stats_layout.setContentsMargins(10, 6, 10, 6)

        self._stats_total = QLabel("总字数: 0")
        self._stats_sentences = QLabel("已拆分: 0 句")
        self._status_label = QLabel("")
        self._status_label.setStyleSheet("color: #2979ff; font-weight: bold;")
        self._status_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        stats_layout.addWidget(self._stats_total)
        stats_layout.addWidget(self._stats_sentences)
        stats_layout.addStretch()
        stats_layout.addWidget(self._status_label)

        layout.addWidget(stats)

        # ── 主区域 ──
        splitter = QSplitter(Qt.Horizontal)

        # 左侧：编辑器
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(6)

        lbl_editor = QLabel("📝 文稿内容")
        lbl_editor.setStyleSheet("font-weight: bold; color: #333;")
        left_layout.addWidget(lbl_editor)

        self._editor = QPlainTextEdit()
        self._editor.setPlaceholderText("在此粘贴、打开或输入文稿…")
        self._editor.textChanged.connect(self._on_text_changed)
        self._editor.setStyleSheet("""
            QPlainTextEdit {
                border: 1px solid #ddd;
                border-radius: 6px;
                padding: 8px;
                font-size: 14px;
                line-height: 1.5;
            }
        """)
        left_layout.addWidget(self._editor, 1)

        splitter.addWidget(left)
        splitter.setStretchFactor(0, 1)

        # 右侧：拆分结果表格
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(6)

        lbl_result = QLabel("📋 拆分结果（可直接编辑）")
        lbl_result.setStyleSheet("font-weight: bold; color: #333;")
        right_layout.addWidget(lbl_result)

        self._table = QTableWidget()
        self._table.setColumnCount(2)
        self._table.setHorizontalHeaderLabels(["#", "句子"])
        self._table.horizontalHeader().setDefaultAlignment(Qt.AlignLeft)
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.verticalHeader().setVisible(False)
        self._table.setShowGrid(True)
        self._table.setStyleSheet("""
            QTableWidget {
                border: 1px solid #ddd;
                border-radius: 6px;
                gridline-color: #e0e0e0;
                font-size: 14px;
            }
            QTableWidget::item {
                padding: 6px;
                border-bottom: 1px solid #e0e0e0;
            }
            QTableWidget::item:selected {
                background: #e3f2fd;
                color: #000;
            }
        """)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._table.cellChanged.connect(self._on_cell_changed)

        # 自定义委托
        self._delegate = SentenceDelegate(self)
        self._delegate.split_requested.connect(self._split_at_cursor)
        self._delegate.merge_requested.connect(self._merge_with_previous)
        self._table.setItemDelegateForColumn(1, self._delegate)

        right_layout.addWidget(self._table, 1)

        splitter.addWidget(right)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([500, 500])
        splitter.setHandleWidth(8)

        layout.addWidget(splitter, 1)

        # 防抖定时器
        self._emit_timer = QTimer(self)
        self._emit_timer.setSingleShot(True)
        self._emit_timer.timeout.connect(self._emit_sentences)

    def set_llm_config(self, cfg: dict):
        """外部注入 LLM 配置。"""
        self._llm_cfg = cfg or {}

    def set_project(self, project: Project):
        """切换工程时刷新数据。"""
        # 切换期间临时断开保存型信号，避免加载旧数据时触发写入
        self._editor.blockSignals(True)
        self._table.blockSignals(True)
        try:
            self._project = project
            self._load_from_project()
        finally:
            self._editor.blockSignals(False)
            self._table.blockSignals(False)

    def _on_text_changed(self):
        text = self._editor.toPlainText()
        self._project.source_text = text
        self._project.save()
        self.text_changed.emit(text)
        self._update_stats()

    def _on_cell_changed(self, row: int, col: int):
        if col != 1 or row >= len(self._sentences):
            return
        item = self._table.item(row, col)
        if not item:
            return
        self._sentences[row] = item.text()
        self._save_sentences_to_project()
        self._update_stats()
        self._emit_timer.stop()
        self._emit_timer.start(300)

    def _emit_sentences(self):
        self.sentences_ready.emit(list(self._sentences))

    def _load_table(self, sentences: list[str]):
        """加载句子到表格，不触发 cellChanged。"""
        self._table.blockSignals(True)
        self._table.clearContents()
        self._table.setRowCount(len(sentences))
        for i, s in enumerate(sentences):
            idx_item = QTableWidgetItem(str(i + 1))
            idx_item.setFlags(idx_item.flags() & ~Qt.ItemIsEditable)
            idx_item.setTextAlignment(Qt.AlignCenter | Qt.AlignVCenter)
            self._table.setItem(i, 0, idx_item)

            text_item = QTableWidgetItem(s)
            self._table.setItem(i, 1, text_item)

        self._table.blockSignals(False)
        self._sentences = list(sentences)
        self._update_stats()
        self._table.viewport().update()

    def _load_from_project(self):
        """启动时从工程加载文稿和拆分结果。"""
        source = self._project.source_text
        sentences = self._project.sentences
        if source:
            self._editor.setPlainText(source)
        if sentences:
            self._load_table(sentences)
            self.sentences_ready.emit(self._sentences)
            self._status_label.setText(
                f"已加载工程「{self._project.name}」：{len(sentences)} 句"
            )
        else:
            self._status_label.setText(
                f"已加载工程「{self._project.name}」"
            )

    def _save_sentences_to_project(self):
        """把当前句子列表同步回工程。"""
        self._project.sentences = list(self._sentences)
        self._project.save()

    def _update_stats(self):
        text = self._editor.toPlainText()
        self._stats_total.setText(f"总字数: {len(text)}")
        self._stats_sentences.setText(f"已拆分: {len(self._sentences)} 句")

    def _clear_text(self):
        self._editor.clear()
        self._load_table([])
        self._project.source_text = ""
        self._save_sentences_to_project()
        self._emit_sentences()

    def _open_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "打开文稿", "", "文本文件 (*.txt *.md);;所有文件 (*)"
        )
        if path:
            try:
                with open(path, "r", encoding="utf-8") as f:
                    self._editor.setPlainText(f.read())
            except Exception as e:
                QMessageBox.warning(self, "错误", f"无法打开文件：{e}")

    def _save_file(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "保存文稿", "文稿.txt", "文本文件 (*.txt)"
        )
        if path:
            try:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(self._editor.toPlainText())
            except Exception as e:
                QMessageBox.warning(self, "错误", f"无法保存：{e}")

    def _do_split(self):
        text = self._editor.toPlainText()
        if not text.strip():
            self._status_label.setText("请先输入文稿")
            return

        mode_map = {"LLM": "llm", "自动": "auto", "规则": "rule"}
        mode = mode_map[self._mode_combo.currentText()]

        if mode == "llm":
            from index_tts_gui.core.llm_client import LLMClient
            if not LLMClient.is_configured(self._llm_cfg):
                QMessageBox.warning(
                    self, "未配置 LLM",
                    "请在左侧「设置」中配置 LLM API（MiMo 或 DeepSeek）。"
                )
                return

        self._btn_split.setEnabled(False)
        self._status_label.setText("正在拆分…")
        self._load_table([])

        self._worker = SplitWorker(
            text=text,
            mode=mode,
            llm_cfg=self._llm_cfg,
            max_length=self._max_len_spin.value(),
        )
        self._worker.finished.connect(self._on_split_finished)
        self._worker.start()

    def _on_split_finished(self, sentences: list, used_llm: bool, msg: str):
        self._btn_split.setEnabled(True)
        self._load_table(sentences)
        self._status_label.setText(msg)
        self._save_sentences_to_project()
        self.sentences_ready.emit(self._sentences)

    def _split_at_cursor(self, cursor_pos: int):
        """在当前编辑行的 cursor_pos 处切分。"""
        # 找到当前正在编辑的行
        current_item = self._table.currentItem()
        if current_item is None:
            return
        row = current_item.row()
        if row < 0 or row >= len(self._sentences):
            return

        text = self._sentences[row]
        if cursor_pos <= 0 or cursor_pos >= len(text):
            return

        before = text[:cursor_pos]
        after = text[cursor_pos:].lstrip(" \t")

        new_sentences = list(self._sentences)
        new_sentences[row] = before
        new_sentences.insert(row + 1, after)

        # 先关闭当前编辑器，避免旧编辑器与重新加载后的行错位
        self._table.setCurrentIndex(self._table.model().index(-1, -1))
        self._load_table(new_sentences)
        self._save_sentences_to_project()

        # 让新行进入编辑状态
        self._table.setCurrentCell(row + 1, 1)
        self._table.editItem(self._table.item(row + 1, 1))

        self._emit_sentences()

    def _merge_with_previous(self):
        """将当前行合并到上一行。"""
        current_item = self._table.currentItem()
        if current_item is None:
            return
        row = current_item.row()
        if row <= 0 or row >= len(self._sentences):
            return

        prev = self._sentences[row - 1]
        curr = self._sentences[row]

        # 当前行开头多余标点和空白去掉
        curr = curr.lstrip(" \t，。！？、；：")

        # 上一行末尾无标点时补上 "。"
        if prev and prev[-1] not in PUNCT:
            prev += '。'

        new_sentences = list(self._sentences)
        new_sentences[row - 1] = prev + curr
        new_sentences.pop(row)

        # 先关闭当前编辑器，避免旧编辑器与重新加载后的行错位
        self._table.setCurrentIndex(self._table.model().index(-1, -1))
        self._load_table(new_sentences)
        self._save_sentences_to_project()

        # 定位到合并后的行
        self._table.setCurrentCell(row - 1, 1)
        self._table.editItem(self._table.item(row - 1, 1))

        self._emit_sentences()

    def get_text(self) -> str:
        return self._editor.toPlainText()

    def set_text(self, text: str):
        self._editor.setPlainText(text)

    def get_sentences(self) -> list[str]:
        return list(self._sentences)
