"""应用数据目录解析 — 配置 / 日志 / 工程目录的统一落点。

原先这些路径都相对 os.getcwd() 解析，导致从桌面快捷方式、launcher、
systemd 等非仓库目录启动时会得到一份全新的空工程和第二个日志文件。

解析优先级：
1. 环境变量 INDEX_TTS_HOME —— 显式指定数据根目录
2. 开发检出 —— src/index_tts_gui/core/paths.py 上溯到含 pyproject.toml
   的仓库根（pip install -e . 的典型布局）
3. 当前工作目录 —— pip 安装后的兜底，保持旧行为
"""
from __future__ import annotations

import os
from pathlib import Path


#: 显式指定数据根目录的环境变量名
ENV_HOME = "INDEX_TTS_HOME"

#: 判定"开发检出仓库根"的标志文件
_MARKER = "pyproject.toml"


def _detect_repo_root() -> str | None:
    """若当前安装布局是源码检出，返回仓库根目录；否则 None。"""
    try:
        candidate = Path(__file__).resolve().parents[3]
    except (OSError, IndexError):  # pragma: no cover - 极端路径异常
        return None
    if (candidate / _MARKER).is_file():
        return str(candidate)
    return None


def app_root() -> str:
    """配置、日志与默认工程所在的数据根目录。"""
    env = (os.environ.get(ENV_HOME) or "").strip()
    if env:
        return os.path.abspath(os.path.expanduser(env))
    repo = _detect_repo_root()
    if repo:
        return repo
    return os.getcwd()


def data_dir() -> str:
    """工程 / 配置 / 日志的根目录（当前与 app_root 相同，保留语义占位）。"""
    return app_root()
