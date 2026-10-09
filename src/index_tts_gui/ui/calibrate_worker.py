"""校准 worker — 在后台线程执行音频对齐与字幕时间戳重新映射。"""
import logging
import os

from PySide6.QtCore import QThread, Signal

from index_tts_gui.core.merger import collect_sentence_wavs, get_wav_duration
from index_tts_gui.core.speech_aligner import (
    align_sentences,
    cumulative_starts,
    is_matched,
    recalibrate_entries,
)
from index_tts_gui.core.subtitle import SubtitleEntry


logger = logging.getLogger("index_tts")


class CalibrateWorker(QThread):
    """后台校准线程：对齐修改后的音频 → 重新映射字幕时间戳。"""

    log = Signal(str)
    progress = Signal(int, int, str)
    # 任务结果信号。不能叫 finished：那会遮蔽 QThread 内置的线程退出信号
    result_ready = Signal(list)
    error = Signal(str)
    canceled = Signal()

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

    def cancel(self):
        """请求取消。用 Qt 的 interruption 机制。"""
        self.requestInterruption()

    @property
    def _canceled(self) -> bool:
        """是否已请求取消（保留旧属性名，兼容既有调用与测试）。"""
        return self.isInterruptionRequested()

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
            self.canceled.emit()
            return

        self.progress.emit(2, 3, "正在对齐音频…")
        # pauses 的补齐规则统一交给 cumulative_starts，避免三处实现各自漂移
        pauses = list(self._original_pauses) if self._original_pauses else []

        new_starts, scores = align_sentences(
            self._modified_wav_path,
            sentence_wavs,
            self._sentences,
            pauses,
        )
        # 全部未定位 → 音频不含任何分句，直接报错
        missing = [i + 1 for i in range(len(new_starts))
                   if not is_matched(new_starts[i], scores[i])]
        if missing and len(missing) == len(self._sentences):
            raise RuntimeError(
                "未能在该音频中匹配到任何分句音频。\n"
                "请确认加载的是【调整间隔后的配音音频】"
                "（full_dub.wav 或其编辑版本），而非视频原声或其他人声文件。"
            )
        unreliable = [i + 1 for i in range(len(scores))
                       if not is_matched(new_starts[i], scores[i])]
        if unreliable:
            shown = ", ".join(map(str, unreliable[:10]))
            more = "…" if len(unreliable) > 10 else ""
            self.log.emit(
                f"{len(unreliable)} 句未能定位"
                "（可能已被删除，或该句后来重新合成过——"
                "两遍 TTS 合成波形不同，无法用当前分句音频匹配），"
                f"对应字幕将被移除: {shown}{more}"
            )

        if self._canceled:
            self.canceled.emit()
            return

        self.progress.emit(3, 3, "正在重新映射字幕时间戳…")

        original_durations = [get_wav_duration(p) for p in sentence_wavs]
        old_starts = cumulative_starts(original_durations, pauses)

        new_entries, dropped = recalibrate_entries(
            self._current_entries,
            old_starts,
            original_durations,
            new_starts,
        )

        msg = f"校准完成: {len(new_entries)} 条字幕已重新映射"
        if dropped:
            msg += f"，{dropped} 条因对应句子已不在音频中被移除"
        self.log.emit(msg)
        self.result_ready.emit(new_entries)
