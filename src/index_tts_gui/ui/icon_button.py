"""图标按钮组件 — 支持纯图标或图标 + 文字。"""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QPushButton

from index_tts_gui.ui.theme import Theme


class IconButton(QPushButton):
    """通用图标按钮。

    支持三种尺寸：sm(28px)、md(32px)、lg(40px)
    支持三种变体：primary、ghost、default
    """

    def __init__(
        self,
        icon_path: str | None = None,
        text: str = "",
        variant: str = "default",
        size: str = "md",
        parent=None,
    ):
        super().__init__(text, parent)
        self._variant = variant
        self._size = size
        self.setProperty("variant", variant)
        self.setCursor(Qt.PointingHandCursor)

        # 尺寸配置
        sizes = {"sm": (28, 20), "md": (32, 24), "lg": (40, 28)}
        btn_size, icon_px = sizes.get(size, sizes["md"])
        self.setFixedSize(btn_size, btn_size) if not text else self.setMinimumHeight(btn_size)
        self.setIconSize(QSize(icon_px, icon_px))

        if icon_path:
            self.setIcon(QIcon(icon_path))

        # 纯图标时文字不显示
        if not text:
            self.setText("")

        self._apply_style()

    def _apply_style(self):
        c = Theme.colors
        if self._variant == "primary":
            self.setStyleSheet(f"""
                QPushButton {{
                    background: {c.primary};
                    color: {c.text_on_primary};
                    border: none;
                    border-radius: {Theme.radius.sm}px;
                    padding: 6px 12px;
                    font-weight: 500;
                }}
                QPushButton:hover {{ background: {c.primary_hover}; }}
                QPushButton:pressed {{ background: {c.primary_active}; }}
                QPushButton:disabled {{ background: {c.primary_light}; }}
            """)
        elif self._variant == "ghost":
            self.setStyleSheet(f"""
                QPushButton {{
                    background: transparent;
                    color: {c.text_secondary};
                    border: none;
                    border-radius: {Theme.radius.sm}px;
                    padding: 6px;
                }}
                QPushButton:hover {{ background: {c.bg}; color: {c.text_primary}; }}
                QPushButton:pressed {{ background: {c.surface_active}; }}
            """)
        else:
            self.setStyleSheet(f"""
                QPushButton {{
                    background: {c.surface};
                    color: {c.text_primary};
                    border: 1px solid {c.border};
                    border-radius: {Theme.radius.sm}px;
                    padding: 6px 12px;
                    font-weight: 500;
                }}
                QPushButton:hover {{ background: {c.surface_hover}; border-color: {c.text_tertiary}; }}
                QPushButton:pressed {{ background: {c.surface_active}; }}
                QPushButton:disabled {{ background: {c.bg}; color: {c.text_disabled}; }}
            """)

    def set_variant(self, variant: str):
        self._variant = variant
        self.setProperty("variant", variant)
        self._apply_style()
