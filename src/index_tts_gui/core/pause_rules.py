"""
停顿规则：根据文本标点计算句间停顿时长。

该模块仅依赖纯文本，不依赖音频或 UI，可供 merger / llm_service 共同引用。
"""
from __future__ import annotations


def compute_pauses(sentences: list[str], base_pause: float = 0.12) -> list[float]:
    """
    根据句子末尾标点计算每句之后的停顿时长。

    最后一句后面返回 0（不需要停顿）。
    """
    pauses = []
    for s in sentences:
        s = s.strip()
        if not s:
            pauses.append(base_pause)
            continue
        last_char = s[-1]
        if last_char in "。！？":
            pauses.append(0.55)
        elif last_char in "，、；：":
            pauses.append(0.22)
        else:
            pauses.append(base_pause)
    # 最后一句不需要尾部停顿
    if pauses:
        pauses[-1] = 0.0
    return pauses
