"""文稿编辑面板"""
import logging
import os
from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QPlainTextEdit, QTextEdit,
    QPushButton, QLabel, QFileDialog, QMessageBox,
    QSplitter, QComboBox, QSpinBox, QFrame, QApplication,
    QTableWidget, QTableWidgetItem, QStyledItemDelegate,
    QLineEdit, QAbstractItemView, QHeaderView,
)
from PySide6.QtCore import Qt, Signal, QTimer, QRect
from PySide6.QtGui import (
    QColor, QKeyEvent, QKeySequence, QShortcut, QTextCharFormat, QTextCursor,
)

from index_tts_gui.ui.split_worker import SplitWorker
from index_tts_gui.ui.find_replace_bar import (
    FindReplaceBar,
    SCOPE_ALL,
    SCOPE_SOURCE,
    SCOPE_SENTENCES,
)
from index_tts_gui.core.project import Project
from index_tts_gui.core.text_search import find_matches, replace_all


logger = logging.getLogger("index_tts")

PUNCT = '。！？，、；：'

# 查找高亮：原文用 ExtraSelection 的背景色，分片表格用半透明覆盖色
MAX_FIND_HIGHLIGHTS = 500          # 原文最多绘制的匹配数，防止超长文稿卡顿
FIND_MATCH_COLOR = QColor("#fff59d")        # 所有匹配（淡黄）
FIND_CURRENT_COLOR = QColor("#ffb300")      # 当前匹配（橙）
FIND_MATCH_OVERLAY = QColor(255, 245, 157, 130)
FIND_CURRENT_OVERLAY = QColor(255, 179, 0, 175)


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
    """为拆分结果列提供自定义行编辑器，并绘制查找命中的高亮。"""

    split_requested = Signal(int)   # cursor_pos
    merge_requested = Signal()      # 行号由 ManuscriptPanel 通过 currentItem 获取

    # 与表格样式里 QTableWidget::item 的 padding 保持一致（高亮定位需要）
    _ITEM_PADDING = 6
    _MAX_HIGHLIGHTS_PER_CELL = 50

    def __init__(self, parent=None):
        super().__init__(parent)
        self._search_term = ""
        self._case_sensitive = False
        self._current = None  # 当前匹配：(row, start, end)；None 表示不强调

    def set_search(self, term: str, case_sensitive: bool, current=None):
        """设置查找高亮状态；term 为空表示清除高亮。"""
        self._search_term = term or ""
        self._case_sensitive = case_sensitive
        self._current = current

    def paint(self, painter, option, index):
        # 先按默认样式绘制背景、选中态与文字，再叠加半透明高亮。
        # 半透明叠加不会遮住文字，比重新实现整段 item 绘制更稳妥。
        super().paint(painter, option, index)
        if index.column() != 1 or not self._search_term:
            return
        text = index.data(Qt.DisplayRole)
        if not isinstance(text, str) or not text:
            return
        spans = find_matches(text, self._search_term, self._case_sensitive)
        if not spans:
            return
        spans = spans[:self._MAX_HIGHLIGHTS_PER_CELL]

        metrics = option.fontMetrics
        top = option.rect.y() + max(0, (option.rect.height() - metrics.height()) // 2) - 1
        left = option.rect.x() + self._ITEM_PADDING
        height = metrics.height() + 2
        painter.save()
        try:
            for start, end in spans:
                x1 = left + metrics.horizontalAdvance(text[:start])
                x2 = left + metrics.horizontalAdvance(text[:end])
                is_current = (
                    self._current is not None
                    and self._current[0] == index.row()
                    and self._current[1] == start
                    and self._current[2] == end
                )
                color = FIND_CURRENT_OVERLAY if is_current else FIND_MATCH_OVERLAY
                painter.fillRect(QRect(x1, top, max(1, x2 - x1), height), color)
        finally:
            painter.restore()

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
        self._split_canceled: bool = False
        # 查找/替换状态
        self._find_matches: list[tuple[str, int, int, int]] = []
        self._find_index: int = -1
        self._find_suppress_refresh: bool = False
        self._find_focus_back: QWidget | None = None
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

        btn_find = QPushButton("🔎 查找")
        btn_find.setToolTip("在原文和分片句子中查找/替换（Ctrl+F / Ctrl+H）")
        btn_find.clicked.connect(lambda: self._open_find(replace=True))
        tb_layout.addWidget(btn_find)

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

        self._btn_stop_split = QPushButton("⏹ 停止")
        self._btn_stop_split.setToolTip("停止正在进行的拆分")
        self._btn_stop_split.setEnabled(False)
        self._btn_stop_split.setStyleSheet("""
            QPushButton {
                background: #d32f2f; color: white;
                padding: 6px 18px; border-radius: 4px;
                font-weight: bold;
            }
            QPushButton:hover { background: #b71c1c; }
            QPushButton:disabled { background: #ccc; }
        """)
        self._btn_stop_split.clicked.connect(self._stop_split)
        tb_layout.addWidget(self._btn_stop_split)

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

        # ── 查找/替换工具条（默认隐藏，Ctrl+F / Ctrl+H 或工具栏按钮唤起）──
        self._find_bar = FindReplaceBar(self)
        self._find_bar.search_changed.connect(self._on_find_search_changed)
        self._find_bar.scope_changed.connect(self._on_find_scope_changed)
        self._find_bar.next_requested.connect(self._find_next)
        self._find_bar.prev_requested.connect(self._find_prev)
        self._find_bar.replace_requested.connect(self._replace_current)
        self._find_bar.replace_all_requested.connect(self._replace_all)
        self._find_bar.closed.connect(self._on_find_closed)
        layout.addWidget(self._find_bar)

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

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.timeout.connect(self._save_text_to_project)

        # 查找/替换快捷键：仅当焦点在文稿面板（编辑器/表格/查找条）内时生效
        self._shortcut_find = QShortcut(QKeySequence(QKeySequence.Find), self)
        self._shortcut_find.setContext(Qt.WidgetWithChildrenShortcut)
        self._shortcut_find.activated.connect(lambda: self._open_find(replace=False))

        self._shortcut_replace = QShortcut(QKeySequence("Ctrl+H"), self)
        self._shortcut_replace.setContext(Qt.WidgetWithChildrenShortcut)
        self._shortcut_replace.activated.connect(lambda: self._open_find(replace=True))

    def set_llm_config(self, cfg: dict):
        """外部注入 LLM 配置。"""
        self._llm_cfg = cfg or {}
        self._update_mode_availability()

    def _update_mode_availability(self):
        """按 llm.enabled 启用/禁用 LLM 相关拆分模式。"""
        enabled = self._llm_cfg.get("enabled", True)
        model = self._mode_combo.model()
        for text in ("LLM", "自动"):
            idx = self._mode_combo.findText(text)
            if idx >= 0:
                model.item(idx).setEnabled(enabled)
        if not enabled and self._mode_combo.currentText() in ("LLM", "自动"):
            self._mode_combo.setCurrentText("规则")

    def set_project(self, project: Project):
        """切换工程时刷新数据。"""
        # 切换工程前关闭查找条，避免高亮/匹配残留在旧工程数据上
        self._close_find_bar()
        # 取消运行中的拆分：结果里带有旧工程文稿，不得写入新工程
        if self._worker is not None:
            # 先断开数据信号再取消：取消点之后仍会发射的 chunk 会被追加进
            # 新工程的句子列表，仅靠 _on_split_finished 的工程校验拦不住
            for signal_name in ("progress", "chunk_ready", "result_ready"):
                try:
                    getattr(self._worker, signal_name).disconnect()
                except Exception:
                    pass
            # 生命周期清理挂在 QThread 内置 finished 上，保持连接
            if self._worker.isRunning():
                self._worker.cancel()
        # 切换期间临时断开保存型信号，避免加载旧数据时触发写入
        self._editor.blockSignals(True)
        self._table.blockSignals(True)
        try:
            self._project = project
            self._load_from_project()
        finally:
            self._editor.blockSignals(False)
            self._table.blockSignals(False)

    def reset_for_new_project(self):
        """新建工程时清空面板状态。"""
        self._close_find_bar()
        self._split_canceled = False
        self._btn_split.setEnabled(True)
        self._btn_stop_split.setEnabled(False)
        self._mode_combo.setEnabled(True)
        self._max_len_spin.setEnabled(True)
        self._editor.blockSignals(True)
        self._table.blockSignals(True)
        try:
            self._editor.clear()
            self._load_table([])
            self._status_label.setText(f"新建工程「{self._project.name}」")
            self._update_stats()
        finally:
            self._editor.blockSignals(False)
            self._table.blockSignals(False)

    def _on_text_changed(self):
        text = self._editor.toPlainText()
        self._project.source_text = text
        self.text_changed.emit(text)
        self._update_stats()
        self._save_timer.stop()
        self._save_timer.start(300)
        if (
            not self._find_suppress_refresh
            and hasattr(self, "_find_bar")
            and self._find_bar.isVisible()
        ):
            # 手动编辑后保持高亮与计数同步（不改变当前匹配、不滚动）
            self._refresh_find(reset=False, navigate=False)

    def _save_text_to_project(self):
        try:
            self._project.save()
        except Exception as e:
            logger.error("自动保存工程失败: %s", e)

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
        if (
            not self._find_suppress_refresh
            and hasattr(self, "_find_bar")
            and self._find_bar.isVisible()
        ):
            self._refresh_find(reset=False, navigate=False)

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
        if (
            not self._find_suppress_refresh
            and hasattr(self, "_find_bar")
            and self._find_bar.isVisible()
        ):
            self._refresh_find(reset=False, navigate=False)

    def _load_from_project(self):
        """启动时从工程加载文稿和拆分结果（空工程清空，不残留上一工程）。"""
        # 切换/加载工程即视为上一次拆分作废，避免残留的取消标记影响新工程
        self._split_canceled = False
        source = self._project.source_text
        sentences = self._project.sentences
        self._editor.setPlainText(source or "")
        if sentences:
            self._load_table(sentences)
            self.sentences_ready.emit(self._sentences)
            self._status_label.setText(
                f"已加载工程「{self._project.name}」：{len(sentences)} 句"
            )
        else:
            self._load_table([])
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

        if self._worker is not None and self._worker.isRunning():
            self._status_label.setText("拆分进行中，请稍候")
            return

        mode_map = {"LLM": "llm", "自动": "auto", "规则": "rule"}
        mode = mode_map[self._mode_combo.currentText()]

        if mode == "llm":
            from index_tts_gui.core.llm_service import LLMService
            if not LLMService(self._llm_cfg).is_configured():
                QMessageBox.warning(
                    self, "未配置 LLM",
                    "请在左侧「设置」中配置 LLM API（MiMo 或 DeepSeek）。"
                )
                return

        self._split_canceled = False
        self._btn_split.setEnabled(False)
        self._btn_stop_split.setEnabled(True)
        self._mode_combo.setEnabled(False)
        self._max_len_spin.setEnabled(False)
        self._status_label.setText("正在拆分…")
        # 清空表格，等待增量结果逐块追加
        self._load_table([])

        if self._worker is not None:
            try:
                self._worker.progress.disconnect()
            except Exception:
                pass
            try:
                self._worker.chunk_ready.disconnect()
            except Exception:
                pass
            try:
                self._worker.result_ready.disconnect()
            except Exception:
                pass
            try:
                self._worker.finished.disconnect()
            except Exception:
                pass
            self._worker.deleteLater()
            self._worker = None

        self._worker = SplitWorker(
            text=text,
            mode=mode,
            llm_cfg=self._llm_cfg,
            max_length=self._max_len_spin.value(),
        )
        self._worker.setProperty("project_dir", self._project.project_dir)
        self._worker.progress.connect(self._on_split_progress)
        self._worker.chunk_ready.connect(self._on_chunk_ready)
        self._worker.result_ready.connect(self._on_split_finished)
        # 生命周期清理挂在 QThread 内置 finished 上：无论正常完成还是
        # 用户取消（result_ready 不发射），线程退出时都会触发，保证按钮状态恢复
        self._worker.finished.connect(self._on_worker_lifetime_finished)
        self._worker.start()

    def _stop_split(self):
        """用户点击「停止」：请求取消拆分。"""
        if self._worker is None or not self._worker.isRunning():
            return
        self._split_canceled = True
        self._worker.cancel()
        self._btn_stop_split.setEnabled(False)
        self._status_label.setText("正在停止…")

    def _on_split_progress(self, current: int, total: int, message: str):
        self._status_label.setText(f"{message} ({current}/{total})")

    def _on_chunk_ready(self, sentences: list[str]):
        """单块拆分完成：把该块的句子追加到表格末尾（增量显示）。"""
        if not sentences:
            return
        # 工程已切换时丢弃旧文稿的增量结果（与 _on_split_finished 的校验一致）
        sender = self.sender()
        expected = sender.property("project_dir") if sender is not None else ""
        if expected and expected != self._project.project_dir:
            return
        self._table.blockSignals(True)
        try:
            base = self._table.rowCount()
            self._table.setRowCount(base + len(sentences))
            for i, s in enumerate(sentences):
                row = base + i
                idx_item = QTableWidgetItem(str(row + 1))
                idx_item.setFlags(idx_item.flags() & ~Qt.ItemIsEditable)
                idx_item.setTextAlignment(Qt.AlignCenter | Qt.AlignVCenter)
                self._table.setItem(row, 0, idx_item)

                text_item = QTableWidgetItem(s)
                self._table.setItem(row, 1, text_item)
            self._sentences.extend(sentences)
        finally:
            self._table.blockSignals(False)
        self._update_stats()
        # 滚动到最新行，让用户看到"拆完一点显示一点"
        self._table.scrollToBottom()
        if (
            not self._find_suppress_refresh
            and hasattr(self, "_find_bar")
            and self._find_bar.isVisible()
        ):
            self._refresh_find(reset=False, navigate=False)

    def _on_split_finished(self, sentences: list, used_llm: bool, msg: str):
        self._btn_split.setEnabled(True)
        self._btn_stop_split.setEnabled(False)
        self._mode_combo.setEnabled(True)
        self._max_len_spin.setEnabled(True)
        # 工程已切换时丢弃旧文稿的拆分结果
        sender = self.sender()
        expected = sender.property("project_dir") if sender is not None else ""
        if expected and expected != self._project.project_dir:
            logger.warning("工程已切换，丢弃旧拆分结果")
            return
        if self._split_canceled:
            # 取消后不应用任何结果（表格保留已增量显示的部分）
            return
        if not sentences:
            # 拆分失败：保留已有句子，不用空结果覆盖工程数据
            self._status_label.setText(msg)
            QMessageBox.warning(self, "拆分失败", msg)
            return
        # 增量显示已逐块把句子追加进表格；最终用权威结果做一次同步
        # （数量一致则不重载，避免闪烁；不一致时全量刷新保证正确）
        if len(self._sentences) != len(sentences):
            self._load_table(sentences)
        else:
            self._sentences = list(sentences)
        self._status_label.setText(msg)
        self._save_sentences_to_project()
        self.sentences_ready.emit(self._sentences)
        if (
            not self._find_suppress_refresh
            and hasattr(self, "_find_bar")
            and self._find_bar.isVisible()
        ):
            self._refresh_find(reset=False, navigate=False)

    def _on_worker_lifetime_finished(self):
        """SplitWorker 生命周期结束，安全清理引用并恢复按钮状态。

        挂在 QThread 内置 finished 上：正常完成与用户取消都会触发。
        """
        if self._worker is not None:
            self._worker.deleteLater()
            self._worker = None
        self._btn_split.setEnabled(True)
        self._btn_stop_split.setEnabled(False)
        self._mode_combo.setEnabled(True)
        self._max_len_spin.setEnabled(True)
        if self._split_canceled:
            self._status_label.setText("已停止")
            self._split_canceled = False

    def cancel_workers(self):
        """取消运行中的拆分任务（应用退出时由主窗口调用）。"""
        if self._worker is not None and self._worker.isRunning():
            try:
                self._worker.disconnect()
            except Exception:
                pass
            self._worker.cancel()
            self._worker.wait(2000)
            if self._worker.isRunning():
                self._worker.terminate()
                self._worker.wait(1000)

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

    # ── 查找 / 替换（同时作用于原文与分片句子）──

    def _open_find(self, replace: bool = False):
        """唤起查找/替换工具条（Ctrl+F / Ctrl+H / 工具栏按钮）。"""
        focus = QApplication.focusWidget()
        self._find_focus_back = focus if focus is not None else self._editor

        initial = ""
        preferred: tuple[str, int, int] | None = None  # (类型, 起始位置, 行号)
        if self._editor.hasFocus():
            # 与常见编辑器一致：编辑器中有选中文本时直接作为查找词
            cursor = self._editor.textCursor()
            selected = cursor.selectedText()
            if (
                selected
                and "\n" not in selected
                and "\r" not in selected
                and "\u2029" not in selected  # QTextDocument 的段落分隔符
            ):
                initial = selected
                preferred = ("source", cursor.selectionStart(), -1)
        elif self._table.hasFocus() and not self._find_bar.term:
            # 在分片表格里选中整句时，用整句作为查找词
            item = self._table.currentItem()
            if item is not None and item.column() == 1:
                initial = item.text()
                preferred = ("sentence", -1, item.row())

        self._find_bar.activate(replace=replace, initial_term=initial)
        self._refresh_find(reset=False, navigate=False)
        if preferred is not None:
            self._select_preferred_match(*preferred)
        self._find_bar.focus_search()

    def _select_preferred_match(self, kind: str, start: int, row: int) -> bool:
        """把当前匹配切到与打开查找条前的选区对应的那一条。"""
        for index, (m_kind, m_start, _m_end, m_row) in enumerate(self._find_matches):
            if m_kind != kind or m_row != row:
                continue
            if start >= 0 and m_start != start:
                continue
            self._find_index = index
            self._apply_find_highlights()
            self._update_find_info()
            self._navigate_to_find_current()
            return True
        return False

    def _on_find_search_changed(self, term: str = "", case_sensitive: bool = False):
        """查找词或「区分大小写」变化：重新匹配并跳到第一个。"""
        self._refresh_find(reset=True, navigate=True)

    def _on_find_scope_changed(self, scope: str = SCOPE_ALL):
        """查找范围变化：重新匹配并跳到第一个。"""
        self._refresh_find(reset=True, navigate=True)

    def _on_find_closed(self):
        """工具条关闭：清除高亮并把焦点还给打开前的控件。"""
        self._clear_find_highlights()
        target = self._find_focus_back
        self._find_focus_back = None
        try:
            if target is not None and target.isVisible():
                target.setFocus()
                return
        except RuntimeError:  # 目标控件可能已随面板销毁
            pass
        self._editor.setFocus()

    def _collect_find_matches(self) -> list[tuple[str, int, int, int]]:
        """按当前查找词/范围收集匹配：(类型, start, end, 表格行号)。

        顺序：原文从上到下，然后分片句子按行、行内从左到右。
        """
        term = self._find_bar.term
        if not term:
            return []
        case_sensitive = self._find_bar.case_sensitive
        scope = self._find_bar.scope
        matches: list[tuple[str, int, int, int]] = []
        if scope in (SCOPE_ALL, SCOPE_SOURCE):
            for start, end in find_matches(
                self._editor.toPlainText(), term, case_sensitive
            ):
                matches.append(("source", start, end, -1))
        if scope in (SCOPE_ALL, SCOPE_SENTENCES):
            for row, sentence in enumerate(self._sentences):
                for start, end in find_matches(sentence, term, case_sensitive):
                    matches.append(("sentence", start, end, row))
        return matches

    def _refresh_find(self, reset: bool = False, navigate: bool = False):
        """重新收集匹配并刷新高亮/计数；navigate=True 时滚动到当前匹配。"""
        if not hasattr(self, "_find_bar") or not self._find_bar.isVisible():
            return
        if reset:
            self._find_index = 0
        self._find_matches = self._collect_find_matches()
        if not self._find_matches:
            self._find_index = -1
        elif self._find_index < 0 or self._find_index >= len(self._find_matches):
            self._find_index = 0
        self._apply_find_highlights()
        self._update_find_info()
        if navigate:
            self._navigate_to_find_current()

    def _apply_find_highlights(self):
        """刷新原文的 ExtraSelection 高亮和分片表格的委托高亮。"""
        if not hasattr(self, "_find_bar"):
            return
        term = self._find_bar.term
        current_source: tuple[int, int] | None = None
        current_sentence: tuple[int, int, int] | None = None
        if term and 0 <= self._find_index < len(self._find_matches):
            kind, start, end, row = self._find_matches[self._find_index]
            if kind == "source":
                current_source = (start, end)
            else:
                current_sentence = (row, start, end)

        selections: list[QTextEdit.ExtraSelection] = []
        if term:
            for kind, start, end, _row in self._find_matches:
                if kind != "source":
                    continue
                if len(selections) >= MAX_FIND_HIGHLIGHTS:
                    break
                cursor = QTextCursor(self._editor.document())
                cursor.setPosition(start)
                cursor.setPosition(end, QTextCursor.KeepAnchor)
                selection = QTextEdit.ExtraSelection()
                selection.cursor = cursor
                fmt = QTextCharFormat()
                fmt.setBackground(
                    FIND_CURRENT_COLOR
                    if current_source == (start, end)
                    else FIND_MATCH_COLOR
                )
                selection.format = fmt
                selections.append(selection)
        self._editor.setExtraSelections(selections)

        self._delegate.set_search(
            term, self._find_bar.case_sensitive, current_sentence
        )
        self._table.viewport().update()

    def _update_find_info(self):
        """更新工具条右侧的「当前/总数 + 来源构成」提示。"""
        if not hasattr(self, "_find_bar"):
            return
        term = self._find_bar.term
        if not term:
            self._find_bar.set_info("")
            return
        total = len(self._find_matches)
        if total == 0:
            self._find_bar.set_info("无匹配")
            return
        source_count = sum(
            1 for kind, _s, _e, _r in self._find_matches if kind == "source"
        )
        position = self._find_index + 1 if self._find_index >= 0 else 0
        self._find_bar.set_info(
            f"{position}/{total}",
            f"原文 {source_count} 处 · 分片句子 {total - source_count} 处",
        )

    def _navigate_to_find_current(self):
        """选中并滚动到当前匹配（不抢走查找框焦点）。"""
        if not (0 <= self._find_index < len(self._find_matches)):
            return
        kind, start, end, row = self._find_matches[self._find_index]
        if kind == "source":
            cursor = QTextCursor(self._editor.document())
            cursor.setPosition(start)
            cursor.setPosition(end, QTextCursor.KeepAnchor)
            self._editor.setTextCursor(cursor)
            self._editor.ensureCursorVisible()
            # 清掉表格里残留的蓝色选中行，避免误以为当前匹配在表格中
            self._table.clearSelection()
        elif 0 <= row < self._table.rowCount():
            item = self._table.item(row, 1)
            if item is not None:
                self._table.setCurrentCell(row, 1)
                self._table.scrollToItem(item, QAbstractItemView.PositionAtCenter)
                # 清掉编辑器里残留的原生选区（当前匹配在表格中）
                cursor = self._editor.textCursor()
                if cursor.hasSelection():
                    cursor.clearSelection()
                    self._editor.setTextCursor(cursor)

    def _find_next(self):
        self._step_find(1)

    def _find_prev(self):
        self._step_find(-1)

    def _step_find(self, step: int):
        """在当前匹配基础上前后移动一条（首尾循环）。"""
        if not self._find_bar.isVisible():
            return
        self._find_matches = self._collect_find_matches()
        if not self._find_matches:
            self._find_index = -1
            self._apply_find_highlights()
            self._update_find_info()
            return
        if self._find_index < 0:
            self._find_index = 0 if step > 0 else len(self._find_matches) - 1
        else:
            self._find_index = (self._find_index + step) % len(self._find_matches)
        self._apply_find_highlights()
        self._update_find_info()
        self._navigate_to_find_current()

    def _set_sentence_text(self, row: int, text: str):
        """更新某行分片句子：绕过 cellChanged，手动落盘并广播。"""
        if row < 0 or row >= len(self._sentences):
            return
        self._sentences[row] = text
        self._table.blockSignals(True)
        try:
            item = self._table.item(row, 1)
            if item is not None:
                item.setText(text)
        finally:
            self._table.blockSignals(False)
        self._save_sentences_to_project()
        self._update_stats()
        self._emit_sentences()

    def _replace_current(self):
        """替换当前匹配，并跳到下一个匹配（末尾则回到开头）。"""
        if not self._find_bar.isVisible():
            return
        term = self._find_bar.term
        if not term:
            self._find_bar.focus_search()
            return
        if not self._find_matches:
            self._refresh_find(reset=True, navigate=True)
            if not self._find_matches:
                self._find_bar.set_info("无匹配")
                return
        if not (0 <= self._find_index < len(self._find_matches)):
            self._find_index = 0

        kind, start, end, row = self._find_matches[self._find_index]
        keep_index = self._find_index
        replacement = self._find_bar.replacement

        self._find_suppress_refresh = True
        try:
            if kind == "source":
                cursor = QTextCursor(self._editor.document())
                cursor.setPosition(start)
                cursor.setPosition(end, QTextCursor.KeepAnchor)
                cursor.insertText(replacement)  # 触发 _on_text_changed 落盘
            else:
                text = self._sentences[row] if row < len(self._sentences) else ""
                self._set_sentence_text(row, text[:start] + replacement + text[end:])
        finally:
            self._find_suppress_refresh = False

        # 被替换的那条已消失，原下标恰好指向"下一个"；末尾则回到开头
        self._find_matches = self._collect_find_matches()
        if not self._find_matches:
            self._find_index = -1
        elif keep_index < len(self._find_matches):
            self._find_index = keep_index
        else:
            self._find_index = 0
        self._apply_find_highlights()
        self._update_find_info()
        self._navigate_to_find_current()

    def _replace_all(self):
        """替换当前范围内所有匹配（原文一次撤销块，分片逐行更新）。"""
        if not self._find_bar.isVisible():
            return
        term = self._find_bar.term
        if not term:
            self._find_bar.focus_search()
            return
        replacement = self._find_bar.replacement
        case_sensitive = self._find_bar.case_sensitive
        scope = self._find_bar.scope
        total_source = 0
        total_sentences = 0

        self._find_suppress_refresh = True
        try:
            if scope in (SCOPE_ALL, SCOPE_SOURCE):
                text = self._editor.toPlainText()
                spans = find_matches(text, term, case_sensitive)
                if spans:
                    cursor = QTextCursor(self._editor.document())
                    cursor.beginEditBlock()
                    try:
                        # 从后往前替换，保证尚未处理区间的下标不受影响
                        for start, end in reversed(spans):
                            cursor.setPosition(start)
                            cursor.setPosition(end, QTextCursor.KeepAnchor)
                            cursor.insertText(replacement)
                    finally:
                        cursor.endEditBlock()
                    total_source = len(spans)

            if scope in (SCOPE_ALL, SCOPE_SENTENCES):
                changed: list[tuple[int, str]] = []
                for row, sentence in enumerate(self._sentences):
                    new_sentence, count = replace_all(
                        sentence, term, replacement, case_sensitive
                    )
                    if count:
                        changed.append((row, new_sentence))
                        total_sentences += count
                if changed:
                    self._table.blockSignals(True)
                    try:
                        for row, new_sentence in changed:
                            self._sentences[row] = new_sentence
                            item = self._table.item(row, 1)
                            if item is not None:
                                item.setText(new_sentence)
                    finally:
                        self._table.blockSignals(False)
                    self._save_sentences_to_project()
                    self._update_stats()
                    self._emit_sentences()
        finally:
            self._find_suppress_refresh = False

        self._refresh_find(reset=True, navigate=False)
        if total_source or total_sentences:
            self._status_label.setText(
                f"替换完成：原文 {total_source} 处，分片句子 {total_sentences} 处"
            )
        else:
            self._status_label.setText(f"未找到「{term}」")
            self._find_bar.set_info("无匹配")

    def _clear_find_highlights(self):
        """清除查找高亮与匹配缓存。"""
        self._find_matches = []
        self._find_index = -1
        self._editor.setExtraSelections([])
        self._delegate.set_search("", False, None)
        self._table.viewport().update()

    def _close_find_bar(self):
        """外部（切换工程/新建工程）关闭查找条。"""
        if hasattr(self, "_find_bar") and self._find_bar.isVisible():
            self._find_focus_back = None
            self._find_bar.close_bar()

    def get_text(self) -> str:
        return self._editor.toPlainText()

    def set_text(self, text: str):
        self._editor.setPlainText(text)

    def get_sentences(self) -> list[str]:
        return list(self._sentences)
