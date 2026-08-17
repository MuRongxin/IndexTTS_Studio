"""字幕配音 worker — 后台线程：逐条合成 → 取时长 → 偏移规划 → 拼接 → 导出字幕"""
import glob
import logging
import os
import tempfile

from PySide6.QtCore import QThread, Signal

from index_tts_gui.core.dub_planner import build_track_pauses, plan_dub_timeline
from index_tts_gui.core.io_ass import entries_to_ass
from index_tts_gui.core.merger import _generate_silence, get_wav_duration, merge_wavs
from index_tts_gui.core.subtitle import SubtitleEntry
from index_tts_gui.core.subtitler import entries_to_srt
from index_tts_gui.core.tts_client import BaseTTSClient


logger = logging.getLogger("index_tts")


class SubtitleDubWorker(QThread):
    """后台配音线程：合成每句 → 规划偏移时间轴 → 拼接完整 WAV → 导出偏移字幕。"""

    progress = Signal(int, int, str)     # current, total, sentence_text
    sentence_done = Signal(int)          # 1-based 条目序号
    finished = Signal(list)              # 输出文件路径列表（失败/取消时为空列表）
    error = Signal(str)                  # 错误信息
    log = Signal(str)                    # 日志

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
        self._canceled = False

    def cancel(self):
        self._canceled = True

    def run(self):
        total = len(self._entries)
        logger.info(
            "开始配音: total=%d audio_name=%s dub_dir=%s export_ass=%s",
            total, self._audio_name, self._dub_dir, self._export_ass,
        )
        self.log.emit(f"开始配音 {total} 条…")
        os.makedirs(self._dub_dir, exist_ok=True)

        # 清理上次运行残留的片段，避免旧文件混入本次拼接
        for stale in glob.glob(os.path.join(self._dub_dir, "dub_*.wav")):
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
                    self.finished.emit([])
                    return

                text = entry.text.strip()
                if not text:
                    # 面板加载时已过滤空文本，这里仅兜底；跳过会导致片段与
                    # 条目错位，直接中止
                    self.log.emit(f"[{i}/{total}] 空文本条目，中止")
                    self.error.emit(f"第 {i} 条字幕文本为空")
                    self.finished.emit([])
                    return

                self.progress.emit(i, total, text)
                self.log.emit(f"[{i}/{total}] {text[:40]}")
                logger.info("配音合成第 %d/%d 条: %s", i, total, text[:80])

                try:
                    audio_bytes = self._client.synthesize(text, self._audio_name)
                except Exception as e:
                    logger.exception("配音合成第 %d 条失败", i)
                    self.log.emit(f"  ✗ 第 {i} 条合成失败: {e}")
                    self.error.emit(f"第 {i} 条合成失败: {e}")
                    self.finished.emit([])
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
                self.finished.emit([])
                return

            # 取每段实际时长
            self.log.emit("📏 读取各段实际时长…")
            durations = [get_wav_duration(p) for p in wav_paths]

            # 偏移规划（影响最小原则：只顺延被顶到的块）
            new_entries = plan_dub_timeline(self._entries, durations)
            pauses = build_track_pauses(new_entries)
            logger.info("偏移规划完成: entries=%d pauses=%s", len(new_entries), pauses)

            # 按静音拼接完整配音
            self.log.emit("🔀 按偏移时间轴拼接完整配音…")
            full_path = os.path.join(self._dub_dir, "dub_full.wav")
            self._concat_with_leading_pauses(wav_paths, pauses, full_path)
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

            logger.info("配音完成: %s", outputs)
            self.log.emit(f"配音完成！共 {len(outputs)} 个输出文件")
            self.finished.emit(outputs)

        except Exception as e:
            logger.exception("配音任务失败")
            self.log.emit(f"✗ 配音任务失败: {e}")
            self.error.emit(f"配音任务失败: {e}")
            self.finished.emit([])

    def _concat_with_leading_pauses(
        self, wav_paths: list[str], pauses: list[float], output_path: str
    ) -> None:
        """把 wav_paths 与每段前的静音（pauses）交替拼成 output_path。"""
        if not wav_paths:
            raise RuntimeError("没有可拼接的配音片段")
        with tempfile.TemporaryDirectory(prefix="dub_concat_") as tmpdir:
            items: list[str] = []
            for i, wav in enumerate(wav_paths):
                pause = pauses[i] if i < len(pauses) else 0.0
                if pause > 0.001:
                    silence_path = os.path.join(tmpdir, f"silence_{i:04d}.wav")
                    logger.debug("生成静音: index=%d duration=%.2f", i, pause)
                    _generate_silence(pause, wav, silence_path)
                    items.append(silence_path)
                items.append(wav)
            merge_wavs(items, output_path)
