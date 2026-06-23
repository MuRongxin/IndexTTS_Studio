"""合成 worker — 在后台线程调用 TTS API"""
import os
from PySide6.QtCore import QThread, Signal

from index_tts_gui.core.tts_client import TTSClient


class SynthesisWorker(QThread):
    """后台合成线程"""

    progress = Signal(int, int, str)     # current, total, sentence_text
    sentence_done = Signal(int, str)     # index, wav_path
    finished = Signal()                  # 全部完成
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

        os.makedirs(self._output_dir, exist_ok=True)

    def cancel(self):
        self._canceled = True

    def run(self):
        total = len(self._sentences)
        self.log.emit(f"开始合成 {total} 句…")

        for i, sentence in enumerate(self._sentences, 1):
            if self._canceled:
                self.log.emit("已取消")
                break

            self.progress.emit(i, total, sentence)
            self.log.emit(f"[{i}/{total}] {sentence[:40]}...")

            try:
                audio_bytes = self._client.synthesize(
                    sentence, self._audio_name
                )
                wav_path = os.path.join(
                    self._output_dir, f"sentence_{i:02d}.wav"
                )
                with open(wav_path, "wb") as f:
                    f.write(audio_bytes)

                self.sentence_done.emit(i, wav_path)
                self.log.emit(f"  ✓ sentence_{i:02d}.wav ({len(audio_bytes)} bytes)")

            except Exception as e:
                self.log.emit(f"  ✗ 第 {i} 句失败: {e}")
                self.error.emit(f"第 {i} 句合成失败: {e}")
                # 继续下一句

        if not self._canceled:
            self.log.emit(f"合成完成！共 {total} 句")
        self.finished.emit()
