"""音色管理面板"""
import logging
import os
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QFileDialog, QFrame, QMessageBox,
    QListWidget, QListWidgetItem, QAbstractItemView,
    QSlider,
)
from PySide6.QtCore import Qt, QSize, Signal, QUrl, QEvent
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtGui import QDragEnterEvent, QDropEvent

from index_tts_gui.core.tts_client import BaseTTSClient, IndexTTSClient
from index_tts_gui.core.project import Project
from index_tts_gui.ui.voice_upload_worker import VoiceUploadWorker


logger = logging.getLogger("index_tts")


class _AudioListItem(QWidget):
    """参考音频列表项：文件名 + 试听 + 上传 + 移除。"""

    play_clicked = Signal(str)   # path
    upload_clicked = Signal(str) # path
    remove_clicked = Signal(str) # path

    def __init__(self, path: str, name: str, parent=None):
        super().__init__(parent)
        self._path = path
        row = QHBoxLayout(self)
        row.setContentsMargins(10, 8, 10, 8)
        row.setSpacing(8)

        label = QLabel(name)
        label.setStyleSheet("font-size: 18px; font-weight: bold;")
        row.addWidget(label, 1)

        btn_play = QPushButton("▶")
        btn_play.setFixedSize(40, 40)
        btn_play.setToolTip("试听")
        btn_play.setStyleSheet("QPushButton { border: none; background: #e3f2fd; color: #1976d2; font-size: 18px; border-radius: 8px; } QPushButton:hover { background: #bbdefb; }")
        btn_play.clicked.connect(lambda: self.play_clicked.emit(self._path))
        row.addWidget(btn_play)

        btn_upload = QPushButton("☁")
        btn_upload.setFixedSize(40, 40)
        btn_upload.setToolTip("上传到 API")
        btn_upload.setStyleSheet("QPushButton { border: none; background: #e8f5e9; color: #2e7d32; font-size: 18px; border-radius: 8px; } QPushButton:hover { background: #c8e6c9; }")
        btn_upload.clicked.connect(lambda: self.upload_clicked.emit(self._path))
        row.addWidget(btn_upload)

        btn_remove = QPushButton("✕")
        btn_remove.setFixedSize(40, 40)
        btn_remove.setToolTip("移除")
        btn_remove.setStyleSheet("QPushButton { border: none; background: #ffebee; color: #c62828; font-size: 18px; border-radius: 8px; } QPushButton:hover { background: #ffcdd2; }")
        btn_remove.clicked.connect(lambda: self.remove_clicked.emit(self._path))
        row.addWidget(btn_remove)


class VoicePanel(QWidget):
    """音色管理：拖放参考音频 + 试听 + 上传"""

    audio_uploaded = Signal(str)  # 上传成功后发射音频名
    segment_regenerate = Signal(int)  # 请求重新合成某句（index）
    voice_log = Signal(str)       # 日志消息（转发到合成面板）

    def __init__(self, project: Project, client: BaseTTSClient | None = None):
        super().__init__()
        self._project = project
        if client is None:
            try:
                client = IndexTTSClient()
            except ValueError as e:
                logger.warning("未配置 TTS API: %s", e)
                client = None
        self._client = client
        self._audio_path: str = ""
        self._audio_name: str = ""
        self._worker: VoiceUploadWorker | None = None
        self._player = QMediaPlayer()
        self._audio_output = QAudioOutput()
        self._player.setAudioOutput(self._audio_output)
        self._selected_segment_index = -1

        self._setup_ui()
        self._load_default_audio()
        self._restore_from_project()

    def _load_default_audio(self):
        """启动时默认加载根目录参考音频，不存在则加载项目目录下第一个 WAV。"""
        default = os.path.join(os.getcwd(), "作为愚人众的十一执行官.wav")
        if os.path.exists(default):
            self._load_audio(default)
            self._project.audio_name = os.path.basename(default)
            self._project.save()
            return

        if not self._project:
            return
        project_dir = self._project.project_dir
        try:
            wav_files = [
                f for f in os.listdir(project_dir)
                if f.lower().endswith(".wav") and os.path.isfile(os.path.join(project_dir, f))
            ]
        except Exception:
            wav_files = []
        if wav_files:
            self._load_audio(os.path.join(project_dir, sorted(wav_files)[0]))

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
        # 文件找不到时，清空路径并禁用上传，避免上传旧文件
        self._audio_path = ""
        self._audio_name = stored_name
        self._btn_play.setEnabled(False)
        self._btn_upload.setEnabled(False)

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)

        # 提示标签
        hint_label = QLabel("📋 参考音频（拖放 WAV 到下方列表）")
        hint_label.setStyleSheet("font-weight: bold; color: #555;")
        layout.addWidget(hint_label)

        # 左右分栏：每个列表下方有自己的变速控件
        lists_row = QHBoxLayout()
        lists_row.setSpacing(8)

        # 左侧：参考音频列表 + 变速面板
        ref_col = QVBoxLayout()
        ref_col.setSpacing(4)

        self._audio_list_widget = QListWidget()
        self._audio_list_widget.setAcceptDrops(True)
        self._audio_list_widget.setDragDropMode(QAbstractItemView.DragDropMode.NoDragDrop)
        self._audio_list_widget.setAlternatingRowColors(True)
        self._audio_list_widget.setSelectionMode(QAbstractItemView.SingleSelection)
        self._audio_list_widget.currentRowChanged.connect(self._on_audio_list_selected)
        self._audio_list_widget.itemPressed.connect(self._on_audio_item_clicked)
        self._audio_list_widget.setStyleSheet("""
            QListWidget {
                border: 2px dashed #bbb;
                border-radius: 6px;
                font-size: 13px;
                background: #fafafa;
            }
            QListWidget::item {
                padding: 4px 8px;
            }
            QListWidget::item:selected {
                background: #2979ff;
                color: white;
            }
        """)
        self._audio_list_widget.installEventFilter(self)
        ref_col.addWidget(self._audio_list_widget, 1)

        self._ref_speed_panel = QFrame()
        self._ref_speed_panel.setVisible(False)
        ref_speed_layout = QHBoxLayout(self._ref_speed_panel)
        ref_speed_layout.setContentsMargins(0, 0, 0, 0)
        ref_speed_layout.setSpacing(8)
        ref_speed_layout.addWidget(QLabel("速度:"))
        self._ref_speed_slider = QSlider(Qt.Horizontal)
        self._ref_speed_slider.setRange(50, 200)
        self._ref_speed_slider.setValue(100)
        self._ref_speed_slider.setFixedWidth(150)
        self._ref_speed_label = QLabel("1.0x")
        self._ref_speed_label.setFixedWidth(40)
        self._ref_speed_slider.valueChanged.connect(
            lambda v: self._ref_speed_label.setText(f"{v/100:.1f}x"))
        ref_speed_layout.addWidget(self._ref_speed_slider)
        ref_speed_layout.addWidget(self._ref_speed_label)
        self._ref_speed_btn = QPushButton("应用并生成新音频")
        self._ref_speed_btn.setStyleSheet("""
            QPushButton { background: #2196f3; color: white; padding: 4px 12px; border-radius: 4px; }
            QPushButton:hover { background: #1976d2; }
        """)
        self._ref_speed_btn.clicked.connect(self._apply_speed_to_reference)
        ref_speed_layout.addWidget(self._ref_speed_btn)
        ref_speed_layout.addStretch()
        ref_col.addWidget(self._ref_speed_panel)

        lists_row.addLayout(ref_col, 1)

        # 右侧：合成片段列表 + 变速面板
        seg_col = QVBoxLayout()
        seg_col.setSpacing(4)

        self._segment_list_widget = QListWidget()
        self._segment_list_widget.setAlternatingRowColors(True)
        self._segment_list_widget.setSelectionMode(QAbstractItemView.SingleSelection)
        self._segment_list_widget.itemDoubleClicked.connect(self._on_segment_double_clicked)
        self._segment_list_widget.itemSelectionChanged.connect(self._on_segment_selection_changed)
        self._segment_list_widget.setStyleSheet("""
            QListWidget {
                border: 1px solid #ddd;
                border-radius: 6px;
                font-size: 12px;
                background: white;
            }
            QListWidget::item {
                padding: 3px 8px;
            }
            QListWidget::item:selected {
                background: #fff3e0;
                color: #333;
            }
        """)
        seg_col.addWidget(self._segment_list_widget, 1)

        self._seg_speed_panel = QFrame()
        self._seg_speed_panel.setVisible(False)
        seg_speed_layout = QHBoxLayout(self._seg_speed_panel)
        seg_speed_layout.setContentsMargins(0, 0, 0, 0)
        seg_speed_layout.setSpacing(8)
        seg_speed_layout.addWidget(QLabel("速度:"))
        self._seg_speed_slider = QSlider(Qt.Horizontal)
        self._seg_speed_slider.setRange(50, 200)
        self._seg_speed_slider.setValue(100)
        self._seg_speed_slider.setFixedWidth(150)
        self._seg_speed_label = QLabel("1.0x")
        self._seg_speed_label.setFixedWidth(40)
        self._seg_speed_slider.valueChanged.connect(
            lambda v: self._seg_speed_label.setText(f"{v/100:.1f}x"))
        seg_speed_layout.addWidget(self._seg_speed_slider)
        seg_speed_layout.addWidget(self._seg_speed_label)
        self._seg_speed_btn = QPushButton("应用并替换原文件")
        self._seg_speed_btn.setStyleSheet("""
            QPushButton { background: #ff9800; color: white; padding: 4px 12px; border-radius: 4px; }
            QPushButton:hover { background: #f57c00; }
        """)
        self._seg_speed_btn.clicked.connect(self._apply_speed_to_segment)
        seg_speed_layout.addWidget(self._seg_speed_btn)
        seg_speed_layout.addStretch()
        seg_col.addWidget(self._seg_speed_panel)

        lists_row.addLayout(seg_col, 1)

        layout.addLayout(lists_row)

        # 播放状态
        self._play_status = QLabel("")
        self._play_status.setStyleSheet("color: #666; font-size: 12px;")
        layout.addWidget(self._play_status)

        # 隐藏的试听按钮（保持兼容，实际试听由列表内联按钮触发）
        self._btn_play = QPushButton()
        self._btn_play.hide()

        # 上传状态（上传按钮已移至合成面板控制栏）
        self._upload_status = QLabel("")
        self._upload_status.setStyleSheet("color: #666; font-size: 12px;")
        layout.addWidget(self._upload_status)

        # 上传按钮引用（由 synthesis_panel 控制）
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

        # 播放器事件
        self._player.playbackStateChanged.connect(self._on_playback_changed)
        self._player.errorOccurred.connect(self._on_play_error)

    def eventFilter(self, obj, event):
        """拦截参考音频列表的拖放和双击事件。"""
        if obj is self._audio_list_widget:
            if event.type() == QEvent.DragEnter:
                self._drag_enter(event)
                return True
            elif event.type() == QEvent.Drop:
                self._drop_event(event)
                return True
            elif event.type() == QEvent.MouseButtonDblClick:
                self._browse()
                return True
        return super().eventFilter(obj, event)

    def _drag_enter(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            urls = event.mimeData().urls()
            if urls and all(u.toLocalFile().lower().endswith('.wav') for u in urls):
                event.acceptProposedAction()
                event.accept()

    def _drop_event(self, event: QDropEvent):
        event.acceptProposedAction()
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if path.lower().endswith('.wav'):
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
        self._btn_play.setEnabled(True)
        self._btn_upload.setEnabled(True)
        self._upload_status.setText("")

        # 加载到播放器
        self._player.setSource(QUrl.fromLocalFile(path))

        # 加入列表（去重）
        self._add_to_audio_list(path, self._audio_name)

    # ── 参考音频列表管理 ──

    def _add_to_audio_list(self, path: str, name: str):
        """将音频加入列表（按文件名去重），同步到工程。"""
        # 去重：按文件名判断
        for i, entry in enumerate(self._project.audio_list):
            if entry.get("name") == name:
                # 同名但路径不同，更新为最新路径
                if entry.get("path") != path:
                    self._project.audio_list[i] = {"path": path, "name": name}
                    self._project.save()
                    self._refresh_audio_list_ui()
                for row in range(self._audio_list_widget.count()):
                    if self._audio_list_widget.item(row).data(Qt.UserRole) == path:
                        self._audio_list_widget.setCurrentRow(row)
                        return
                # 列表 UI 与数据不一致，刷新后重试
                self._refresh_audio_list_ui()
                return
        # 添加新条目
        self._project.audio_list.append({"path": path, "name": name})
        self._project.save()
        self._insert_audio_list_item(path, name)
        self._audio_list_widget.setCurrentRow(self._audio_list_widget.count() - 1)

    def _on_audio_item_clicked(self, item: QListWidgetItem):
        """点击列表项任意位置（含内联按钮）时确保选中并显示变速面板。"""
        row = self._audio_list_widget.row(item)
        self._audio_list_widget.setCurrentRow(row)

    def _on_audio_list_selected(self, row: int):
        """列表项被选中时切换音频。"""
        if row < 0:
            self._hide_speed_panels()
            return
        item = self._audio_list_widget.item(row)
        path = item.data(Qt.UserRole)
        if path and path != self._audio_path:
            self._load_audio_from_list(path)
        self._show_speed_panel_for_reference()

    def _load_audio_from_list(self, path: str):
        """从列表加载音频（不重复加入列表）。"""
        if not os.path.exists(path):
            # 文件不存在，从列表和工程中移除
            self._remove_audio_path(path)
            return
        self._audio_path = path
        self._audio_name = os.path.basename(path)
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
            self._insert_audio_list_item(path, name)
        self._audio_list_widget.blockSignals(False)

    def _insert_audio_list_item(self, path: str, name: str):
        """插入一个带内联按钮的列表项。"""
        widget = _AudioListItem(path, name)
        widget.play_clicked.connect(self._on_item_play)
        widget.upload_clicked.connect(self._on_item_upload)
        widget.remove_clicked.connect(self._on_item_remove)

        item = QListWidgetItem()
        item.setData(Qt.UserRole, path)
        item.setSizeHint(QSize(widget.sizeHint().width(), 56))
        self._audio_list_widget.addItem(item)
        self._audio_list_widget.setItemWidget(item, widget)

        if path == self._audio_path:
            self._audio_list_widget.setCurrentItem(item)

    # ── 变速控制 ──

    def _show_speed_panel_for_reference(self):
        self._ref_speed_panel.setVisible(True)
        self._ref_speed_slider.setValue(100)
        self._seg_speed_panel.setVisible(False)

    def _show_speed_panel_for_segment(self):
        self._seg_speed_panel.setVisible(True)
        self._seg_speed_slider.setValue(100)
        self._ref_speed_panel.setVisible(False)

    def _hide_speed_panels(self):
        self._ref_speed_panel.setVisible(False)
        self._seg_speed_panel.setVisible(False)

    def _apply_speed_to_reference(self):
        """对选中的参考音频变速，生成新文件加入列表。"""
        if not self._audio_path:
            return
        rate = self._ref_speed_slider.value() / 100.0
        if abs(rate - 1.0) < 0.01:
            return  # 1.0x 不处理
        from index_tts_gui.core.audio_speed import change_audio_speed
        base = os.path.splitext(self._audio_path)[0]
        out_path = f"{base}_{rate:.1f}x.wav"
        try:
            change_audio_speed(self._audio_path, out_path, rate)
            self._load_audio(out_path)
            self._log_msg(f"✅ 变速完成: {os.path.basename(out_path)} ({rate:.1f}x)")
        except RuntimeError as e:
            self._log_msg(f"✗ 变速失败: {e}")

    def _apply_speed_to_segment(self):
        """对选中的生成片段变速，直接覆盖原文件。"""
        if self._selected_segment_index < 0 or not self._project:
            return
        rate = self._seg_speed_slider.value() / 100.0
        if abs(rate - 1.0) < 0.01:
            return
        output_dir = self._project.output_dir
        # 查找对应 WAV
        target = None
        for f in os.listdir(output_dir):
            if f.startswith(f"sentence_{self._selected_segment_index + 1:02d}_") and f.endswith(".wav"):
                target = os.path.join(output_dir, f)
                break
        if not target:
            self._log_msg(f"⚠ 未找到第 {self._selected_segment_index + 1} 句的音频文件")
            return
        from index_tts_gui.core.audio_speed import change_audio_speed
        import tempfile
        try:
            # ffmpeg 不能直接输入输出同一文件，先写入临时文件再替换
            fd, tmp_path = tempfile.mkstemp(suffix=".wav", prefix="speed_", dir=output_dir)
            os.close(fd)
            try:
                change_audio_speed(target, tmp_path, rate)
                os.replace(tmp_path, target)
            except Exception:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
                raise
            self._log_msg(f"✅ 片段变速完成: {os.path.basename(target)} ({rate:.1f}x)")
        except RuntimeError as e:
            self._log_msg(f"✗ 变速失败: {e}")

    def _log_msg(self, msg: str):
        """发送日志到合成面板。"""
        self.voice_log.emit(msg)

    # ── 列表项操作 ──

    def _on_item_play(self, path: str):
        """列表项试听按钮：加载并播放。"""
        if path == self._audio_path:
            self._toggle_play()
        else:
            self._load_audio_from_list(path)
            self._toggle_play()

    def _on_item_upload(self, path: str):
        """列表项上传按钮：切换到该音频并上传。"""
        if path != self._audio_path:
            self._load_audio_from_list(path)
        self._upload()

    def _on_item_remove(self, path: str):
        """列表项移除按钮。"""
        self._remove_audio_path(path)

    def add_segment(self, index: int, filename: str):
        """合成完成一句后追加到右侧片段列表。

        index 为 1-based 句子序号，内部统一存储为 0-based。
        """
        item = QListWidgetItem(f"📄 {filename}")
        item.setData(Qt.UserRole, index - 1)  # 统一存储 0-based 索引
        item.setToolTip(f"第 {index} 句: {filename}\n双击预览，选中可重新生成")
        self._segment_list_widget.addItem(item)
        self._segment_list_widget.scrollToBottom()

    def _on_segment_selection_changed(self):
        """片段选中变化时，通知外部更新按钮状态。"""
        item = self._segment_list_widget.currentItem()
        if item:
            self._selected_segment_index = item.data(Qt.UserRole)
            self.segment_regenerate.emit(self._selected_segment_index)
            self._show_speed_panel_for_segment()
        else:
            self._selected_segment_index = -1
            self._hide_speed_panels()

    segment_preview = Signal(str)  # 请求预览某句（wav_path）

    def _on_segment_double_clicked(self, item: QListWidgetItem):
        """双击合成片段：预览该句音频。"""
        index = item.data(Qt.UserRole)
        if index is None or not self._project:
            return
        output_dir = self._project.output_dir
        # index 内部为 0-based，文件名为 1-based
        file_prefix = f"sentence_{index + 1:02d}_"
        for f in os.listdir(output_dir):
            if f.startswith(file_prefix) and f.endswith(".wav"):
                self.segment_preview.emit(os.path.join(output_dir, f))
                return

    def preview_segment(self, wav_path: str):
        """预览合成片段音频。"""
        if os.path.exists(wav_path):
            self._player.setSource(QUrl.fromLocalFile(wav_path))
            self._player.play()

    def clear_segments(self):
        """清空合成片段列表。"""
        self._segment_list_widget.clear()

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
        if self._client is None:
            self._upload_status.setText("⚠ 未配置 TTS API")
            self._upload_status.setStyleSheet("color: #d32f2f;")
            return
        if not self._audio_path:
            return

        if self._worker and self._worker.isRunning():
            return

        if self._worker is not None:
            try:
                self._worker.success.disconnect()
            except Exception:
                pass
            try:
                self._worker.error.disconnect()
            except Exception:
                pass
            try:
                self._worker.finished.disconnect()
            except Exception:
                pass
            self._worker.deleteLater()

        self._btn_upload.setEnabled(False)
        self._upload_status.setText("上传中…")
        self._upload_status.setStyleSheet("color: #f57c00;")

        self._worker = VoiceUploadWorker(
            self._client, self._audio_path, self._audio_name
        )
        self._worker.success.connect(self._on_upload_success)
        self._worker.error.connect(self._on_upload_error)
        self._worker.finished.connect(self._on_upload_finished)
        self._worker.finished.connect(self._worker.deleteLater)
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
        self._btn_play.setEnabled(False)
        self._btn_upload.setEnabled(False)
        self._upload_status.setText("")
        self._player.stop()
        self._player.setSource(QUrl())
        self._audio_list_widget.clear()
        self._segment_list_widget.clear()

    def get_audio_name(self) -> str:
        return self._audio_name
