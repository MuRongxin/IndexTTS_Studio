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
from index_tts_gui.ui.theme import Theme


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
        self._file_label.setText(f"{stored_name} (文件缺失)")
        self._btn_upload.setEnabled(True)

    def _card(self, title: str = "") -> QFrame:
        """创建一个现代卡片容器。"""
        card = QFrame()
        c = Theme.colors
        r = Theme.radius
        card.setStyleSheet(f"""
            QFrame {{
                background: {c.surface};
                border: 1px solid {c.border};
                border-radius: {r.md}px;
            }}
        """)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        if title:
            lbl = QLabel(title)
            lbl.setStyleSheet(
                f"font-size: {Theme.fonts.size_lg}px; font-weight: 700; color: {c.text_primary};"
            )
            layout.addWidget(lbl)
        return card

    def _setup_ui(self):
        c = Theme.colors
        r = Theme.radius
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)

        # ── 拖放区卡片 ──
        drop_card = self._card()
        drop_card_layout = drop_card.layout()

        self._drop_frame = QFrame()
        self._drop_frame.setAcceptDrops(True)
        self._drop_frame.setMinimumHeight(140)
        self._drop_frame.setStyleSheet(f"""
            QFrame {{
                background: {c.bg};
                border: 2px dashed {c.border};
                border-radius: {r.md}px;
            }}
            QFrame:hover {{
                border-color: {c.primary};
                background: {c.primary_light};
            }}
        """)
        self._drop_frame.dragEnterEvent = self._drag_enter
        self._drop_frame.dropEvent = self._drop_event

        drop_layout = QVBoxLayout(self._drop_frame)
        self._drop_label = QLabel("拖放 WAV 参考音频到这里\n推荐 3~15 秒")
        self._drop_label.setAlignment(Qt.AlignCenter)
        self._drop_label.setStyleSheet(
            f"border: none; color: {c.text_secondary}; font-size: {Theme.fonts.size_md}px;"
        )
        drop_layout.addWidget(self._drop_label)
        drop_card_layout.addWidget(self._drop_frame)

        # 已选文件信息
        info_layout = QHBoxLayout()
        self._file_label = QLabel("未选择音频")
        self._file_label.setStyleSheet(
            f"font-weight: 600; color: {c.text_primary};"
        )
        info_layout.addWidget(self._file_label)
        info_layout.addStretch()

        btn_browse = QPushButton("浏览文件")
        btn_browse.setCursor(Qt.PointingHandCursor)
        btn_browse.clicked.connect(self._browse)
        info_layout.addWidget(btn_browse)
        drop_card_layout.addLayout(info_layout)

        # 播放与上传
        ctrl_layout = QHBoxLayout()
        self._btn_play = QPushButton("试听")
        self._btn_play.setEnabled(False)
        self._btn_play.setCursor(Qt.PointingHandCursor)
        self._btn_play.clicked.connect(self._toggle_play)
        ctrl_layout.addWidget(self._btn_play)

        self._play_status = QLabel("")
        self._play_status.setStyleSheet(f"color: {c.text_secondary};")
        ctrl_layout.addWidget(self._play_status)
        ctrl_layout.addStretch()

        self._btn_upload = QPushButton("上传到 API")
        self._btn_upload.setEnabled(False)
        self._btn_upload.setProperty("variant", "primary")
        self._btn_upload.setCursor(Qt.PointingHandCursor)
        self._btn_upload.clicked.connect(self._upload)
        ctrl_layout.addWidget(self._btn_upload)

        drop_card_layout.addLayout(ctrl_layout)

        self._upload_status = QLabel("")
        self._upload_status.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        drop_card_layout.addWidget(self._upload_status)

        layout.addWidget(drop_card)

        # ── 参考音频列表卡片 ──
        list_card = self._card("参考音频列表")
        list_card_layout = list_card.layout()

        self._audio_list_widget = QListWidget()
        self._audio_list_widget.setMaximumHeight(140)
        self._audio_list_widget.setAlternatingRowColors(True)
        self._audio_list_widget.setSelectionMode(QAbstractItemView.SingleSelection)
        self._audio_list_widget.currentRowChanged.connect(self._on_audio_list_selected)
        self._audio_list_widget.setStyleSheet(f"""
            QListWidget {{
                border: 1px solid {c.border};
                border-radius: {r.sm}px;
                font-size: {Theme.fonts.size_sm}px;
                background: {c.surface};
            }}
            QListWidget::item {{
                padding: 8px 12px;
                border-bottom: 1px solid {c.border};
            }}
            QListWidget::item:selected {{
                background: {c.primary_light};
                color: {c.primary};
            }}
        """)
        list_card_layout.addWidget(self._audio_list_widget)

        list_btn_row = QHBoxLayout()
        self._btn_remove_audio = QPushButton("移除选中")
        self._btn_remove_audio.setToolTip("从列表中移除选中的参考音频")
        self._btn_remove_audio.setProperty("variant", "ghost")
        self._btn_remove_audio.setCursor(Qt.PointingHandCursor)
        self._btn_remove_audio.clicked.connect(self._remove_selected_audio)
        list_btn_row.addWidget(self._btn_remove_audio)
        list_btn_row.addStretch()
        list_card_layout.addLayout(list_btn_row)

        layout.addWidget(list_card)
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
        self._file_label.setText(self._audio_name)
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

        item = QListWidgetItem(name)
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
            item = QListWidgetItem(name)
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
            self._btn_play.setText("暂停")
            self._play_status.setText("播放中…")
        else:
            self._btn_play.setText("试听")
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
        self._upload_status.setText("上传成功")
        self._upload_status.setStyleSheet(
            f"color: {Theme.colors.success}; font-weight: 600;"
        )
        self.audio_uploaded.emit(audio_name)

    def _on_upload_error(self, msg: str):
        self._upload_status.setText(msg)
        self._upload_status.setStyleSheet(
            f"color: {Theme.colors.error}; font-weight: 600;"
        )

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
