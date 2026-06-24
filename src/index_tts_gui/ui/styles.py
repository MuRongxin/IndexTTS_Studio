"""全局 QSS 样式生成器。

基于 theme.py 中的 token，生成统一的 Qt 样式表。
使用方式：
    app.setStyleSheet(global_stylesheet())
"""
from __future__ import annotations

from index_tts_gui.ui.theme import Theme


def global_stylesheet() -> str:
    """返回应用全局样式表字符串。"""
    c = Theme.colors
    f = Theme.fonts
    r = Theme.radius

    return f"""
    /* ── 基础窗口与字体 ── */
    QWidget {{
        font-family: {f.family};
        font-size: {f.size_base}px;
        color: {c.text_primary};
        outline: none;
    }}

    QMainWindow {{
        background: {c.bg};
    }}

    QToolTip {{
        background: {c.text_primary};
        color: {c.text_on_primary};
        border: none;
        border-radius: {r.sm}px;
        padding: 4px 8px;
        font-size: {f.size_sm}px;
    }}

    /* ── 按钮 ── */
    QPushButton {{
        background: {c.surface};
        color: {c.text_primary};
        border: 1px solid {c.border};
        border-radius: {r.sm}px;
        padding: 8px 16px;
        font-weight: 500;
        min-height: 32px;
    }}
    QPushButton:hover {{
        background: {c.surface_hover};
        border-color: {c.text_tertiary};
    }}
    QPushButton:pressed {{
        background: {c.surface_active};
    }}
    QPushButton:disabled {{
        background: {c.bg};
        color: {c.text_disabled};
        border-color: {c.border};
    }}

    QPushButton[variant="primary"] {{
        background: {c.primary};
        color: {c.text_on_primary};
        border: none;
    }}
    QPushButton[variant="primary"]:hover {{
        background: {c.primary_hover};
    }}
    QPushButton[variant="primary"]:pressed {{
        background: {c.primary_active};
    }}
    QPushButton[variant="primary"]:disabled {{
        background: {c.primary_light};
        color: {c.text_on_primary};
    }}

    QPushButton[variant="danger"] {{
        background: {c.error};
        color: {c.text_on_primary};
        border: none;
    }}
    QPushButton[variant="danger"]:hover {{
        background: #DC2626;
    }}

    QPushButton[variant="ghost"] {{
        background: transparent;
        border: none;
        color: {c.text_secondary};
    }}
    QPushButton[variant="ghost"]:hover {{
        background: {c.bg};
        color: {c.text_primary};
    }}

    /* ── 输入框 ── */
    QLineEdit, QTextEdit, QPlainTextEdit {{
        background: {c.surface};
        border: 1px solid {c.border};
        border-radius: {r.sm}px;
        padding: 8px 12px;
        selection-background-color: {c.primary_light};
        selection-color: {c.primary};
    }}
    QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus {{
        border-color: {c.primary};
    }}
    QLineEdit:disabled, QTextEdit:disabled, QPlainTextEdit:disabled {{
        background: {c.bg};
        color: {c.text_disabled};
    }}

    /* ── 数字/下拉框 ── */
    QSpinBox, QDoubleSpinBox, QComboBox {{
        background: {c.surface};
        border: 1px solid {c.border};
        border-radius: {r.sm}px;
        padding: 6px 10px;
        min-height: 28px;
    }}
    QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{
        border-color: {c.primary};
    }}
    QComboBox::drop-down {{
        border: none;
        width: 24px;
    }}
    QComboBox::down-arrow {{
        image: none;
        border-left: 4px solid transparent;
        border-right: 4px solid transparent;
        border-top: 5px solid {c.text_secondary};
        width: 0;
        height: 0;
    }}
    QSpinBox::up-button, QDoubleSpinBox::up-button {{
        subcontrol-origin: border;
        subcontrol-position: top right;
        width: 18px;
        border-left: 1px solid {c.border};
        border-bottom: 1px solid {c.border};
    }}
    QSpinBox::down-button, QDoubleSpinBox::down-button {{
        subcontrol-origin: border;
        subcontrol-position: bottom right;
        width: 18px;
        border-left: 1px solid {c.border};
    }}

    /* ── 复选框 ── */
    QCheckBox {{
        spacing: 6px;
    }}
    QCheckBox::indicator {{
        width: 16px;
        height: 16px;
        border: 1px solid {c.border};
        border-radius: 4px;
        background: {c.surface};
    }}
    QCheckBox::indicator:checked {{
        background: {c.primary};
        border-color: {c.primary};
    }}

    /* ── 滑块 ── */
    QSlider::groove:horizontal {{
        height: 4px;
        background: {c.border};
        border-radius: 2px;
    }}
    QSlider::sub-page:horizontal {{
        background: {c.primary};
        border-radius: 2px;
    }}
    QSlider::handle:horizontal {{
        width: 14px;
        height: 14px;
        margin: -5px 0;
        background: {c.surface};
        border: 1px solid {c.border};
        border-radius: 7px;
    }}
    QSlider::handle:horizontal:hover {{
        background: {c.primary_light};
        border-color: {c.primary};
    }}

    /* ── 进度条 ── */
    QProgressBar {{
        border: none;
        border-radius: 4px;
        background: {c.border};
        text-align: center;
        color: {c.text_secondary};
        font-size: {f.size_sm}px;
        height: 8px;
    }}
    QProgressBar::chunk {{
        background: {c.primary};
        border-radius: 4px;
    }}

    /* ── 表格 ── */
    QTableWidget {{
        background: {c.surface};
        border: 1px solid {c.border};
        border-radius: {r.sm}px;
        gridline-color: {c.divider};
        selection-background-color: {c.primary_light};
        selection-color: {c.text_primary};
    }}
    QTableWidget::item {{
        padding: 8px 12px;
        border-bottom: 1px solid {c.divider};
    }}
    QTableWidget::item:selected {{
        background: {c.primary_light};
    }}
    QHeaderView::section {{
        background: {c.bg};
        color: {c.text_secondary};
        padding: 8px 12px;
        border: none;
        border-bottom: 1px solid {c.border};
        font-weight: 600;
        font-size: {f.size_sm}px;
    }}
    QTableWidget::item:focus {{
        border: none;
    }}

    /* ── 分组框 ── */
    QGroupBox {{
        background: {c.surface};
        border: 1px solid {c.border};
        border-radius: {r.md}px;
        margin-top: 12px;
        padding-top: 16px;
        padding: 16px;
        font-weight: 600;
    }}
    QGroupBox::title {{
        subcontrol-origin: margin;
        subcontrol-position: top left;
        left: 12px;
        top: -10px;
        padding: 0 6px;
        color: {c.text_secondary};
        font-size: {f.size_sm}px;
        font-weight: 600;
        background: {c.surface};
    }}

    /* ── 滚动条 ── */
    QScrollBar:vertical {{
        background: transparent;
        width: 8px;
        margin: 0;
    }}
    QScrollBar::handle:vertical {{
        background: {c.text_tertiary};
        border-radius: 4px;
        min-height: 30px;
    }}
    QScrollBar::handle:vertical:hover {{
        background: {c.text_secondary};
    }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
        height: 0;
    }}
    QScrollBar:horizontal {{
        background: transparent;
        height: 8px;
    }}
    QScrollBar::handle:horizontal {{
        background: {c.text_tertiary};
        border-radius: 4px;
        min-width: 30px;
    }}

    /* ── 标签页/分割器 ── */
    QSplitter::handle {{
        background: {c.border};
    }}
    QSplitter::handle:horizontal {{
        width: 2px;
    }}
    QSplitter::handle:vertical {{
        height: 2px;
    }}

    /* ── 对话框 ── */
    QDialog {{
        background: {c.bg};
    }}
    QDialog QPushButton {{
        min-width: 80px;
    }}
"""
