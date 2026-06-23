"""合成控制面板"""
import os
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QProgressBar, QPlainTextEdit, QLabel, QFileDialog,
    QGroupBox, QSpinBox,
)
from PySide6.QtCore import Qt, Signal

from index_tts_gui.core.tts_client import BaseTTSClient, IndexTTSClient
from index_tts_gui.ui.synthesis_worker import SynthesisWorker
from index_tts_gui.ui.voice_panel import VoicePanel


class SynthesisPanel(QWidget):
    """合成控制：音色选择 + 进度条 + 日志 + 启停"""

    synthesis_done = Signal(str)  # 合成完成，输出目录路径

    def __init__(self, client: BaseTTSClient | None = None):
        super().__init__()
        self._client = client or IndexTTSClient()
        self._worker: SynthesisWorker | None = None
        self._sentences: list[str] = []
        self._audio_name: str = ""
        self._output_dir: str = "output_tts"
        self._was_canceled = False

        self._voice_panel = VoicePanel(self._client)
        self._voice_panel.audio_uploaded.connect(self.set_audio_name)

        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # ── 音色选择区（嵌入 VoicePanel）──
        layout.addWidget(self._voice_panel)

        # 设置区
        gb = QGroupBox("合成设置")
        gb_layout = QHBoxLayout(gb)

        gb_layout.addWidget(QLabel("输出目录:"))
        self._dir_label = QLabel("output_tts/")
        self._dir_label.setStyleSheet("font-weight: bold;")
        gb_layout.addWidget(self._dir_label)

        btn_dir = QPushButton("…")
        btn_dir.setFixedWidth(30)
        btn_dir.clicked.connect(self._choose_dir)
        gb_layout.addWidget(btn_dir)

        gb_layout.addStretch()
        layout.addWidget(gb)

        # 进度条
        self._progress = QProgressBar()
        self._progress.setMinimum(0)
        self._progress.setMaximum(100)
        self._progress.setValue(0)
        layout.addWidget(self._progress)

        self._status_label = QLabel("等待开始…")
        layout.addWidget(self._status_label)

        # 控制按钮
        ctrl = QHBoxLayout()
        self._btn_start = QPushButton("▶ 开始合成")
        self._btn_start.setStyleSheet("""
            QPushButton {
                background: #2979ff; color: white;
                padding: 10px 24px; border-radius: 6px;
                font-size: 14px; font-weight: bold;
            }
            QPushButton:hover { background: #1565c0; }
            QPushButton:disabled { background: #ccc; }
        """)
        self._btn_start.clicked.connect(self._start)
        ctrl.addWidget(self._btn_start)

        self._btn_stop = QPushButton("⏹ 停止")
        self._btn_stop.setEnabled(False)
        self._btn_stop.setStyleSheet("""
            QPushButton {
                background: #d32f2f; color: white;
                padding: 10px 24px; border-radius: 6px;
                font-size: 14px; font-weight: bold;
            }
            QPushButton:hover { background: #b71c1c; }
            QPushButton:disabled { background: #ccc; }
        """)
        self._btn_stop.clicked.connect(self._stop)
        ctrl.addWidget(self._btn_stop)
        ctrl.addStretch()
        layout.addLayout(ctrl)

        # 日志
        log_label = QLabel("日志:")
        log_label.setStyleSheet("font-weight: bold; margin-top: 8px;")
        layout.addWidget(log_label)

        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        self._log.setMaximumBlockCount(500)
        self._log.setStyleSheet("background: #1e1e1e; color: #d4d4d4; font-family: monospace;")
        layout.addWidget(self._log)

    def set_sentences(self, sentences: list[str]):
        self._sentences = sentences
        self._progress.setMaximum(len(sentences))

    def set_audio_name(self, name: str):
        self._audio_name = name

    def _choose_dir(self):
        path = QFileDialog.getExistingDirectory(self, "选择输出目录")
        if path:
            self._output_dir = path
            self._dir_label.setText(path)

    def _start(self):
        if not self._sentences:
            self._log_msg("⚠ 请先在文稿面板拆分句子")
            return
        if not self._audio_name:
            self._log_msg("⚠ 请先在音色面板上传参考音频")
            return

        self._was_canceled = False
        self._progress.setValue(0)
        self._btn_start.setEnabled(False)
        self._btn_stop.setEnabled(True)
        self._log.clear()

        self._worker = SynthesisWorker(
            self._sentences, self._audio_name,
            self._output_dir, self._client,
        )
        self._worker.progress.connect(self._on_progress)
        self._worker.sentence_done.connect(self._on_sentence_done)
        self._worker.log.connect(self._log_msg)
        self._worker.error.connect(self._log_msg)
        self._worker.finished.connect(self._on_finished)
        self._worker.start()

    def _stop(self):
        if self._worker and self._worker.isRunning():
            self._was_canceled = True
            self._worker.cancel()
            self._log_msg("正在停止…")

    def _on_progress(self, current, total, text):
        self._progress.setValue(current)
        self._status_label.setText(f"合成中 [{current}/{total}]: {text[:40]}...")

    def _on_sentence_done(self, index, path):
        pass

    def _on_finished(self):
        self._btn_start.setEnabled(True)
        self._btn_stop.setEnabled(False)

        if self._was_canceled:
            self._status_label.setText("已停止")
            self._log_msg("━━━━━━━━━━ 已取消 ━━━━━━━━━━")
            return

        self._progress.setValue(self._progress.maximum())
        self._status_label.setText("合成完成 ✓")
        self._log_msg("━━━━━━━━━━ 完成 ━━━━━━━━━━")
        self.synthesis_done.emit(self._output_dir)

    def _log_msg(self, msg: str):
        self._log.appendPlainText(msg)

    def set_client(self, client: BaseTTSClient):
        """外部（如 MainWindow）动态切换 API 客户端。"""
        self._client = client
        self._voice_panel.set_client(client)

    def get_output_dir(self) -> str:
        return self._output_dir
