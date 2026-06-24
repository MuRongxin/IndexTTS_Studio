"""公共 UI 组件 / 辅助函数。

提供卡片容器等可复用控件，避免各面板重复实现。
"""
from __future__ import annotations

from PySide6.QtWidgets import QFrame, QGroupBox, QVBoxLayout, QWidget

from index_tts_gui.ui.theme import Theme


def card(title: str = "", use_groupbox: bool = False, parent: QWidget | None = None) -> QFrame | QGroupBox:
    """创建一个现代风格的卡片容器。

    参数:
        title: 卡片标题；为空时不显示标题栏
        use_groupbox: 为 True 时返回 QGroupBox（可利用其 title 绘制）
        parent: 父控件
    """
    c = Theme.colors
    r = Theme.radius

    if use_groupbox:
        widget: QFrame | QGroupBox = QGroupBox(parent)
        widget.setStyleSheet(f"""
            QGroupBox {{
                background: {c.surface};
                border: 1px solid {c.border};
                border-radius: {r.md}px;
                margin-top: 0;
                padding-top: 0;
            }}
            QGroupBox::title {{
                subcontrol-origin: padding;
                subcontrol-position: top left;
                left: 0;
                top: -20px;
                color: {c.text_secondary};
                font-size: {Theme.fonts.size_sm}px;
                font-weight: 600;
            }}
        """)
        if title:
            widget.setTitle(title)
    else:
        widget = QFrame(parent)
        widget.setStyleSheet(f"""
            QFrame {{
                background: {c.surface};
                border: 1px solid {c.border};
                border-radius: {r.md}px;
            }}
        """)

    layout = QVBoxLayout(widget)
    layout.setContentsMargins(16, 16, 16, 16)
    layout.setSpacing(12)

    if title and not use_groupbox:
        from PySide6.QtWidgets import QLabel
        lbl = QLabel(title)
        lbl.setStyleSheet(
            f"font-size: {Theme.fonts.size_lg}px; font-weight: 700; color: {c.text_primary};"
        )
        layout.addWidget(lbl)

    return widget


class Card(QFrame):
    """卡片容器 QWidget 子类，便于在 Designer 或代码中直接使用。"""

    def __init__(self, title: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        c = Theme.colors
        r = Theme.radius
        self.setStyleSheet(f"""
            QFrame {{
                background: {c.surface};
                border: 1px solid {c.border};
                border-radius: {r.md}px;
            }}
        """)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(16, 16, 16, 16)
        self._layout.setSpacing(12)

        if title:
            from PySide6.QtWidgets import QLabel
            lbl = QLabel(title)
            lbl.setStyleSheet(
                f"font-size: {Theme.fonts.size_lg}px; font-weight: 700; color: {c.text_primary};"
            )
            self._layout.addWidget(lbl)

    def layout(self):
        return self._layout
