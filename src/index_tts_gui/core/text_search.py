"""纯文本的查找/替换工具（不依赖 Qt）。

文稿面板的查找替换同时作用于「原文」和「分片句子」，两处必须使用
完全一致的匹配规则，所以统一收敛到这里：

- 默认大小写不敏感，可选区分大小写；
- 纯文本匹配（不支持正则），从左到右、互不重叠；
- 返回原字符串下标，不做 ``lower()`` 之类的转换，避免个别 Unicode
  字符大小写转换后长度变化导致下标错位（例如 'İ'.lower() 是 2 个字符）。
"""
from __future__ import annotations

import re


def _compile(term: str, case_sensitive: bool) -> re.Pattern[str]:
    flags = 0 if case_sensitive else re.IGNORECASE
    return re.compile(re.escape(term), flags)


def find_matches(
    text: str, term: str, case_sensitive: bool = False
) -> list[tuple[int, int]]:
    """返回 ``[start, end)`` 匹配区间列表；term 为空时返回空列表。"""
    if not text or not term:
        return []
    return [(m.start(), m.end()) for m in _compile(term, case_sensitive).finditer(text)]


def replace_all(
    text: str, term: str, replacement: str, case_sensitive: bool = False
) -> tuple[str, int]:
    """把 text 中所有匹配替换为 replacement，返回 ``(新文本, 替换次数)``。

    替换串按字面量处理（作为函数返回值，不会解析 ``\\1`` 之类的反斜杠转义）；
    term 为空时原样返回、次数为 0。
    """
    if not text or not term:
        return text, 0
    pattern = _compile(term, case_sensitive)
    return pattern.subn(lambda _match: replacement, text)


def replace_span(text: str, start: int, end: int, replacement: str) -> str:
    """把 ``text[start:end]`` 替换为 replacement（不做匹配校验）。"""
    return text[:start] + replacement + text[end:]
