"""字幕重新生成 worker — 在后台线程执行 ffprobe + 字幕生成"""
import logging

from PySide6.QtCore import QThread, Signal

from index_tts_gui.core.merger import collect_sentence_wavs
from index_tts_gui.core.subtitler import (
    generate_srt_from_sentences,
    generate_srt_from_sentences_with_pauses,
)


logger = logging.getLogger("index_tts")


class SubtitleRegenerateWorker(QThread):
    """后台线程：收集 WAV → ffprobe 取时长 → 生成字幕条目。"""

    # 任务结果信号。不能叫 finished：那会遮蔽 QThread 内置的线程退出信号
    result_ready = Signal(list)  # entries: list[SubtitleEntry]
    error = Signal(str)          # 错误信息

    def __init__(
        self,
        sentences: list[str],
        output_dir: str,
        pauses: list[float] | None = None,
    ):
        super().__init__()
        self._sentences = sentences
        self._output_dir = output_dir
        self._pauses = pauses

    def cancel(self) -> None:
        """请求中断：run() 会在每个步骤之间检查并立即返回，不发出信号。"""
        self.requestInterruption()

    def run(self):
        # 每一步之间检查中断请求：ffprobe 与生成都是不可切分的耗时操作，
        # 只能在它们的边界上让出，保证 cancel() 后线程能尽快退出。
        if self.isInterruptionRequested():
            return
        try:
            # 必须按文件名中的数字序号排序：sentence_100 按字典序会排在
            # sentence_10 前面，直接 sorted() 会在 ≥100 句时错序
            wavs = collect_sentence_wavs(self._output_dir)
            if self.isInterruptionRequested():
                return
            if not wavs:
                self.error.emit(f"{self._output_dir}/ 下无分句 WAV")
                return
            if len(wavs) != len(self._sentences):
                self.error.emit(
                    f"句子数（{len(self._sentences)}）与音频数（{len(wavs)}）不一致"
                )
                return

            if self._pauses and len(self._pauses) == len(self._sentences):
                entries = generate_srt_from_sentences_with_pauses(
                    self._sentences, wavs, self._pauses
                )
            else:
                entries = generate_srt_from_sentences(
                    self._sentences, wavs
                )
            if self.isInterruptionRequested():
                return
            self.result_ready.emit(entries)
        except Exception as e:
            logger.exception("字幕重新生成失败")
            self.error.emit(str(e))
