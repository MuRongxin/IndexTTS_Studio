"""合成控制面板"""
import glob
import logging
import os
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QProgressBar, QPlainTextEdit, QLabel, QFileDialog,
    QGroupBox, QSpinBox,
)
from PySide6.QtCore import Qt, Signal

from index_tts_gui.core.tts_client import BaseTTSClient, IndexTTSClient
from index_tts_gui.core.project import Project
from index_tts_gui.core.merger import collect_sentence_wavs
from index_tts_gui.ui.merge_worker import MergeWorker
from index_tts_gui.ui.synthesis_worker import SynthesisWorker
from index_tts_gui.ui.voice_panel import VoicePanel
from index_tts_gui.ui.theme import Theme
from index_tts_gui.ui.widgets import card


logger = logging.getLogger("index_tts")


class SynthesisPanel(QWidget):
    """合成控制：音色选择 + 进度条 + 日志 + 启停"""

    synthesis_done = Signal(str)  # 合成完成，输出目录路径
    merge_done = Signal(list)     # 合并完成，字幕条目列表

    def __init__(self, project: Project, client: BaseTTSClient | None = None):
        super().__init__()
        self._project = project
        self._client = client or IndexTTSClient()
        self._worker: SynthesisWorker | None = None
        self._merge_worker: MergeWorker | None = None
        self._sentences: list[str] = []
        self._audio_name: str = project.audio_name
        self._output_dir: str = project.output_dir
        self._was_canceled = False
        self._llm_cfg: dict = {}

        self._voice_panel = VoicePanel(self._project, self._client)
        self._voice_panel.audio_uploaded.connect(self.set_audio_name)

        self._setup_ui()
        # 从工程恢复状态（兜底：sentences_ready 信号可能在构造时尚未连接）
        if self._project.sentences:
            self.set_sentences(self._project.sentences)
        self._refresh_merge_button()

    def _setup_ui(self):
        c = Theme.colors
        r = Theme.radius
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)

        # ── 音色选择区（嵌入 VoicePanel）──
        layout.addWidget(self._voice_panel)

        # ── 合成控制卡片 ──
        ctrl_card = card("合成控制", use_groupbox=True)
        ctrl_layout = ctrl_card.layout()

        # 步骤指示
        steps = QHBoxLayout()
        steps.setSpacing(8)
        for i, text in enumerate(["1 选择音色", "2 开始合成", "3 合并音频"], 1):
            step_lbl = QLabel(text)
            step_lbl.setStyleSheet(
                f"color: {c.text_secondary}; font-size: {Theme.fonts.size_sm}px; font-weight: 500;"
            )
            steps.addWidget(step_lbl)
            if i < 3:
                sep = QLabel("→")
                sep.setStyleSheet(f"color: {c.border};")
                steps.addWidget(sep)
        steps.addStretch()
        ctrl_layout.addLayout(steps)

        # 输出目录
        dir_layout = QHBoxLayout()
        dir_lbl = QLabel("输出目录")
        dir_lbl.setStyleSheet(f"color: {c.text_secondary};")
        dir_layout.addWidget(dir_lbl)
        self._dir_label = QLabel(self._project.output_dir)
        self._dir_label.setStyleSheet(
            f"font-weight: 600; color: {c.text_primary};"
        )
        self._dir_label.setToolTip("合成输出保存在当前工程文件夹内")
        dir_layout.addWidget(self._dir_label)
        dir_layout.addStretch()
        ctrl_layout.addLayout(dir_layout)

        # 进度条
        self._progress = QProgressBar()
        self._progress.setMinimum(0)
        self._progress.setMaximum(100)
        self._progress.setValue(0)
        self._progress.setTextVisible(True)
        ctrl_layout.addWidget(self._progress)

        self._status_label = QLabel("等待开始…")
        self._status_label.setStyleSheet(f"color: {c.text_secondary};")
        ctrl_layout.addWidget(self._status_label)

        # 控制按钮
        ctrl = QHBoxLayout()
        ctrl.setSpacing(10)

        self._btn_start = QPushButton("开始合成")
        self._btn_start.setProperty("variant", "primary")
        self._btn_start.setCursor(Qt.PointingHandCursor)
        self._btn_start.setMinimumHeight(40)
        self._btn_start.clicked.connect(self._start)
        ctrl.addWidget(self._btn_start)

        self._btn_stop = QPushButton("停止")
        self._btn_stop.setEnabled(False)
        self._btn_stop.setProperty("variant", "danger")
        self._btn_stop.setCursor(Qt.PointingHandCursor)
        self._btn_stop.setMinimumHeight(40)
        self._btn_stop.clicked.connect(self._stop)
        ctrl.addWidget(self._btn_stop)

        self._btn_merge = QPushButton("合并完整音频")
        self._btn_merge.setEnabled(False)
        self._btn_merge.setCursor(Qt.PointingHandCursor)
        self._btn_merge.setMinimumHeight(40)
        self._btn_merge.clicked.connect(self._merge_full_audio)
        ctrl.addWidget(self._btn_merge)

        self._btn_clear_output = QPushButton("清空输出")
        self._btn_clear_output.setProperty("variant", "ghost")
        self._btn_clear_output.setCursor(Qt.PointingHandCursor)
        self._btn_clear_output.clicked.connect(self._clear_output_dir)
        ctrl.addWidget(self._btn_clear_output)

        ctrl.addStretch()
        ctrl_layout.addLayout(ctrl)
        layout.addWidget(ctrl_card)

        # ── 日志卡片 ──
        log_card = card("运行日志", use_groupbox=True)
        log_layout = log_card.layout()

        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        self._log.setMaximumBlockCount(500)
        self._log.setStyleSheet(f"""
            QPlainTextEdit {{
                background: {c.bg};
                color: {c.text_primary};
                border: 1px solid {c.border};
                border-radius: {r.sm}px;
                font-family: {Theme.fonts.mono};
                font-size: {Theme.fonts.size_sm}px;
                padding: 10px;
            }}
        """)
        log_layout.addWidget(self._log)
        layout.addWidget(log_card, 1)

    def set_sentences(self, sentences: list[str]):
        self._sentences = sentences
        self._progress.setMaximum(len(sentences))
        self._refresh_merge_button()

    def set_audio_name(self, name: str):
        self._audio_name = name
        self._project.audio_name = name
        self._project.save()

    def set_llm_config(self, cfg: dict):
        """外部注入 LLM 配置，用于智能停顿建议。"""
        self._llm_cfg = cfg or {}

    def _refresh_merge_button(self):
        """当输出目录存在 sentence_*.wav 时启用合并按钮。"""
        has_wavs = bool(collect_sentence_wavs(self._output_dir))
        has_sentences = bool(self._sentences)
        self._btn_merge.setEnabled(has_wavs and has_sentences)

    def _start(self):
        if not self._sentences:
            self._log_msg("请先在文稿面板拆分句子")
            return
        if not self._audio_name:
            self._log_msg("请先在音色面板上传参考音频")
            return

        self._was_canceled = False
        self._progress.setValue(0)
        self._btn_start.setEnabled(False)
        self._btn_stop.setEnabled(True)
        self._btn_merge.setEnabled(False)
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
        self._status_label.setText("合成完成")
        self._log_msg("━━━━━━━━━━ 完成 ━━━━━━━━━━")
        self._btn_merge.setEnabled(True)
        self.synthesis_done.emit(self._output_dir)

    def _log_msg(self, msg: str):
        self._log.appendPlainText(msg)

    def _clear_output_dir(self):
        """清空输出目录下的生成文件。"""
        if not os.path.exists(self._output_dir):
            self._log_msg("输出目录不存在，无需清空")
            return

        patterns = ["sentence_*.wav", "full_dub.wav", "split_result.txt"]
        removed = 0
        for pattern in patterns:
            for path in glob.glob(os.path.join(self._output_dir, pattern)):
                try:
                    os.remove(path)
                    removed += 1
                except Exception as e:
                    self._log_msg(f"删除失败 {path}: {e}")

        self._log_msg(f"已清空输出目录，删除 {removed} 个文件")
        self._project.pauses = []
        self._project.save()
        self._refresh_merge_button()

    def _merge_full_audio(self):
        """把 output_dir 里的片段合并成完整音频，在后台线程执行。"""
        if not self._sentences:
            self._log_msg("没有句子，无法合并")
            return

        logger.info("开始合并: output_dir=%s sentences=%d", self._output_dir, len(self._sentences))

        self._btn_merge.setEnabled(False)
        self._status_label.setText("正在合并完整音频…")

        self._merge_worker = MergeWorker(
            self._sentences, self._output_dir, self._llm_cfg
        )
        self._merge_worker.log.connect(self._log_msg)
        self._merge_worker.progress.connect(self._on_merge_progress)
        self._merge_worker.finished.connect(self._on_merge_finished)
        self._merge_worker.error.connect(self._on_merge_error)
        self._merge_worker.start()

    def _on_merge_progress(self, current: int, total: int, message: str):
        self._progress.setMaximum(total)
        self._progress.setValue(current)
        self._status_label.setText(f"合并中 [{current}/{total}]: {message}")

    def _on_merge_finished(self, entries: list):
        self._progress.setValue(self._progress.maximum())
        self._status_label.setText(f"完整音频已生成: {self._output_dir}/full_dub.wav")
        self._btn_merge.setEnabled(True)

        # 保存本次合并实际使用的 pauses，供字幕页重新生成时对齐
        if self._merge_worker is not None:
            self._project.pauses = list(self._merge_worker.pauses)
            self._project.save()

        self.merge_done.emit(entries)

    def _on_merge_error(self, msg: str):
        self._log_msg(f"合并失败: {msg}")
        self._status_label.setText("合并失败")
        self._btn_merge.setEnabled(True)

    def set_client(self, client: BaseTTSClient):
        """外部（如 MainWindow）动态切换 API 客户端。"""
        self._client = client
        self._voice_panel.set_client(client)

    def set_project(self, project: Project):
        """切换工程时刷新输出目录和相关状态。"""
        self._project = project
        self._output_dir = project.output_dir
        self._dir_label.setText(project.output_dir)
        self._audio_name = project.audio_name
        self._voice_panel.set_project(project)
        if project.sentences:
            self.set_sentences(project.sentences)
        self._refresh_merge_button()

    def reset_for_new_project(self):
        """新建工程时清空面板状态。"""
        self._sentences = []
        self._progress.setValue(0)
        self._progress.setMaximum(0)
        self._status_label.setText("等待拆分句子")
        self._log.clear()
        self._btn_start.setEnabled(True)
        self._btn_stop.setEnabled(False)
        self._btn_merge.setEnabled(False)
        self._voice_panel.reset_for_new_project()

    def get_output_dir(self) -> str:
        return self._output_dir
