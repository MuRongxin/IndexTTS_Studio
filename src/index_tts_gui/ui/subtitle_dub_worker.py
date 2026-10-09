"""字幕配音 worker — 后台线程：逐条合成 → 取时长 → 偏移规划 → 拼接 → 导出字幕"""
import glob
import logging
import os
import re

from PySide6.QtCore import QThread, Signal

from index_tts_gui.core.dub_planner import build_track_pauses, plan_dub_timeline
from index_tts_gui.core.io_ass import entries_to_ass
from index_tts_gui.core.merger import get_wav_duration, merge_wavs_with_custom_pauses
from index_tts_gui.core.subtitle import SubtitleEntry
from index_tts_gui.core.subtitler import entries_to_srt
from index_tts_gui.core.tts_client import BaseTTSClient
from index_tts_gui.core.fingerprint import (
    compute_file as compute_fingerprint,
    save as save_fingerprints,
)


logger = logging.getLogger("index_tts")

# 只匹配分段文件 dub_001.wav，排除拼接产物 dub_full.wav
_SEGMENT_RE = re.compile(r"dub_(\d+)\.wav$")


class SubtitleDubWorker(QThread):
    """后台配音线程：合成每句 → 规划偏移时间轴 → 拼接完整 WAV → 导出偏移字幕。"""

    progress = Signal(int, int, str)     # current, total, sentence_text
    sentence_done = Signal(int)          # 1-based 条目序号
    # 任务结果信号。不能叫 finished：那会遮蔽 QThread 内置的线程退出信号
    result_ready = Signal(list)          # 输出文件路径列表（失败/取消时为空列表）
    error = Signal(str)                  # 错误信息
    log = Signal(str)                    # 日志
    canceled = Signal()                  # 用户主动取消（与失败区分开）

    def __init__(
        self,
        entries: list[SubtitleEntry],
        audio_name: str,
        dub_dir: str,
        client: BaseTTSClient,
        export_ass: bool = False,
    ):
        super().__init__()
        self._entries = entries
        self._audio_name = audio_name
        self._dub_dir = dub_dir
        self._client = client
        self._export_ass = export_ass

    def cancel(self):
        """请求取消。用 Qt 的 interruption 机制，逐阶段检查。"""
        self.requestInterruption()

    @property
    def _canceled(self) -> bool:
        """是否已请求取消（保留旧属性名，兼容既有调用与测试）。"""
        return self.isInterruptionRequested()

    def _emit_canceled(self) -> None:
        """统一的取消出口：发 canceled 而非 result_ready([])。

        之前取消也走 result_ready([])，面板把它渲染成"配音失败，
        无输出"，用户分不清是自己取消的还是出错了。
        """
        self.canceled.emit()
        self.result_ready.emit([])

    def run(self):
        total = len(self._entries)
        logger.info(
            "开始配音: total=%d audio_name=%s dub_dir=%s export_ass=%s",
            total, self._audio_name, self._dub_dir, self._export_ass,
        )
        self.log.emit(f"开始配音 {total} 条…")
        os.makedirs(self._dub_dir, exist_ok=True)

        # 清理上次运行残留的分段文件，避免旧片段混入本次拼接。
        # 只匹配 dub_NNN.wav 分段：glob "dub_*.wav" 会把上一次的
        # dub_full.wav 也删掉，若本次失败/取消，旧成品将不可恢复。
        for stale in glob.glob(os.path.join(self._dub_dir, "dub_*.wav")):
            if not _SEGMENT_RE.search(os.path.basename(stale)):
                continue
            try:
                os.remove(stale)
            except Exception:
                pass

        wav_paths: list[str] = []
        try:
            for i, entry in enumerate(self._entries, 1):
                if self._canceled:
                    self.log.emit("已取消")
                    logger.info("配音已取消，已完成 %d/%d", i - 1, total)
                    self._emit_canceled()
                    return

                text = entry.text.strip()
                if not text:
                    # 面板加载时已过滤空文本，这里仅兜底；跳过会导致片段与
                    # 条目错位，直接中止
                    self.log.emit(f"[{i}/{total}] 空文本条目，中止")
                    self.error.emit(f"第 {i} 条字幕文本为空")
                    self.result_ready.emit([])
                    return

                self.progress.emit(i, total, text)
                self.log.emit(f"[{i}/{total}] {text[:40]}")
                logger.info("配音合成第 %d/%d 条: %s", i, total, text[:80])

                try:
                    audio_bytes = self._client.synthesize(text, self._audio_name)
                except Exception as e:
                    logger.exception("配音合成第 %d 条失败", i)
                    msg = f"第 {i} 条合成失败: {e}"
                    # 同时发 log：面板的 _on_error 会把 msg 写进日志面板，
                    # 只发 error 会让失败原因在界面上完全看不到
                    self.log.emit(f"  ✗ {msg}")
                    self.error.emit(msg)
                    self.result_ready.emit([])
                    return

                wav_path = os.path.join(self._dub_dir, f"dub_{i:03d}.wav")
                with open(wav_path, "wb") as f:
                    f.write(audio_bytes)
                wav_paths.append(wav_path)
                logger.info(
                    "配音合成成功: %s size=%d bytes",
                    os.path.basename(wav_path), len(audio_bytes)
                )
                self.sentence_done.emit(i)
                self.log.emit(f"  ✓ {os.path.basename(wav_path)} ({len(audio_bytes)} bytes)")

            if self._canceled:
                self.log.emit("已取消")
                self._emit_canceled()
                return

            # 取每段实际时长。ffprobe 每段一次、每段最长 30s 超时，
            # 取消必须在这里也能被响应，否则按"停止"会毫无反应。
            self.log.emit("📏 读取各段实际时长…")
            durations = []
            for n_done, p in enumerate(wav_paths, 1):
                if self._canceled:
                    self.log.emit("已取消")
                    self._emit_canceled()
                    return
                durations.append(get_wav_duration(p))
                self.progress.emit(n_done, len(wav_paths), os.path.basename(p))

            # 偏移规划（影响最小原则：只顺延被顶到的块）
            new_entries = plan_dub_timeline(self._entries, durations)
            pauses = build_track_pauses(new_entries)
            logger.info("偏移规划完成: entries=%d pauses=%s", len(new_entries), pauses)

            # 按静音拼接完整配音
            if self._canceled:
                self.log.emit("已取消")
                self._emit_canceled()
                return
            self.log.emit("🔀 按偏移时间轴拼接完整配音…")
            full_path = os.path.join(self._dub_dir, "dub_full.wav")
            merge_wavs_with_custom_pauses(
                wav_paths, pauses, full_path,
                leading=True,
                on_progress=lambda cur, tot, msg: self.progress.emit(
                    cur, tot, msg
                ),
            )
            self.log.emit(f"  ✓ {full_path}")

            # 导出偏移后的字幕
            outputs = [full_path]
            srt_path = os.path.join(self._dub_dir, "dub_shifted.srt")
            with open(srt_path, "w", encoding="utf-8") as f:
                f.write(entries_to_srt(new_entries))
            outputs.append(srt_path)
            self.log.emit(f"  ✓ {srt_path}")

            if self._export_ass:
                ass_path = os.path.join(self._dub_dir, "dub_shifted.ass")
                entries_to_ass(new_entries, ass_path)
                outputs.append(ass_path)
                self.log.emit(f"  ✓ {ass_path}")

            # 声学指纹：描述刚被拼进 dub_full.wav 的分段，之后某段被
            # 重新配音时仍能靠它定位
            try:
                fps = {i: f for i, f in
                       ((n, compute_fingerprint(p))
                        for n, p in enumerate(wav_paths, 1))
                       if f}
                if fps:
                    save_fingerprints(self._dub_dir, fps)
                    self.log.emit(f"✓ 已保存声学指纹: {len(fps)} 条")
            except Exception as e:
                logger.warning("保存声学指纹失败: %s", e)

            logger.info("配音完成: %s", outputs)
            self.log.emit(f"配音完成！共 {len(outputs)} 个输出文件")
            self.result_ready.emit(outputs)

        except Exception as e:
            logger.exception("配音任务失败")
            msg = f"配音任务失败: {e}"
            self.log.emit(f"✗ {msg}")
            self.error.emit(msg)
            self.result_ready.emit([])
