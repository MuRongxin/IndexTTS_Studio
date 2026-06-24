"""音色管理面板"""
import os
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QFileDialog, QFrame, QMessageBox,
    QListWidget, QListWidgetItem, QAbstractItemView,
)
from PySide6.QtCore import Qt, Signal, QUrl
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtGui import QDragEnterEvent, QDropEvent

from index_tts_gui.core.tts_client import BaseTTSClient, IndexTTSClient
from index_tts_gui.core.project import Project
from index_tts_gui.ui.voice_upload_worker import VoiceUploadWorker


class VoicePanel(QWidget):
    """音色管理：拖放参考音频 + 试听 + 上传"""

    audio_uploaded = Signal(str)  # 上传成功后发射音频名

    def __init__(self, project: Project, client: BaseTTSClient | None = None):
        super().__init__()
        self._project = project
        self._client = client or IndexTTSClient()
        self._audio_path: str = ""
        self._audio_name: str = ""
        self._worker: VoiceUploadWorker | None = None
        self._player = QMediaPlayer()
        self._audio_output = QAudioOutput()
        self._player.setAudioOutput(self._audio_output)

        self._setup_ui()
        self._load_default_audio()
        self._restore_from_project()

    def _load_default_audio(self):
        """启动时默认加载项目目录下的参考音频。"""
        default = "作为愚人众的十一执行官.wav"
        if os.path.exists(default):
            self._load_audio(default)

    def _restore_from_project(self):
        """从工程恢复已记录的音频列表和当前选择。"""
        if not self._project:
            return

        # 刷新列表 UI
        self._refresh_audio_list_ui()

        if not self._project.audio_name:
            return
        stored_name = self._project.audio_name
        # 如果已加载的就是工程记录的音频，不需要恢复
        if self._audio_name == stored_name and self._audio_path:
            return
        # 优先从 audio_list 中找匹配路径
        for entry in self._project.audio_list:
            if entry.get("name") == stored_name and os.path.exists(entry.get("path", "")):
                self._load_audio_from_list(entry["path"])
                return
        # 尝试在常见位置查找
        candidates = [
            os.path.join(os.getcwd(), stored_name),
            os.path.join(self._project.project_dir, stored_name),
            os.path.join(self._project.output_dir, stored_name),
        ]
        for path in candidates:
            if os.path.exists(path):
                self._load_audio(path)
                return
        # 文件找不到时，至少记录名称并启用上传状态提示
        self._audio_name = stored_name
        self._file_label.setText(f"📁 {stored_name} (文件缺失)")
        self._btn_upload.setEnabled(True)

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

        # 参考音频列表
        list_label = QLabel("📋 参考音频列表:")
        list_label.setStyleSheet("font-weight: bold; margin-top: 8px;")
        layout.addWidget(list_label)

        self._audio_list_widget = QListWidget()
        self._audio_list_widget.setMaximumHeight(120)
        self._audio_list_widget.setAlternatingRowColors(True)
        self._audio_list_widget.setSelectionMode(QAbstractItemView.SingleSelection)
        self._audio_list_widget.currentRowChanged.connect(self._on_audio_list_selected)
        self._audio_list_widget.setStyleSheet("""
            QListWidget {
                border: 1px solid #ddd;
                border-radius: 4px;
                font-size: 13px;
            }
            QListWidget::item {
                padding: 4px 8px;
            }
            QListWidget::item:selected {
                background: #2979ff;
                color: white;
            }
        """)
        layout.addWidget(self._audio_list_widget)

        # 列表操作按钮
        list_btn_row = QHBoxLayout()
        self._btn_remove_audio = QPushButton("🗑 移除选中")
        self._btn_remove_audio.setToolTip("从列表中移除选中的参考音频")
        self._btn_remove_audio.clicked.connect(self._remove_selected_audio)
        self._btn_remove_audio.setStyleSheet("""
            QPushButton {
                background: #757575; color: white;
                padding: 2px 10px; border-radius: 3px;
                font-size: 12px;
            }
            QPushButton:hover { background: #d32f2f; }
        """)
        list_btn_row.addWidget(self._btn_remove_audio)
        list_btn_row.addStretch()
        layout.addLayout(list_btn_row)

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

        # 加入列表（去重）
        self._add_to_audio_list(path, self._audio_name)

    # ── 参考音频列表管理 ──

    def _add_to_audio_list(self, path: str, name: str):
        """将音频加入列表（去重），同步到工程。"""
        # 去重：检查路径
        for entry in self._project.audio_list:
            if entry.get("path") == path:
                # 已存在，选中它
                for i in range(self._audio_list_widget.count()):
                    if self._audio_list_widget.item(i).data(Qt.UserRole) == path:
                        self._audio_list_widget.setCurrentRow(i)
                        return
        # 添加新条目
        self._project.audio_list.append({"path": path, "name": name})
        self._project.save()

        item = QListWidgetItem(f"🎵 {name}")
        item.setData(Qt.UserRole, path)
        item.setToolTip(path)
        self._audio_list_widget.addItem(item)
        self._audio_list_widget.setCurrentRow(self._audio_list_widget.count() - 1)

    def _on_audio_list_selected(self, row: int):
        """列表项被选中时切换音频。"""
        if row < 0:
            return
        item = self._audio_list_widget.item(row)
        path = item.data(Qt.UserRole)
        if path and path != self._audio_path:
            self._load_audio_from_list(path)

    def _load_audio_from_list(self, path: str):
        """从列表加载音频（不重复加入列表）。"""
        if not os.path.exists(path):
            # 文件不存在，从列表和工程中移除
            self._remove_audio_path(path)
            return
        self._audio_path = path
        self._audio_name = os.path.basename(path)
        self._file_label.setText(f"📁 {self._audio_name}")
        self._btn_play.setEnabled(True)
        self._btn_upload.setEnabled(True)
        self._upload_status.setText("")
        self._player.setSource(QUrl.fromLocalFile(path))
        # 同步到工程
        self._project.audio_name = self._audio_name
        self._project.save()

    def _remove_selected_audio(self):
        """移除列表中选中的音频。"""
        row = self._audio_list_widget.currentRow()
        if row < 0:
            return
        item = self._audio_list_widget.item(row)
        path = item.data(Qt.UserRole)
        self._remove_audio_path(path)

    def _remove_audio_path(self, path: str):
        """从列表和工程中移除指定路径的音频。"""
        # 从工程移除
        self._project.audio_list = [
            e for e in self._project.audio_list if e.get("path") != path
        ]
        self._project.save()
        # 从列表控件移除
        for i in range(self._audio_list_widget.count()):
            if self._audio_list_widget.item(i).data(Qt.UserRole) == path:
                self._audio_list_widget.takeItem(i)
                break
        # 如果当前正在使用被删除的音频，清除状态
        if self._audio_path == path:
            self._audio_path = ""
            self._audio_name = ""
            self._file_label.setText("未选择音频")
            self._btn_play.setEnabled(False)
            self._btn_upload.setEnabled(False)
            self._upload_status.setText("")
            self._player.stop()

    def _refresh_audio_list_ui(self):
        """从工程数据刷新列表 UI（切换工程时调用）。"""
        self._audio_list_widget.blockSignals(True)
        self._audio_list_widget.clear()
        for entry in self._project.audio_list:
            path = entry.get("path", "")
            name = entry.get("name", os.path.basename(path))
            if not os.path.exists(path):
                continue
            item = QListWidgetItem(f"🎵 {name}")
            item.setData(Qt.UserRole, path)
            item.setToolTip(path)
            self._audio_list_widget.addItem(item)
            # 选中当前使用的音频
            if path == self._audio_path:
                self._audio_list_widget.setCurrentItem(item)
        self._audio_list_widget.blockSignals(False)

    # ── 播放控制 ──

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

    def set_project(self, project: Project):
        """切换工程时更新引用（默认音频保留在项目根目录）。"""
        self._project = project
        self._restore_from_project()

    def reset_for_new_project(self):
        """新建工程时清空已选音频状态（不删除项目默认音频文件）。"""
        self._audio_path = ""
        self._audio_name = ""
        self._file_label.setText("未选择音频")
        self._btn_play.setEnabled(False)
        self._btn_upload.setEnabled(False)
        self._upload_status.setText("")
        self._player.stop()
        self._player.setSource(QUrl())
        self._audio_list_widget.clear()

    def get_audio_name(self) -> str:
        return self._audio_name
