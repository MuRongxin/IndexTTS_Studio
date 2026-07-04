"""校准 worker — 在后台线程执行音频对齐与字幕时间戳重新映射。"""
import logging
import os

from PySide6.QtCore import QThread, Signal

from index_tts_gui.core.merger import collect_sentence_wavs, get_wav_duration
from index_tts_gui.core.speech_aligner import align_sentences, recalibrate_entries
from index_tts_gui.core.subtitle import SubtitleEntry


logger = logging.getLogger("index_tts")


class CalibrateWorker(QThread):
    """后台校准线程：对齐修改后的音频 → 重新映射字幕时间戳。"""

    log = Signal(str)
    progress = Signal(int, int, str)
    finished = Signal(list)
    error = Signal(str)

    def __init__(
        self,
        modified_wav_path: str,
        sentences: list[str],
        output_dir: str,
        original_pauses: list[float],
        current_entries: list[SubtitleEntry],
    ):
        super().__init__()
        self._modified_wav_path = modified_wav_path
        self._sentences = sentences
        self._output_dir = output_dir
        self._original_pauses = original_pauses
        self._current_entries = current_entries
        self._canceled = False

    def cancel(self):
        self._canceled = True

    def run(self):
        try:
            self._do_calibrate()
        except Exception as e:
            logger.exception("字幕校准失败")
            self.error.emit(str(e))

    def _do_calibrate(self):
        self.log.emit("开始校准字幕时间戳…")

        self.progress.emit(1, 3, "收集原始分句音频")
        sentence_wavs = collect_sentence_wavs(self._output_dir)
        if not sentence_wavs:
            raise RuntimeError(f"在 {self._output_dir} 下未找到 sentence_*.wav")
        if len(sentence_wavs) != len(self._sentences):
            raise RuntimeError(
                f"分句音频数 ({len(sentence_wavs)}) 与句子数 ({len(self._sentences)}) 不一致"
            )
        self.log.emit(f"已找到 {len(sentence_wavs)} 个分句音频")

        if self._canceled:
            return

        self.progress.emit(2, 3, "正在对齐音频…")
        pauses = (
            self._original_pauses
            if self._original_pauses
            else [0.0] * len(self._sentences)
        )
        if len(pauses) < len(self._sentences):
            pauses = list(pauses) + [0.0] * (len(self._sentences) - len(pauses))

        new_starts = align_sentences(
            self._modified_wav_path,
            sentence_wavs,
            self._sentences,
            pauses,
        )

        if self._canceled:
            return

        self.progress.emit(3, 3, "正在重新映射字幕时间戳…")

        original_durations = [get_wav_duration(p) for p in sentence_wavs]
        old_cumulative = 0.0
        old_starts = []
        for i in range(len(self._sentences)):
            old_starts.append(old_cumulative)
            old_cumulative += original_durations[i]
            if i < len(pauses):
                old_cumulative += pauses[i]

        new_entries = recalibrate_entries(
            self._current_entries,
            old_starts,
            original_durations,
            new_starts,
        )

        self.log.emit(f"校准完成: {len(new_entries)} 条字幕已重新映射")
        self.finished.emit(new_entries)
