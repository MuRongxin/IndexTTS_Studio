"""空状态组件 — 用于面板无数据时展示引导。"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QVBoxLayout, QLabel, QWidget

from index_tts_gui.ui.theme import Theme


class EmptyState(QWidget):
    """居中的空状态提示。

    参数:
        title: 主标题
        subtitle: 副标题/操作提示
        parent: 父控件
    """

    def __init__(self, title: str, subtitle: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(f"background: transparent;")

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignCenter)
        layout.setSpacing(8)

        self._title = QLabel(title)
        self._title.setAlignment(Qt.AlignCenter)
        self._title.setStyleSheet(
            f"color: {Theme.colors.text_secondary}; font-size: {Theme.fonts.size_lg}px; font-weight: 600;"
        )
        layout.addWidget(self._title)

        self._subtitle = QLabel(subtitle)
        self._subtitle.setAlignment(Qt.AlignCenter)
        self._subtitle.setWordWrap(True)
        self._subtitle.setStyleSheet(
            f"color: {Theme.colors.text_tertiary}; font-size: {Theme.fonts.size_sm}px;"
        )
        layout.addWidget(self._subtitle)

    def set_title(self, title: str):
        self._title.setText(title)

    def set_subtitle(self, subtitle: str):
        self._subtitle.setText(subtitle)
