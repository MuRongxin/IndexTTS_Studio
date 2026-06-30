"""字幕重新对齐 worker — 音频指纹匹配，自动更新字幕时间轴。"""
import logging
from PySide6.QtCore import QThread, Signal

from index_tts_gui.core.subtitle import SubtitleEntry
from index_tts_gui.core.audio_fingerprint import match_fingerprint


logger = logging.getLogger("index_tts")


class SubtitleRealignWorker(QThread):
    """后台线程：逐条匹配字幕指纹到目标音频。"""

    progress = Signal(int, int, float)   # current, total, best_correlation
    log = Signal(str)                    # 日志
    finished = Signal(list)              # updated_entries
    entry_matched = Signal(int, float)   # entry_index, new_start_sec

    def __init__(self, entries: list[SubtitleEntry], target_wav: str):
        super().__init__()
        self._entries = entries
        self._target_wav = target_wav

    def run(self):
        total = len(self._entries)
        matched = 0
        updated = []

        for i, e in enumerate(self._entries):
            if e.fingerprint is None or len(e.fingerprint) == 0:
                updated.append(e)
                self.log.emit(f"⏭ 句{i+1}: 无指纹，跳过")
                continue

            # 搜索范围：原时间 ±30 秒
            search_start = max(0.0, e.start_sec - 30)
            search_end = e.end_sec + 30

            result = match_fingerprint(
                e.fingerprint,
                self._target_wav,
                search_start=search_start,
                search_end=search_end,
                min_correlation=0.6,
            )

            if result is not None:
                new_start, corr = result
                dur = e.end_sec - e.start_sec
                e.start_sec = round(new_start, 3)
                e.end_sec = round(new_start + dur, 3)
                matched += 1
                self.log.emit(
                    f"✅ 句{i+1}: {e.start_sec:.2f}s (置信度 {corr:.2f})"
                )
                self.progress.emit(i + 1, total, corr)
                self.entry_matched.emit(i, e.start_sec)
            else:
                self.log.emit(
                    f"⚠ 句{i+1}: 匹配失败，保持原位 {e.start_sec:.2f}s"
                )
                self.progress.emit(i + 1, total, -1)

            updated.append(e)

        self.log.emit(f"━━━━━ 完成：{matched}/{total} 成功 ━━━━━")
        self.finished.emit(updated)
