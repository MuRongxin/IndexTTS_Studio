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
from index_tts_gui.core.merger import (
    collect_sentence_wavs,
    merge_wavs_with_pauses,
    merge_wavs_with_custom_pauses,
    validate_wav_order,
)
from index_tts_gui.core.pause_advisor import (
    LLMPauseAdvisor,
    is_configured as llm_is_configured,
)
from index_tts_gui.core.subtitler import generate_srt_from_sentences_with_pauses
from index_tts_gui.ui.synthesis_worker import SynthesisWorker
from index_tts_gui.ui.voice_panel import VoicePanel


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
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # ── 音色选择区（嵌入 VoicePanel）──
        layout.addWidget(self._voice_panel)

        # 设置区
        gb = QGroupBox("合成设置")
        gb_layout = QHBoxLayout(gb)

        gb_layout.addWidget(QLabel("工程输出目录:"))
        self._dir_label = QLabel(self._project.output_dir)
        self._dir_label.setStyleSheet("font-weight: bold;")
        self._dir_label.setToolTip("合成输出保存在当前工程文件夹内")
        gb_layout.addWidget(self._dir_label)

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

        self._btn_merge = QPushButton("🔀 合并完整音频")
        self._btn_merge.setEnabled(False)
        self._btn_merge.setStyleSheet("""
            QPushButton {
                background: #ff9800; color: white;
                padding: 10px 24px; border-radius: 6px;
                font-size: 14px; font-weight: bold;
            }
            QPushButton:hover { background: #f57c00; }
            QPushButton:disabled { background: #ccc; }
        """)
        self._btn_merge.clicked.connect(self._merge_full_audio)
        ctrl.addWidget(self._btn_merge)

        self._btn_clear_output = QPushButton("🗑 清空输出")
        self._btn_clear_output.setStyleSheet("""
            QPushButton {
                background: #757575; color: white;
                padding: 10px 24px; border-radius: 6px;
                font-size: 14px; font-weight: bold;
            }
            QPushButton:hover { background: #616161; }
        """)
        self._btn_clear_output.clicked.connect(self._clear_output_dir)
        ctrl.addWidget(self._btn_clear_output)

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
            self._log_msg("⚠ 请先在文稿面板拆分句子")
            return
        if not self._audio_name:
            self._log_msg("⚠ 请先在音色面板上传参考音频")
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
        self._status_label.setText("合成完成 ✓")
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
                    self._log_msg(f"✗ 删除失败 {path}: {e}")

        self._log_msg(f"🗑 已清空输出目录，删除 {removed} 个文件")
        self._refresh_merge_button()

    def _merge_full_audio(self):
        """把 output_dir 里的片段合并成完整音频，优先使用 LLM 停顿建议。"""
        if not self._sentences:
            self._log_msg("⚠ 没有句子，无法合并")
            return

        logger.info("开始合并: output_dir=%s sentences=%d", self._output_dir, len(self._sentences))

        wavs = collect_sentence_wavs(self._output_dir)
        logger.info("发现音频片段: %d 个", len(wavs))
        if not wavs:
            self._log_msg(f"⚠ 在 {self._output_dir} 下未找到 sentence_*.wav")
            return

        if len(wavs) != len(self._sentences):
            self._log_msg(
                f"⚠ 音频片段数（{len(wavs)}）与句子数（{len(self._sentences)}）不一致"
            )
            return

        # 校验文件名中的文本与当前句子是否一致
        errors = validate_wav_order(wavs, self._sentences)
        if errors:
            self._log_msg("⚠ 音频文件与当前句子不匹配：")
            for err in errors:
                self._log_msg(f"  - {err}")
            self._log_msg("请重新合成，或检查 output_tts 目录")
            return

        output_path = os.path.join(self._output_dir, "full_dub.wav")

        # 优先调用 LLM 停顿顾问
        pauses = None
        if llm_is_configured(self._llm_cfg):
            self._log_msg("🤖 正在询问 LLM 停顿建议…")
            try:
                advisor = LLMPauseAdvisor(
                    api_url=self._llm_cfg["api_url"],
                    api_key=self._llm_cfg["api_key"],
                    model=self._llm_cfg["model"],
                    timeout=self._llm_cfg.get("timeout", 60),
                    prompt_template=self._llm_cfg.get(
                        "pause_prompt_template", ""
                    ) or None,
                )
                pauses = advisor.advise(self._sentences)
                self._log_msg(f"📐 LLM 停顿建议: {pauses}")
            except Exception as e:
                logger.exception("LLM 停顿顾问失败")
                self._log_msg(f"⚠ LLM 停顿建议失败，回退标点规则: {e}")

        try:
            if pauses:
                merge_wavs_with_custom_pauses(wavs, pauses, output_path)
            else:
                # 回退到标点规则，同时得到 pauses 用于字幕对齐
                from index_tts_gui.core.merger import _compute_pauses
                pauses = _compute_pauses(self._sentences)
                merge_wavs_with_custom_pauses(wavs, pauses, output_path)

            self._log_msg(f"✓ 已生成完整音频: {output_path}")
            self._status_label.setText(f"完整音频已生成: {output_path}")

            # 用同一组 pauses 生成字幕，保证时间轴对齐
            entries = generate_srt_from_sentences_with_pauses(
                self._sentences, wavs, pauses
            )
            self._log_msg(f"✓ 已生成字幕: {len(entries)} 条")
            self.merge_done.emit(entries)
        except Exception as e:
            logger.exception("合并完整音频失败")
            self._log_msg(f"✗ 合并失败: {e}")

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

    def get_output_dir(self) -> str:
        return self._output_dir
