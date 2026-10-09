"""后台拆分线程：避免 LLM 调用阻塞 GUI。"""
import logging

from PySide6.QtCore import QThread, Signal

from index_tts_gui.core.llm_service import LLMService, LLMServiceError
from index_tts_gui.core.splitter import RuleBasedSplitter


logger = logging.getLogger("index_tts")


class _SplitCanceled(Exception):
    """拆分被用户取消（内部控制流，不作为错误上报）。"""


class SplitWorker(QThread):
    """后台执行文本拆分。"""

    # 不能叫 started：那会遮蔽 QThread 内置的线程启动信号
    run_started = Signal()
    progress = Signal(int, int, str)  # current, total, message
    chunk_ready = Signal(list)         # 单块拆分完成，sentences: list[str]（用于增量显示）
    # 任务结果信号。不能叫 finished：那会遮蔽 QThread 内置的线程退出信号
    result_ready = Signal(list, bool, str)
    canceled = Signal()                # 用户取消：所有取消返回路径统一发射
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
        self._canceled = False

    def cancel(self):
        """请求取消：在分块进度回调等安全点生效，不发任何结果信号。"""
        self._canceled = True

    @staticmethod
    def _split_message(service: LLMService) -> str:
        """把拆分完整性诊断写进界面提示，避免内容被静默漏掉。"""
        pct = round(service.last_coverage * 100)
        if not service.last_missing:
            return f"LLM 拆分完成（内容覆盖 {pct}%）"
        samples = "、".join(s[:12] for s in service.last_missing[:3])
        return f"LLM 拆分完成（内容覆盖 {pct}%），⚠ 疑似遗漏: {samples}"

    def _on_chunk_progress(self, c: int, t: int, m: str):
        if self._canceled:
            raise _SplitCanceled()
        self.progress.emit(c, t, m)

    def _on_chunk_result(self, sentences: list[str]):
        """单块拆分完成：检查取消后发射 chunk_ready 供 UI 增量显示。"""
        if self._canceled:
            raise _SplitCanceled()
        self.chunk_ready.emit(sentences)

    def run(self):
        self.run_started.emit()
        mode = self._mode.lower().strip()
        try:
            if self._canceled:
                self.canceled.emit()
                return
            if mode == "rule":
                sentences = RuleBasedSplitter(self._max_length).split(self._text)
                self.result_ready.emit(sentences, False, "规则拆分完成")
                return

            service = LLMService(self._llm_cfg)
            if not service.is_configured():
                if mode == "llm":
                    raise LLMServiceError("LLM 模式需要有效的 api_url / api_key / model")
                # auto 模式回退
                sentences = RuleBasedSplitter(self._max_length).split(self._text)
                self.result_ready.emit(sentences, False, "LLM 未配置，已回退规则拆分")
                return

            sentences = service.split_text(
                self._text, self._max_length,
                on_progress=self._on_chunk_progress,
                on_chunk_result=self._on_chunk_result,
            )
            if self._canceled:
                self.canceled.emit()
                return
            self.result_ready.emit(sentences, True, self._split_message(service))

        except _SplitCanceled:
            self.canceled.emit()
            return
        except LLMServiceError as e:
            # LLM 失败只有这一条回退路径（不再在内部 try 里重复处理），
            # 同一故障不会出现两种提示文案
            logger.exception("LLM 拆分失败")
            try:
                sentences = RuleBasedSplitter(self._max_length).split(self._text)
                self.result_ready.emit(sentences, False, f"LLM 拆分失败({e})，已回退规则拆分")
            except Exception as e2:
                self.result_ready.emit([], False, f"拆分失败: {e}; 规则回退也失败: {e2}")
        except Exception as e:
            logger.exception("拆分异常")
            self.result_ready.emit([], False, f"拆分失败: {e}")
