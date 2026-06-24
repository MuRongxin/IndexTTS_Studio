"""设计系统 — 颜色、字体、间距、圆角、阴影等视觉 Token。

所有 UI 文件应从这里引用常量，避免硬编码。
"""
from __future__ import annotations


class Colors:
    """浅色专业风配色"""

    # 背景
    bg = "#F3F4F6"          # 窗口背景
    surface = "#FFFFFF"      # 卡片/面板背景
    surface_hover = "#F9FAFB"  # 卡片悬停
    surface_active = "#EFF6FF"  # 卡片选中/激活

    # 边框
    border = "#E5E7EB"
    border_focus = "#93C5FD"
    divider = "#E5E7EB"

    # 主色
    primary = "#2563EB"
    primary_hover = "#1D4ED8"
    primary_active = "#1E40AF"
    primary_light = "#DBEAFE"

    # 文字
    text_primary = "#111827"
    text_secondary = "#6B7280"
    text_tertiary = "#9CA3AF"
    text_on_primary = "#FFFFFF"
    text_disabled = "#9CA3AF"

    # 功能色
    success = "#10B981"
    success_light = "#D1FAE5"
    warning = "#F59E0B"
    warning_light = "#FEF3C7"
    error = "#EF4444"
    error_light = "#FEE2E2"
    info = "#3B82F6"
    info_light = "#DBEAFE"

    # 阴影（CSS 字符串）
    shadow_sm = "0 1px 2px rgba(0,0,0,0.05)"
    shadow_md = "0 4px 6px -1px rgba(0,0,0,0.1), 0 2px 4px -1px rgba(0,0,0,0.06)"
    shadow_lg = "0 10px 15px -3px rgba(0,0,0,0.1), 0 4px 6px -2px rgba(0,0,0,0.05)"


class Fonts:
    """字体族与字号"""

    family = "-apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', sans-serif"
    mono = "'SF Mono', Consolas, 'Courier New', monospace"

    size_xs = 11
    size_sm = 12
    size_base = 13
    size_md = 14
    size_lg = 16
    size_xl = 20
    size_2xl = 24


class Spacing:
    """间距网格"""

    xs = 4
    sm = 8
    md = 12
    lg = 16
    xl = 24
    xxl = 32
    xxxl = 48


class Radius:
    """圆角"""

    sm = 6
    md = 10
    lg = 16
    xl = 24
    full = 9999


class Transitions:
    """过渡动画"""

    fast = "80ms ease"
    normal = "150ms ease"
    slow = "250ms ease"


class Theme:
    """聚合访问入口"""

    colors = Colors
    fonts = Fonts
    spacing = Spacing
    radius = Radius
    transitions = Transitions
