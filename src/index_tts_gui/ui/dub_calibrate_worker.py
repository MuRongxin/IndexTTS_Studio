"""配音校准 worker — 后台线程：对齐修改间隔后的配音音频，反向校准字幕时间戳。"""
import glob
import logging
import os
import re

from PySide6.QtCore import QThread, Signal

from index_tts_gui.core.io_ass import entries_to_ass
from index_tts_gui.core.io_subtitle import parse_srt
from index_tts_gui.core.merger import get_wav_duration
from index_tts_gui.core.speech_aligner import (
    align_sentences,
    is_matched,
    recalibrate_entries,
)
from index_tts_gui.core.subtitle import SubtitleEntry
from index_tts_gui.core.subtitler import entries_to_srt


logger = logging.getLogger("index_tts")

# 只匹配分段文件 dub_001.wav，排除拼接产物 dub_full.wav
_SEGMENT_RE = re.compile(r"dub_(\d+)\.wav$")


class DubCalibrateWorker(QThread):
    """后台校准线程：对齐修改间隔后的配音音频 → 重新映射字幕时间戳。

    校准基准是 dub_shifted.srt（与 dub_full.wav 布局一致），以文件为基准，
    重启 app 后仍可校准。基准时间轴先整体平移到 0 起点（片头归零），
    使 align_sentences 内部的累计先验与实际布局一致；映射结果仍是
    修改后音频中的绝对时间（用户裁掉片头静音也能正确映射）。
    """

    log = Signal(str)
    progress = Signal(int, int, str)
    # 任务结果信号。不能叫 finished：那会遮蔽 QThread 内置的线程退出信号
    result_ready = Signal(list)  # 校准后的 SubtitleEntry 列表（失败/取消为空列表）
    error = Signal(str)
    canceled = Signal()

    def __init__(self, modified_wav_path: str, dub_dir: str, export_ass: bool = False):
        super().__init__()
        self._modified_wav_path = modified_wav_path
        self._dub_dir = dub_dir
        self._export_ass = export_ass

    def cancel(self):
        """请求取消。用 Qt 的 interruption 机制。"""
        self.requestInterruption()

    @property
    def _canceled(self) -> bool:
        """是否已请求取消（保留旧属性名，兼容既有调用与测试）。"""
        return self.isInterruptionRequested()

    def _emit_canceled(self) -> None:
        """统一取消出口：发 canceled，面板据此显示"已取消"而非"校准失败"。"""
        self.canceled.emit()
        self.result_ready.emit([])

    def run(self):
        try:
            self._do_calibrate()
        except Exception as e:
            logger.exception("配音校准失败")
            self.error.emit(f"配音校准失败: {e}")
            self.result_ready.emit([])

    def _do_calibrate(self):
        self.log.emit("开始校准配音字幕时间戳…")

        # 1. 解析基准字幕（配音时写出的偏移后字幕）
        self.progress.emit(1, 4, "解析基准字幕")
        shifted_srt = os.path.join(self._dub_dir, "dub_shifted.srt")
        if not os.path.exists(shifted_srt):
            raise RuntimeError(f"未找到基准字幕 {shifted_srt}，请先完成配音")
        entries = parse_srt(shifted_srt)
        if not entries:
            raise RuntimeError(f"基准字幕为空: {shifted_srt}")
        self.log.emit(f"基准字幕: {len(entries)} 条")

        # 2. 收集分段音频（排除 dub_full.wav）
        segment_wavs = sorted(
            (p for p in glob.glob(os.path.join(self._dub_dir, "dub_*.wav"))
             if _SEGMENT_RE.search(os.path.basename(p))),
            key=lambda p: int(_SEGMENT_RE.search(os.path.basename(p)).group(1)),
        )
        if len(segment_wavs) != len(entries):
            raise RuntimeError(
                f"配音分段数（{len(segment_wavs)}）与字幕条目数（{len(entries)}）不一致"
            )
        self.log.emit(f"已找到 {len(segment_wavs)} 个配音分段")

        if self._canceled:
            self._emit_canceled()
            return

        # 3. 片头归零：基准时间轴平移到 0 起点，段后间隔作为对齐先验
        self.progress.emit(2, 4, "读取分段时长")
        durations = [get_wav_duration(p) for p in segment_wavs]
        lead = entries[0].start_sec
        zeroed = [
            SubtitleEntry(e.index, e.start_sec - lead, e.end_sec - lead, e.text)
            for e in entries
        ]
        pauses = [
            zeroed[i + 1].start_sec - zeroed[i].end_sec
            for i in range(len(zeroed) - 1)
        ]

        if self._canceled:
            self._emit_canceled()
            return

        # 4. 对齐：在修改后音频中定位每个分段
        self.progress.emit(3, 4, "正在对齐音频…")
        texts = [e.text for e in entries]
        new_starts, scores = align_sentences(
            self._modified_wav_path, segment_wavs, texts, pauses,
        )
        # 失败判据与CalibrateWorker 统一走 is_matched，不再各自判断
        unreliable = [i + 1 for i in range(len(scores))
                       if not is_matched(new_starts[i], scores[i])]
        if len(unreliable) == len(entries):
            # 全部未匹配：说明音频里根本不含这些配音分段（常见原因是加载了
            # 视频原声/其他人声）。此时能量分割回退只会产出看似合理的垃圾，
            # 直接报错中止，不写文件。
            raise RuntimeError(
                "未能在该音频中匹配到任何配音分段。\n"
                "请确认加载的是【调整间隔后的配音音频】"
                "（dub_full.wav 或其编辑版本），而非视频原声或其他人声文件。"
            )
        if unreliable:
            shown = ", ".join(map(str, unreliable[:10]))
            more = "…" if len(unreliable) > 10 else ""
            self.log.emit(
                f"⚠ {len(unreliable)} 条未得到有效匹配"
                "（可能已删除、重排，或该句后来重新合成过——"
                "两遍 TTS 合成波形不同，无法匹配），"
                f"对应字幕将被移除: {shown}{more}"
            )

        if self._canceled:
            self._emit_canceled()
            return

        # 5. 重新映射时间戳（结果为修改后音频中的绝对时间）
        self.progress.emit(4, 4, "重新映射字幕时间戳")
        new_entries, dropped = recalibrate_entries(
            zeroed,
            [e.start_sec for e in zeroed],
            durations,
            new_starts,
        )
        if dropped:
            self.log.emit(
                f"  ⚠ {dropped} 条字幕因对应片段已不在音频中被移除"
            )

        # 6. 写出校准结果，不覆盖 dub_shifted.*
        outputs = []
        srt_path = os.path.join(self._dub_dir, "dub_calibrated.srt")
        with open(srt_path, "w", encoding="utf-8") as f:
            f.write(entries_to_srt(new_entries))
        outputs.append(srt_path)
        self.log.emit(f"  ✓ {srt_path}")

        if self._export_ass:
            ass_path = os.path.join(self._dub_dir, "dub_calibrated.ass")
            entries_to_ass(new_entries, ass_path)
            outputs.append(ass_path)
            self.log.emit(f"  ✓ {ass_path}")

        self.log.emit(f"校准完成: {len(new_entries)} 条字幕已重新映射")
        logger.info("配音校准完成: %s", outputs)
        self.result_ready.emit(new_entries)
