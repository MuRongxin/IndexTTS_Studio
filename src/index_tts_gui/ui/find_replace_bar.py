"""文稿面板的查找/替换工具条。

嵌在 ManuscriptPanel 顶部，同时作用于「原文」文本框和「分片句子」表格：
- Ctrl+F / Ctrl+H 唤起，Esc 关闭；
- Enter 下一个、Shift+Enter 上一个，替换框里 Enter 替换当前并跳下一个；
- 范围可选「全部（原文+分片）」/「仅原文」/「仅分片句子」。

本模块只负责界面与交互信号，匹配/替换逻辑由 ManuscriptPanel 统一实现
（纯文本查找规则见 core.text_search）。
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


# 查找范围：与 ManuscriptPanel 约定的取值
SCOPE_ALL = "all"
SCOPE_SOURCE = "source"
SCOPE_SENTENCES = "sentences"


class _EnterLineEdit(QLineEdit):
    """区分 Enter / Shift+Enter 的行编辑，供查找框、替换框复用。"""

    enter_pressed = Signal(bool)  # reversed=True 表示 Shift+Enter

    def keyPressEvent(self, event):  # noqa: N802 - Qt 命名
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            self.enter_pressed.emit(bool(event.modifiers() & Qt.ShiftModifier))
            return
        super().keyPressEvent(event)


class FindReplaceBar(QFrame):
    """查找/替换工具条。

    信号：
    - search_changed(str, bool)：关键词变化 / 区分大小写切换
    - scope_changed(str)：查找范围变化（SCOPE_*）
    - next_requested() / prev_requested()：上一条 / 下一条
    - replace_requested() / replace_all_requested()：替换当前 / 全部替换
    - closed()：工具条被关闭（由面板清理高亮、恢复焦点）
    """

    search_changed = Signal(str, bool)
    scope_changed = Signal(str)
    next_requested = Signal()
    prev_requested = Signal()
    replace_requested = Signal()
    replace_all_requested = Signal()
    closed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("findReplaceBar")
        self.setStyleSheet("""
            QFrame#findReplaceBar {
                background: #eef4ff;
                border: 1px solid #c5d9f5;
                border-radius: 6px;
            }
            QFrame#findReplaceBar QLabel {
                color: #37474f;
                font-size: 12px;
                background: transparent;
                border: none;
            }
            QFrame#findReplaceBar QLineEdit {
                background: #ffffff;
                border: 1px solid #c9d7ea;
                border-radius: 4px;
                padding: 4px 6px;
                font-size: 13px;
            }
            QFrame#findReplaceBar QLineEdit:focus {
                border-color: #2979ff;
            }
            QFrame#findReplaceBar QPushButton {
                background: #ffffff;
                border: 1px solid #c9d7ea;
                border-radius: 4px;
                padding: 4px 10px;
                font-size: 12px;
                color: #263238;
            }
            QFrame#findReplaceBar QPushButton:hover {
                background: #e3f2fd;
            }
            QFrame#findReplaceBar QPushButton:pressed {
                background: #bbdefb;
            }
            QFrame#findReplaceBar QCheckBox {
                color: #37474f;
                font-size: 12px;
                background: transparent;
            }
        """)
        self._build_ui()
        self.hide()

    # ── 界面 ──

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 8)
        root.setSpacing(6)

        # 第一行：查找词 + 选项 + 上一条/下一条 + 计数 + 关闭
        row1 = QHBoxLayout()
        row1.setSpacing(6)

        row1.addWidget(QLabel("查找:"))
        self._search_edit = _EnterLineEdit()
        self._search_edit.setPlaceholderText("查找内容（同时查找原文与分片句子）")
        self._search_edit.setClearButtonEnabled(True)
        self._search_edit.setMinimumWidth(200)
        self._search_edit.textChanged.connect(self._on_search_text_changed)
        self._search_edit.enter_pressed.connect(self._on_search_enter)
        row1.addWidget(self._search_edit, 1)

        self._case_check = QCheckBox("Aa")
        self._case_check.setToolTip("区分大小写")
        self._case_check.toggled.connect(self._on_search_text_changed)
        row1.addWidget(self._case_check)

        row1.addWidget(QLabel("范围:"))
        self._scope_combo = QComboBox()
        self._scope_combo.addItem("全部（原文+分片）", SCOPE_ALL)
        self._scope_combo.addItem("仅原文", SCOPE_SOURCE)
        self._scope_combo.addItem("仅分片句子", SCOPE_SENTENCES)
        self._scope_combo.setToolTip("查找/替换作用的范围")
        self._scope_combo.currentIndexChanged.connect(self._on_scope_changed)
        row1.addWidget(self._scope_combo)

        self._prev_btn = QPushButton("▲")
        self._prev_btn.setToolTip("上一个匹配（Shift+Enter）")
        self._prev_btn.clicked.connect(lambda: self.prev_requested.emit())
        row1.addWidget(self._prev_btn)

        self._next_btn = QPushButton("▼")
        self._next_btn.setToolTip("下一个匹配（Enter）")
        self._next_btn.clicked.connect(lambda: self.next_requested.emit())
        row1.addWidget(self._next_btn)

        self._info_label = QLabel("")
        self._info_label.setMinimumWidth(110)
        self._info_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        row1.addWidget(self._info_label)

        self._close_btn = QPushButton("✕")
        self._close_btn.setToolTip("关闭（Esc）")
        self._close_btn.setFixedWidth(30)
        self._close_btn.clicked.connect(self.close_bar)
        row1.addWidget(self._close_btn)

        root.addLayout(row1)

        # 第二行：替换词 + 替换 / 全部替换（仅替换模式显示）
        self._replace_row = QWidget()
        row2 = QHBoxLayout(self._replace_row)
        row2.setContentsMargins(0, 0, 0, 0)
        row2.setSpacing(6)

        row2.addWidget(QLabel("替换为:"))
        self._replace_edit = _EnterLineEdit()
        self._replace_edit.setPlaceholderText("替换内容（留空表示删除）")
        self._replace_edit.setClearButtonEnabled(True)
        # 替换框里 Enter：替换当前匹配并跳到下一个
        self._replace_edit.enter_pressed.connect(lambda _rev: self.replace_requested.emit())
        row2.addWidget(self._replace_edit, 1)

        self._replace_btn = QPushButton("替换")
        self._replace_btn.setToolTip("替换当前匹配，并跳到下一个匹配（Enter）")
        self._replace_btn.clicked.connect(lambda: self.replace_requested.emit())
        row2.addWidget(self._replace_btn)

        self._replace_all_btn = QPushButton("全部替换")
        self._replace_all_btn.setToolTip("替换当前范围内所有匹配")
        self._replace_all_btn.clicked.connect(lambda: self.replace_all_requested.emit())
        row2.addWidget(self._replace_all_btn)

        root.addWidget(self._replace_row)

        esc_shortcut = QShortcut(QKeySequence("Esc"), self)
        esc_shortcut.setContext(Qt.WidgetWithChildrenShortcut)
        esc_shortcut.activated.connect(self.close_bar)

    # ── 对外接口 ──

    @property
    def term(self) -> str:
        return self._search_edit.text()

    @property
    def replacement(self) -> str:
        return self._replace_edit.text()

    @property
    def case_sensitive(self) -> bool:
        return self._case_check.isChecked()

    @property
    def scope(self) -> str:
        value = self._scope_combo.currentData()
        return value if value else SCOPE_ALL

    def activate(self, replace: bool = False, initial_term: str = ""):
        """显示工具条；replace=True 时同时显示替换行。"""
        self._replace_row.setVisible(replace)
        if initial_term:
            self._search_edit.setText(initial_term)
        self.show()
        self.focus_search(select_all=bool(initial_term))

    def focus_search(self, select_all: bool = True):
        """把焦点放到查找框；select_all=True 时全选已有查找词。"""
        self._search_edit.setFocus()
        if select_all:
            self._search_edit.selectAll()

    def focus_replace(self):
        self._replace_edit.setFocus()
        self._replace_edit.selectAll()

    def set_info(self, text: str, tooltip: str = ""):
        """设置右侧的匹配计数/提示文本。"""
        self._info_label.setText(text)
        self._info_label.setToolTip(tooltip)

    def close_bar(self):
        """关闭工具条（由面板响应 closed 信号清理高亮、恢复焦点）。"""
        if not self.isVisible():
            return
        self.hide()
        self._info_label.setText("")
        self.closed.emit()

    # ── 内部槽函数 ──

    def _on_search_text_changed(self, *_args):
        self.search_changed.emit(self.term, self.case_sensitive)

    def _on_search_enter(self, reversed_: bool):
        if reversed_:
            self.prev_requested.emit()
        else:
            self.next_requested.emit()

    def _on_scope_changed(self, *_args):
        self.scope_changed.emit(self.scope)
