"""后台音频波形加载 worker"""
from __future__ import annotations

import logging

import numpy as np
from PySide6.QtCore import QThread, Signal

from index_tts_gui.ui.audio_engine import AudioEngine


logger = logging.getLogger("index_tts")


class AudioLoadWorker(QThread):
    """在后台线程加载音频波形，避免阻塞主线程 UI。"""

    loaded = Signal(str, float, int, object)  # filepath, duration, sample_rate, waveform
    failed = Signal(str, str)                 # filepath, error_message

    def __init__(self, filepath: str):
        super().__init__()
        self._filepath = filepath

    def run(self):
        logger.info("开始后台加载音频波形: %s", self._filepath)
        try:
            engine = AudioEngine()
            ok = engine.load_audio(self._filepath)
            if ok and engine.is_loaded():
                logger.info(
                    "音频波形加载完成: %s duration=%.2fs sr=%d samples=%d",
                    self._filepath,
                    engine.duration,
                    engine.sample_rate,
                    engine.waveform.shape[0],
                )
                self.loaded.emit(
                    self._filepath,
                    engine.duration,
                    engine.sample_rate,
                    engine.waveform,
                )
            else:
                msg = "无法识别音频格式或文件为空"
                logger.warning("音频加载失败: %s - %s", self._filepath, msg)
                self.failed.emit(self._filepath, msg)
        except Exception as e:
            logger.exception("后台加载音频失败: %s", self._filepath)
            self.failed.emit(self._filepath, str(e))
