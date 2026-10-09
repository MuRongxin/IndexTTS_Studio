"""pytest 全局配置。

两件事必须集中在这里，否则各测试文件各自为政会导致"单跑某个文件就挂"：

1. 在import PySide6 之前把 QT_QPA_PLATFORM 设为 offscreen。
   以前只有 2/19 个文件设了，其余文件（test_editor_table / test_subtitle_panel）
   无保护地构造 QApplication 并 show()，无头机器上会挂 —— 全靠 pytest
   按字母序收集时碰巧排在有设置的文件后面才通过。

2. 提供唯一的 session 级 QApplication fixture。
   以前有 4 份各自独立的同名 fixture，其中 test_workers.py 建的是
   QCoreApplication；谁先构造谁赢，widget 构造是否安全纯看收集顺序。
"""
import os
import sys

import pytest

# 必须在导入 PySide6 之前设置
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)


@pytest.fixture(scope="session")
def qapp():
    """整个测试会话共用一个 QApplication。

    用 QApplication 而非 QCoreApplication：部分测试要构造并show() 真实
    widget，QCoreApplication 下会直接中止。
    """
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv[:1])
    yield app
