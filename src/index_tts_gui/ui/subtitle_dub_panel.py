"""🎬 配音面板 — 导入外部字幕，逐条合成并按偏移时间轴拼接"""
import logging
import os

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from index_tts_gui.core.io_subtitle import parse_subtitle_file
from index_tts_gui.core.project import Project
from index_tts_gui.core.subtitle import SubtitleEntry, seconds_to_time_str
from index_tts_gui.core.tts_client import BaseTTSClient
from index_tts_gui.ui.dub_calibrate_worker import DubCalibrateWorker
from index_tts_gui.ui.subtitle_dub_worker import SubtitleDubWorker
from index_tts_gui.ui.voice_upload_worker import VoiceUploadWorker


logger = logging.getLogger("index_tts")


class SubtitleDubPanel(QWidget):
    """配音页：字幕文件 → 音色 → 只读预览 → 开始/取消 → 输出路径。"""

    def __init__(self, project: Project, client: BaseTTSClient | None = None):
        super().__init__()
        self._project = project
        self._client = client
        self._entries: list[SubtitleEntry] = []
        self._input_path: str = ""
        self._input_ext: str = ""
        # 页面级临时音色：优先于 project.audio_name，不写回工程文件
        self._temp_audio_path: str = ""
        self._temp_audio_name: str = ""
        self._worker: SubtitleDubWorker | None = None
        self._upload_worker: VoiceUploadWorker | None = None
        self._calibrate_worker: DubCalibrateWorker | None = None
        self._was_canceled = False

        self._player = QMediaPlayer()
        self._audio_output = QAudioOutput()
        self._player.setAudioOutput(self._audio_output)

        self._setup_ui()
        self._refresh_voice_label()
        self._refresh_start_button()
        self._refresh_calibrate_button()

    # ── UI ──

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # ── 文件区 ──
        file_gb = QGroupBox("字幕文件")
        file_layout = QVBoxLayout(file_gb)
        file_row = QHBoxLayout()
        self._btn_choose_file = QPushButton("📂 选择字幕文件")
        self._btn_choose_file.clicked.connect(self._choose_subtitle_file)
        file_row.addWidget(self._btn_choose_file)
        self._file_label = QLabel("未选择文件")
        self._file_label.setStyleSheet("font-weight: bold;")
        file_row.addWidget(self._file_label, 1)
        file_layout.addLayout(file_row)
        self._parse_label = QLabel("尚未解析字幕（支持 .srt / .ass）")
        self._parse_label.setStyleSheet("color: #666;")
        file_layout.addWidget(self._parse_label)
        layout.addWidget(file_gb)

        # ── 音色区 ──
        voice_gb = QGroupBox("音色")
        voice_layout = QHBoxLayout(voice_gb)
        voice_layout.addWidget(QLabel("当前音色:"))
        self._voice_label = QLabel("")
        self._voice_label.setStyleSheet("font-weight: bold; color: #2979ff;")
        voice_layout.addWidget(self._voice_label, 1)

        self._btn_choose_voice = QPushButton("🎵 更换音色")
        self._btn_choose_voice.setToolTip("选择参考音频并上传为页面级临时音色（不改写工程保存值）")
        self._btn_choose_voice.clicked.connect(self._choose_voice)
        voice_layout.addWidget(self._btn_choose_voice)

        self._btn_preview_voice = QPushButton("▶ 试听")
        self._btn_preview_voice.setEnabled(False)
        self._btn_preview_voice.clicked.connect(self._preview_temp_voice)
        voice_layout.addWidget(self._btn_preview_voice)

        self._btn_use_project_voice = QPushButton("↺ 用回项目音色")
        self._btn_use_project_voice.setToolTip("清除页面级临时音色，恢复使用工程保存的音色")
        self._btn_use_project_voice.clicked.connect(self._use_project_voice)
        voice_layout.addWidget(self._btn_use_project_voice)
        layout.addWidget(voice_gb)

        # ── 预览表格 ──
        self._table_gb = QGroupBox("字幕预览（只读）")
        table_layout = QVBoxLayout(self._table_gb)
        self._table = QTableWidget(0, 5)
        self._table.setHorizontalHeaderLabels(
            ["序号", "原始开始", "原始结束", "文本", "合成状态"]
        )
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setAlternatingRowColors(True)
        header = self._table.horizontalHeader()
        for col in (0, 1, 2, 4):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        table_layout.addWidget(self._table)
        layout.addWidget(self._table_gb, 1)

        # ── 控制区 ──
        self._progress = QProgressBar()
        self._progress.setMinimum(0)
        self._progress.setMaximum(100)
        self._progress.setValue(0)
        layout.addWidget(self._progress)

        self._status_label = QLabel("等待开始…")
        layout.addWidget(self._status_label)

        ctrl = QHBoxLayout()
        self._btn_start = QPushButton("▶ 开始配音")
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

        self._btn_stop = QPushButton("⏹ 取消")
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

        self._btn_calibrate = QPushButton("🔄 校准字幕")
        self._btn_calibrate.setToolTip(
            "加载调整间隔后的配音音频，反向校准字幕时间戳（需先完成一次配音）"
        )
        self._btn_calibrate.setEnabled(False)
        self._btn_calibrate.clicked.connect(self._calibrate)
        ctrl.addWidget(self._btn_calibrate)

        self._start_hint = QLabel("")
        self._start_hint.setStyleSheet("color: #d32f2f;")
        ctrl.addWidget(self._start_hint)
        ctrl.addStretch()
        layout.addLayout(ctrl)

        log_label = QLabel("日志:")
        log_label.setStyleSheet("font-weight: bold; margin-top: 8px;")
        layout.addWidget(log_label)

        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        self._log.setMaximumBlockCount(500)
        self._log.setMaximumHeight(120)
        self._log.setStyleSheet(
            "background: #1e1e1e; color: #d4d4d4; font-family: monospace;"
        )
        layout.addWidget(self._log)

        self._outputs_label = QLabel("输出文件将在完成后显示在这里")
        self._outputs_label.setWordWrap(True)
        self._outputs_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._outputs_label.setStyleSheet("color: #2e7d32;")
        layout.addWidget(self._outputs_label)

    # ── 字幕文件 ──

    def _choose_subtitle_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择字幕文件", "",
            "字幕文件 (*.srt *.ass);;所有文件 (*)"
        )
        if path:
            self._load_subtitle(path)

    def _load_subtitle(self, path: str):
        try:
            entries = parse_subtitle_file(path)
        except Exception as e:
            self._log_msg(f"✗ 解析失败: {e}")
            return

        kept: list[SubtitleEntry] = []
        for e in entries:
            if e.end_sec <= e.start_sec:
                self._log_msg(f"⚠ 跳过时长为 0 的条目 #{e.index}: {e.text[:30]}")
                continue
            kept.append(e)
        # 重排 1-based 序号
        for i, e in enumerate(kept, 1):
            e.index = i

        self._entries = kept
        self._input_path = path
        self._input_ext = os.path.splitext(path)[1].lower()
        self._file_label.setText(os.path.basename(path))

        total = max((e.end_sec for e in kept), default=0.0)
        self._parse_label.setText(
            f"解析成功：{len(kept)} 条，总时长 {seconds_to_time_str(total)}"
        )
        self._log_msg(f"📄 已加载字幕: {os.path.basename(path)}（{len(kept)} 条）")
        self._populate_table()
        self._refresh_start_button()

    def _populate_table(self):
        self._table_gb.setTitle("字幕预览（只读）")
        self._table.setRowCount(0)
        self._table.setRowCount(len(self._entries))
        for row, e in enumerate(self._entries):
            values = [
                str(e.index),
                seconds_to_time_str(e.start_sec),
                seconds_to_time_str(e.end_sec),
                e.text.replace("\n", " "),
                "待合成",
            ]
            for col, text in enumerate(values):
                item = QTableWidgetItem(text)
                if col in (0, 1, 2, 4):
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self._table.setItem(row, col, item)

    def _reset_status_cells(self):
        for row in range(self._table.rowCount()):
            item = self._table.item(row, 4)
            if item is not None:
                item.setText("待合成")
                item.setForeground(QBrush())

    # ── 音色 ──

    def _choose_voice(self):
        # 无上传能力的 provider（如 index_tts2）：直接填服务器端音色名/路径
        if self._client is not None and not getattr(
            self._client, "supports_upload", True
        ):
            from PySide6.QtWidgets import QInputDialog
            name, ok = QInputDialog.getText(
                self, "服务器音色",
                "该 API 不支持上传参考音频。\n"
                "请输入服务器端音色文件名/路径（如 demo_boy.wav）:"
            )
            if not ok or not name.strip():
                return
            self._temp_audio_name = name.strip()
            self._temp_audio_path = ""  # 服务器端音色，无本地文件可试听
            self._btn_preview_voice.setEnabled(False)
            self._refresh_voice_label()
            self._log_msg(f"✓ 已设置页面服务器音色: {self._temp_audio_name}（临时）")
            self._refresh_start_button()
            return

        path, _ = QFileDialog.getOpenFileName(
            self, "选择参考音频", "", "WAV 文件 (*.wav);;所有文件 (*)"
        )
        if not path:
            return
        self._temp_audio_path = path
        self._temp_audio_name = ""  # 上传成功后才生效
        self._btn_preview_voice.setEnabled(True)
        self._voice_label.setText(f"页面音色: {os.path.basename(path)}（上传中…）")
        self._load_player_source(path)

        if self._client is None:
            self._log_msg("⚠ 未配置 TTS API，无法上传音色，请先在「设置」中配置")
            self._refresh_start_button()
            return

        if self._upload_worker is not None and self._upload_worker.isRunning():
            self._log_msg("⚠ 正在上传其他音色，请稍候")
            return

        if self._upload_worker is not None:
            self._disconnect_upload_worker(self._upload_worker)
            self._upload_worker.deleteLater()
            self._upload_worker = None

        self._log_msg(f"☁ 正在上传参考音频: {os.path.basename(path)}…")
        self._upload_worker = VoiceUploadWorker(
            self._client, path, os.path.basename(path)
        )
        self._upload_worker.success.connect(self._on_upload_success)
        self._upload_worker.error.connect(self._on_upload_error)
        self._upload_worker.result_ready.connect(self._on_upload_finished)
        # 生命周期挂 finished：run() 抛异常时 result_ready 不会发
        self._upload_worker.finished.connect(self._on_upload_finished)
        self._upload_worker.start()
        self._refresh_start_button()

    def _on_upload_success(self, audio_name: str):
        self._temp_audio_name = audio_name
        self._voice_label.setText(f"页面音色: {audio_name}（临时）")
        self._log_msg(f"✓ 音色上传成功: {audio_name}（页面级临时音色，不影响工程保存值）")
        self._refresh_start_button()

    def _on_upload_error(self, msg: str):
        self._log_msg(f"✗ 音色上传失败: {msg}")

    def _on_upload_finished(self):
        worker = self._upload_worker
        self._upload_worker = None
        if worker is not None:
            try:
                worker.deleteLater()
            except RuntimeError:
                pass
        self._refresh_start_button()

    def _preview_temp_voice(self):
        if self._temp_audio_path and os.path.exists(self._temp_audio_path):
            self._player.stop()
            self._player.setSource(QUrl())
            self._player.setSource(QUrl.fromLocalFile(self._temp_audio_path))
            self._player.play()

    def _load_player_source(self, path: str):
        self._player.stop()
        self._player.setSource(QUrl())
        self._player.setSource(QUrl.fromLocalFile(path))

    def _use_project_voice(self):
        self._temp_audio_path = ""
        self._temp_audio_name = ""
        self._btn_preview_voice.setEnabled(False)
        self._player.stop()
        self._player.setSource(QUrl())
        self._log_msg("↺ 已清除页面临时音色，恢复使用项目音色")
        self._refresh_voice_label()
        self._refresh_start_button()

    def _get_audio_name(self) -> str:
        if self._temp_audio_name:
            return self._temp_audio_name
        return self._project.audio_name or ""

    def _refresh_voice_label(self):
        if self._temp_audio_name:
            self._voice_label.setText(f"页面音色: {self._temp_audio_name}（临时）")
        elif self._project.audio_name:
            self._voice_label.setText(f"项目音色: {self._project.audio_name}")
        else:
            self._voice_label.setText("未设置音色")

    def _refresh_start_button(self):
        running = self._worker is not None and self._worker.isRunning()
        if running:
            self._btn_start.setEnabled(False)
            self._start_hint.setText("")
            return
        if self._client is None:
            self._btn_start.setEnabled(False)
            self._start_hint.setText("未配置 TTS API（请在「设置」中填写）")
            return
        if not self._entries:
            self._btn_start.setEnabled(False)
            self._start_hint.setText("请先选择字幕文件")
            return
        if not self._get_audio_name():
            self._btn_start.setEnabled(False)
            self._start_hint.setText("未设置音色（更换音色或使用项目音色）")
            return
        self._btn_start.setEnabled(True)
        self._start_hint.setText("")

    # ── 校准 ──

    def _refresh_calibrate_button(self):
        running = self._calibrate_worker is not None and self._calibrate_worker.isRunning()
        dubbing = self._worker is not None and self._worker.isRunning()
        if running:
            self._btn_calibrate.setEnabled(False)
            self._btn_calibrate.setText("🔄 校准中…")
            return
        self._btn_calibrate.setText("🔄 校准字幕")
        shifted_srt = os.path.join(
            self._project.output_dir, "dub", "dub_shifted.srt"
        )
        self._btn_calibrate.setEnabled(
            not dubbing and os.path.exists(shifted_srt)
        )

    def _calibrate(self):
        if self._calibrate_worker is not None and self._calibrate_worker.isRunning():
            self._log_msg("⚠ 已有校准任务在运行")
            return

        dub_dir = os.path.join(self._project.output_dir, "dub")
        path, _ = QFileDialog.getOpenFileName(
            self, "选择调整间隔后的配音音频", dub_dir,
            "音频文件 (*.wav *.mp3 *.flac)"
        )
        if not path:
            return

        # 是否导出 ASS 以配音产物为准（工程切换/重启后状态可能丢失）
        export_ass = os.path.exists(os.path.join(dub_dir, "dub_shifted.ass"))
        self._calibrate_worker = DubCalibrateWorker(
            path, dub_dir, export_ass=export_ass,
        )
        self._calibrate_worker.log.connect(self._log_msg)
        self._calibrate_worker.progress.connect(self._on_calibrate_progress)
        self._calibrate_worker.error.connect(self._on_calibrate_error)
        self._calibrate_worker.canceled.connect(self._on_calibrate_canceled)
        self._calibrate_worker.result_ready.connect(self._on_calibrate_finished)
        self._calibrate_worker.finished.connect(
            self._on_calibrate_lifetime_finished
        )
        self._log_msg(f"🔄 开始校准: {os.path.basename(path)}")
        self._calibrate_worker.start()
        self._refresh_calibrate_button()

    def _on_calibrate_progress(self, current: int, total: int, stage: str):
        self._progress.setMaximum(total)
        self._progress.setValue(current)
        self._status_label.setText(f"校准中 [{current}/{total}]: {stage}")

    def _on_calibrate_finished(self, entries: list):
        if not entries:
            self._status_label.setText("校准失败，无输出（详见日志）")
            return
        prev_count = len(self._entries)

        # 必须无条件把校准结果写回面板状态。
        # 丢行是校准的正常结果（对应片段已不在音频里），原先只在
        # "条数恰好相等"时才刷新表格，于是界面仍显示校准前的时间戳、
        # 却宣布"校准完成"，而且之后再点"开始配音"会用旧条目重配。
        self._entries = list(entries)
        self._populate_table()
        self._table_gb.setTitle("字幕预览（校准后）")
        dropped = prev_count - len(self._entries)
        if dropped > 0:
            self._log_msg(f"  ⚠ {dropped} 条字幕因对应片段已不在音频中被移除")

        dub_dir = os.path.join(self._project.output_dir, "dub")
        outputs = [
            os.path.join(dub_dir, name)
            for name in ("dub_calibrated.srt", "dub_calibrated.ass")
            if os.path.exists(os.path.join(dub_dir, name))
        ]
        self._status_label.setText("校准完成 ✓")
        self._outputs_label.setText("输出文件:\n" + "\n".join(outputs))
        self._log_msg("━━━━━━━━━━ 校准完成 ━━━━━━━━━━")
        for p in outputs:
            self._log_msg(f"📦 {p}")

    def _on_calibrate_error(self, msg: str):
        self._status_label.setText("校准失败")
        self._log_msg(f"✗ {msg}")

    def _on_calibrate_canceled(self):
        """用户主动取消校准：按钮状态必须恢复，否则校准永久置灰。"""
        self._log_msg("已取消校准")
        self._status_label.setText("已取消")

    def _on_calibrate_lifetime_finished(self):
        """校准 worker 生命周期结束，安全清理引用。"""
        worker = self._calibrate_worker
        self._calibrate_worker = None
        if worker is not None:
            try:
                worker.deleteLater()
            except RuntimeError:
                pass
        self._refresh_calibrate_button()

    # ── 开始 / 取消 ──

    def _start(self):
        if self._worker is not None and self._worker.isRunning():
            self._log_msg("⚠ 已有配音任务在运行")
            return
        if self._client is None:
            self._log_msg("⚠ 未配置 TTS API，请在左侧「设置」中填写 API URL")
            return
        if not self._entries:
            self._log_msg("⚠ 请先选择并解析字幕文件")
            return
        audio_name = self._get_audio_name()
        if not audio_name:
            self._log_msg("⚠ 未设置音色：请更换音色或使用项目音色")
            return

        self._was_canceled = False
        self._progress.setMaximum(len(self._entries))
        self._progress.setValue(0)
        self._outputs_label.setText("")
        self._reset_status_cells()
        self._btn_start.setEnabled(False)
        self._btn_stop.setEnabled(True)
        self._log.clear()
        self._refresh_calibrate_button()

        dub_dir = os.path.join(self._project.output_dir, "dub")
        export_ass = self._input_ext in (".ass", ".ssa")
        self._worker = SubtitleDubWorker(
            self._entries, audio_name, dub_dir, self._client,
            export_ass=export_ass,
        )
        self._worker.setProperty("project_dir", self._project.project_dir)
        self._worker.progress.connect(self._on_progress)
        self._worker.sentence_done.connect(self._on_sentence_done)
        self._worker.log.connect(self._log_msg)
        self._worker.error.connect(self._on_error)
        self._worker.canceled.connect(self._on_dub_canceled)
        self._worker.result_ready.connect(self._on_finished)
        # 生命周期挂 finished：run() 抛异常时 result_ready 不会发，
        # 挂在它上面会让"开始配音"永久禁用
        self._worker.finished.connect(self._on_worker_lifetime_finished)
        self._worker.start()

    def _stop(self):
        if self._worker and self._worker.isRunning():
            self._was_canceled = True
            self._worker.cancel()
            self._log_msg("正在取消…")
            # 立即禁用：否则用户无法区分"取消没生效"和"正在收尾"
            self._btn_stop.setEnabled(False)

    def _on_progress(self, current: int, total: int, text: str):
        self._progress.setMaximum(total)
        self._progress.setValue(current)
        self._status_label.setText(f"配音中 [{current}/{total}]: {text[:40]}")

    def _on_sentence_done(self, index: int):
        row = index - 1
        if 0 <= row < self._table.rowCount():
            item = self._table.item(row, 4)
            if item is not None:
                item.setText("✓")
                item.setForeground(QBrush(QColor("#2e7d32")))

    def _on_dub_canceled(self):
        """用户主动取消：与"配音失败"区分开，并立即恢复按钮。"""
        self._was_canceled = True
        self._log_msg("已取消配音")
        self._status_label.setText("已取消")
        self._btn_stop.setEnabled(False)
        self._btn_start.setEnabled(True)

    def _on_error(self, msg: str):
        self._status_label.setText("配音失败")
        # msg 必须落日志：之前直接丢弃，用户只看到"配音失败"，
        # 失败原因在界面上完全不可见
        self._log_msg(f"✗ {msg}")

    def _on_finished(self, outputs: list):
        self._btn_stop.setEnabled(False)
        self._btn_start.setEnabled(True)

        # 工程已切换时丢弃旧工程的结果，防止串写新工程
        sender = self.sender()
        expected = sender.property("project_dir") if sender is not None else ""
        if expected and expected != self._project.project_dir:
            logger.warning("工程已切换，丢弃旧配音结果")
            return

        if self._was_canceled:
            self._status_label.setText("已取消")
            self._log_msg("━━━━━━━━━━ 已取消 ━━━━━━━━━━")
            return

        if outputs:
            self._progress.setValue(self._progress.maximum())
            self._status_label.setText("配音完成 ✓")
            self._outputs_label.setText("输出文件:\n" + "\n".join(outputs))
            self._log_msg("━━━━━━━━━━ 完成 ━━━━━━━━━━")
            for p in outputs:
                self._log_msg(f"📦 {p}")
        else:
            self._status_label.setText("配音失败，无输出（详见日志）")
        self._refresh_start_button()
        self._refresh_calibrate_button()

    def _on_worker_lifetime_finished(self):
        """worker 生命周期结束，安全清理引用，不访问其成员。"""
        worker = self._worker
        self._worker = None
        if worker is not None:
            try:
                worker.deleteLater()
            except RuntimeError:
                pass
        self._refresh_start_button()
        self._refresh_calibrate_button()

    # ── 外部协议 ──

    def set_client(self, client: BaseTTSClient):
        """外部（如 MainWindow）动态切换 API 客户端。"""
        self._client = client
        self._refresh_start_button()

    def set_project(self, project: Project):
        """切换工程时清空页面状态（临时音色/字幕不跨工程保留）。"""
        self._project = project
        self._entries = []
        self._input_path = ""
        self._input_ext = ""
        self._temp_audio_path = ""
        self._temp_audio_name = ""
        self._btn_preview_voice.setEnabled(False)
        self._player.stop()
        self._player.setSource(QUrl())
        self._table.setRowCount(0)
        self._table_gb.setTitle("字幕预览（只读）")
        self._file_label.setText("未选择文件")
        self._parse_label.setText("尚未解析字幕（支持 .srt / .ass）")
        self._outputs_label.setText("")
        self._progress.setValue(0)
        self._status_label.setText("等待开始…")
        self._refresh_voice_label()
        self._refresh_start_button()
        self._refresh_calibrate_button()
        self._log_msg(f"已切换到工程: {project.name}")

    def reset_for_new_project(self):
        """新建工程时清空面板状态。"""
        self._entries = []
        self._input_path = ""
        self._input_ext = ""
        self._temp_audio_path = ""
        self._temp_audio_name = ""
        self._btn_preview_voice.setEnabled(False)
        self._player.stop()
        self._player.setSource(QUrl())
        self._table.setRowCount(0)
        self._table_gb.setTitle("字幕预览（只读）")
        self._file_label.setText("未选择文件")
        self._parse_label.setText("尚未解析字幕（支持 .srt / .ass）")
        self._outputs_label.setText("")
        self._progress.setValue(0)
        self._status_label.setText("等待开始…")
        self._log.clear()
        self._refresh_voice_label()
        self._refresh_start_button()
        self._refresh_calibrate_button()

    def _disconnect_upload_worker(self, worker):
        """断开 worker 全部信号。

        原先逐个列举 ("success","error","finished")，但面板实际连的是
        result_ready —— 它根本没被断开。跨线程信号是 queued metacall，
        旧 worker 已返回而调用仍在主线程排队时，它会打到新 worker 上。
        全断即可，不再手工维护信号列表。
        """
        if worker is None:
            return
        try:
            worker.disconnect()
        except (RuntimeError, TypeError):
            pass
        for sig in ("success", "error", "finished"):
            try:
                getattr(worker, sig).disconnect()
            except Exception:
                pass

    def cancel_workers(self):
        """取消所有后台任务（应用退出时由主窗口调用）。"""
        for worker in (self._worker, self._upload_worker, self._calibrate_worker):
            if worker is None:
                continue
            try:
                worker.disconnect()
            except Exception:
                pass
            cancel = getattr(worker, "cancel", None)
            if callable(cancel):
                cancel()
            if worker.isRunning():
                worker.wait(2000)
            if worker.isRunning():
                worker.terminate()
                worker.wait(1000)
        try:
            self._player.stop()
        except Exception:
            pass

    def _log_msg(self, msg: str):
        self._log.appendPlainText(msg)
