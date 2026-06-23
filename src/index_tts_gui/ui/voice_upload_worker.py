"""参考音频上传 worker：避免上传阻塞 GUI。"""
import logging

from PySide6.QtCore import QThread, Signal

from index_tts_gui.core.tts_client import BaseTTSClient


logger = logging.getLogger("index_tts")


class VoiceUploadWorker(QThread):
    """后台上传参考音频到 TTS API。"""

    started = Signal()
    success = Signal(str)   # audio_name
    error = Signal(str)     # error message
    finished = Signal()

    def __init__(
        self,
        client: BaseTTSClient,
        audio_path: str,
        audio_name: str,
        parent=None,
    ):
        super().__init__(parent)
        self._client = client
        self._audio_path = audio_path
        self._audio_name = audio_name

    def run(self):
        self.started.emit()
        try:
            logger.info("开始上传参考音频: %s", self._audio_name)
            result = self._client.upload_audio(self._audio_path)
            if result.get("code") == 200:
                logger.info("参考音频上传成功: %s", self._audio_name)
                self.success.emit(self._audio_name)
            else:
                msg = result.get("msg", "未知错误")
                logger.error("上传失败: %s", msg)
                self.error.emit(f"上传失败: {msg}")
        except Exception as e:
            logger.exception("上传参考音频失败")
            self.error.emit(f"上传失败: {e}")
        finally:
            self.finished.emit()
