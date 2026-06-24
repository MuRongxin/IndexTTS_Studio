"""合成 worker — 在后台线程调用 TTS API"""
import logging
import os
from PySide6.QtCore import QThread, Signal

from index_tts_gui.core.merger import sanitize_for_filename
from index_tts_gui.core.tts_client import TTSClient


logger = logging.getLogger("index_tts")


class SynthesisWorker(QThread):
    """后台合成线程"""

    progress = Signal(int, int, str)     # current, total, sentence_text
    sentence_done = Signal(int, str)     # index, wav_path
    finished = Signal(list)              # wav_map: list[dict]
    error = Signal(str)                  # 错误信息
    log = Signal(str)                    # 日志

    def __init__(self, sentences: list[str], audio_name: str,
                 output_dir: str, client: TTSClient):
        super().__init__()
        self._sentences = sentences
        self._audio_name = audio_name
        self._output_dir = output_dir
        self._client = client
        self._canceled = False
        self._wav_map: list[dict] = []

        os.makedirs(self._output_dir, exist_ok=True)

    def cancel(self):
        self._canceled = True

    def run(self):
        total = len(self._sentences)
        logger.info(
            "开始合成: total=%d audio_name=%s output_dir=%s",
            total, self._audio_name, self._output_dir,
        )
        self.log.emit(f"开始合成 {total} 句…")

        for i, sentence in enumerate(self._sentences, 1):
            if self._canceled:
                self.log.emit("已取消")
                logger.info("合成已取消，已完成 %d/%d", i - 1, total)
                break

            self.progress.emit(i, total, sentence)
            self.log.emit(f"[{i}/{total}] {sentence[:40]}...")
            logger.info("合成第 %d/%d 句: %s", i, total, sentence[:80])

            try:
                audio_bytes = self._client.synthesize(
                    sentence, self._audio_name
                )
                text_part = sanitize_for_filename(sentence)
                wav_path = os.path.join(
                    self._output_dir, f"sentence_{i:02d}_{text_part}.wav"
                )
                with open(wav_path, "wb") as f:
                    f.write(audio_bytes)

                logger.info(
                    "合成成功: %s size=%d bytes",
                    os.path.basename(wav_path), len(audio_bytes)
                )
                self.sentence_done.emit(i, wav_path)
                self._wav_map.append({
                    "index": i - 1,  # 0-based
                    "text": sentence,
                    "wav": os.path.basename(wav_path),
                })
                self.log.emit(f"  ✓ {os.path.basename(wav_path)} ({len(audio_bytes)} bytes)")

            except Exception as e:
                logger.exception("合成第 %d 句失败", i)
                self.log.emit(f"  ✗ 第 {i} 句失败: {e}")
                self.error.emit(f"第 {i} 句合成失败: {e}")
                # 继续下一句

        if not self._canceled:
            logger.info("合成完成: 共 %d 句", total)
            self.log.emit(f"合成完成！共 {total} 句")
            self.log.emit(f"📝 写入 WAV 映射: {len(self._wav_map)} 条")
        self.finished.emit(self._wav_map)
