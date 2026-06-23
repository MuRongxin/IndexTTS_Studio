"""音色管理面板"""
import os
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QFileDialog, QFrame, QMessageBox,
)
from PySide6.QtCore import Qt, Signal, QUrl
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtGui import QDragEnterEvent, QDropEvent

from index_tts_gui.core.tts_client import BaseTTSClient, IndexTTSClient
from index_tts_gui.ui.voice_upload_worker import VoiceUploadWorker


class VoicePanel(QWidget):
    """音色管理：拖放参考音频 + 试听 + 上传"""

    audio_uploaded = Signal(str)  # 上传成功后发射音频名

    def __init__(self, client: BaseTTSClient | None = None):
        super().__init__()
        self._client = client or IndexTTSClient()
        self._audio_path: str = ""
        self._audio_name: str = ""
        self._worker: VoiceUploadWorker | None = None
        self._player = QMediaPlayer()
        self._audio_output = QAudioOutput()
        self._player.setAudioOutput(self._audio_output)

        self._setup_ui()
        self._load_default_audio()

    def _load_default_audio(self):
        """启动时默认加载项目目录下的参考音频。"""
        default = "作为愚人众的十一执行官.wav"
        if os.path.exists(default):
            self._load_audio(default)

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)

        # 拖放区
        self._drop_frame = QFrame()
        self._drop_frame.setAcceptDrops(True)
        self._drop_frame.setFrameStyle(QFrame.StyledPanel | QFrame.Sunken)
        self._drop_frame.setMinimumHeight(120)
        self._drop_frame.setStyleSheet("""
            QFrame {
                background: #fafafa;
                border: 2px dashed #bbb;
                border-radius: 8px;
            }
            QFrame:hover {
                border-color: #2979ff;
                background: #f0f5ff;
            }
        """)
        self._drop_frame.dragEnterEvent = self._drag_enter
        self._drop_frame.dropEvent = self._drop_event

        drop_layout = QVBoxLayout(self._drop_frame)
        self._drop_label = QLabel("拖放参考音频到这里\n（WAV 格式，3~15 秒最佳）")
        self._drop_label.setAlignment(Qt.AlignCenter)
        self._drop_label.setStyleSheet("border: none; color: #888;")
        drop_layout.addWidget(self._drop_label)

        layout.addWidget(self._drop_frame)

        # 已选文件信息
        info_layout = QHBoxLayout()
        self._file_label = QLabel("未选择音频")
        self._file_label.setStyleSheet("font-weight: bold;")
        info_layout.addWidget(self._file_label)
        info_layout.addStretch()

        btn_browse = QPushButton("📁 浏览")
        btn_browse.clicked.connect(self._browse)
        info_layout.addWidget(btn_browse)
        layout.addLayout(info_layout)

        # 播放控制
        ctrl_layout = QHBoxLayout()
        self._btn_play = QPushButton("▶ 试听")
        self._btn_play.setEnabled(False)
        self._btn_play.clicked.connect(self._toggle_play)
        ctrl_layout.addWidget(self._btn_play)

        self._play_status = QLabel("")
        ctrl_layout.addWidget(self._play_status)
        ctrl_layout.addStretch()

        layout.addLayout(ctrl_layout)

        # 上传区
        upload_layout = QHBoxLayout()
        self._btn_upload = QPushButton("☁ 上传到 API")
        self._btn_upload.setEnabled(False)
        self._btn_upload.setStyleSheet("""
            QPushButton {
                background: #4caf50; color: white;
                padding: 8px 20px; border-radius: 4px;
                font-weight: bold;
            }
            QPushButton:hover { background: #388e3c; }
            QPushButton:disabled { background: #ccc; }
        """)
        self._btn_upload.clicked.connect(self._upload)
        upload_layout.addWidget(self._btn_upload)

        self._upload_status = QLabel("")
        upload_layout.addWidget(self._upload_status)
        upload_layout.addStretch()
        layout.addLayout(upload_layout)

        layout.addStretch()

        # 播放器事件
        self._player.playbackStateChanged.connect(self._on_playback_changed)
        self._player.errorOccurred.connect(self._on_play_error)

    def _drag_enter(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            url = event.mimeData().urls()[0]
            if url.toLocalFile().lower().endswith('.wav'):
                event.acceptProposedAction()

    def _drop_event(self, event: QDropEvent):
        path = event.mimeData().urls()[0].toLocalFile()
        self._load_audio(path)

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择参考音频", "", "WAV 文件 (*.wav);;所有文件 (*)"
        )
        if path:
            self._load_audio(path)

    def _load_audio(self, path: str):
        if not os.path.exists(path):
            return
        self._audio_path = path
        self._audio_name = os.path.basename(path)
        self._file_label.setText(f"📁 {self._audio_name}")
        self._btn_play.setEnabled(True)
        self._btn_upload.setEnabled(True)
        self._upload_status.setText("")

        # 加载到播放器
        self._player.setSource(QUrl.fromLocalFile(path))

    def _toggle_play(self):
        if self._player.playbackState() == QMediaPlayer.PlayingState:
            self._player.pause()
        else:
            self._player.play()

    def _on_playback_changed(self, state):
        if state == QMediaPlayer.PlayingState:
            self._btn_play.setText("⏸ 暂停")
            self._play_status.setText("播放中…")
        else:
            self._btn_play.setText("▶ 试听")
            self._play_status.setText("")

    def _on_play_error(self, error, error_str):
        self._play_status.setText(f"播放失败: {error_str}")

    def _upload(self):
        if not self._audio_path:
            return

        if self._worker and self._worker.isRunning():
            return

        self._btn_upload.setEnabled(False)
        self._upload_status.setText("上传中…")
        self._upload_status.setStyleSheet("color: #f57c00;")

        self._worker = VoiceUploadWorker(
            self._client, self._audio_path, self._audio_name
        )
        self._worker.success.connect(self._on_upload_success)
        self._worker.error.connect(self._on_upload_error)
        self._worker.finished.connect(self._on_upload_finished)
        self._worker.start()

    def _on_upload_success(self, audio_name: str):
        self._upload_status.setText("✓ 上传成功")
        self._upload_status.setStyleSheet("color: #4caf50; font-weight: bold;")
        self.audio_uploaded.emit(audio_name)

    def _on_upload_error(self, msg: str):
        self._upload_status.setText(f"✗ {msg}")
        self._upload_status.setStyleSheet("color: #d32f2f;")

    def _on_upload_finished(self):
        self._btn_upload.setEnabled(True)

    def set_client(self, client: BaseTTSClient):
        """外部（如 MainWindow）动态切换 API 客户端。"""
        self._client = client

    def get_audio_name(self) -> str:
        return self._audio_name
