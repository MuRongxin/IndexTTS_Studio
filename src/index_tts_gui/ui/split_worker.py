"""后台拆分线程：避免 LLM 调用阻塞 GUI。"""
import logging

from PySide6.QtCore import QThread, Signal

from index_tts_gui.core.splitter import (
    BaseSplitter,
    HybridSplitter,
    create_splitter,
)


logger = logging.getLogger("index_tts")


class SplitWorker(QThread):
    """后台执行文本拆分。"""

    started = Signal()
    finished = Signal(list, bool, str)
    # sentences: list[str], used_llm: bool, message: str

    def __init__(
        self,
        text: str,
        mode: str,
        llm_cfg: dict | None,
        max_length: int = 0,
        parent=None,
    ):
        super().__init__(parent)
        self._text = text
        self._mode = mode
        self._llm_cfg = llm_cfg or {}
        self._max_length = max_length

    def run(self):
        self.started.emit()
        try:
            splitter = create_splitter(
                mode=self._mode,
                llm_cfg=self._llm_cfg,
                max_length=self._max_length,
            )

            used_llm = False
            if isinstance(splitter, HybridSplitter):
                sentences, used_llm = splitter.split(self._text)
            else:
                sentences = splitter.split(self._text)
                if self._mode == "llm":
                    used_llm = True

            if self._mode == "llm":
                msg = "LLM 拆分完成" if used_llm else "LLM 拆分失败"
            elif self._mode == "auto":
                msg = "LLM 拆分完成" if used_llm else "LLM 拆分失败，已回退规则拆分"
            else:
                msg = "规则拆分完成"

            logger.info("拆分完成: mode=%s used_llm=%s sentences=%d", self._mode, used_llm, len(sentences))
            self.finished.emit(sentences, used_llm, msg)
        except Exception as e:
            logger.exception("拆分失败: mode=%s", self._mode)
            if self._mode in ("llm", "auto"):
                # LLM/自动 模式失败时，回退规则拆分，避免用户拿到空结果
                try:
                    rule = create_splitter(mode="rule", max_length=self._max_length)
                    sentences = rule.split(self._text)
                    logger.info("LLM 失败后回退规则拆分: sentences=%d", len(sentences))
                    self.finished.emit(sentences, False, f"LLM 拆分失败({e})，已回退规则拆分")
                except Exception as e2:
                    logger.exception("规则回退也失败")
                    self.finished.emit([], False, f"拆分失败: {e}; 规则回退也失败: {e2}")
            else:
                self.finished.emit([], False, f"拆分失败: {e}")
