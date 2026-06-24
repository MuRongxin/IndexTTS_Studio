"""后台拆分线程：避免 LLM 调用阻塞 GUI。"""
import logging

from PySide6.QtCore import QThread, Signal

from index_tts_gui.core.llm_service import LLMService, LLMServiceError
from index_tts_gui.core.splitter import RuleBasedSplitter


logger = logging.getLogger("index_tts")


class SplitWorker(QThread):
    """后台执行文本拆分。"""

    started = Signal()
    progress = Signal(int, int, str)  # current, total, message
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
        mode = self._mode.lower().strip()
        try:
            if mode == "rule":
                sentences = RuleBasedSplitter(self._max_length).split(self._text)
                self.finished.emit(sentences, False, "规则拆分完成")
                return

            service = LLMService(self._llm_cfg)
            if not service.is_configured():
                if mode == "llm":
                    raise LLMServiceError("LLM 模式需要有效的 api_url / api_key / model")
                # auto 模式回退
                sentences = RuleBasedSplitter(self._max_length).split(self._text)
                self.finished.emit(sentences, False, "LLM 未配置，已回退规则拆分")
                return

            try:
                sentences = service.split_text(
                    self._text, self._max_length,
                    on_progress=lambda c, t, m: self.progress.emit(c, t, m),
                )
                self.finished.emit(sentences, True, "LLM 拆分完成")
            except LLMServiceError as e:
                if mode == "llm":
                    raise
                # auto 模式回退
                sentences = RuleBasedSplitter(self._max_length).split(self._text)
                self.finished.emit(sentences, False, f"LLM 失败({e})，已回退规则拆分")

        except LLMServiceError as e:
            logger.exception("LLM 拆分失败")
            if mode in ("llm", "auto"):
                try:
                    sentences = RuleBasedSplitter(self._max_length).split(self._text)
                    self.finished.emit(sentences, False, f"LLM 拆分失败({e})，已回退规则拆分")
                except Exception as e2:
                    self.finished.emit([], False, f"拆分失败: {e}; 规则回退也失败: {e2}")
            else:
                self.finished.emit([], False, f"拆分失败: {e}")
        except Exception as e:
            logger.exception("拆分异常")
            self.finished.emit([], False, f"拆分失败: {e}")
