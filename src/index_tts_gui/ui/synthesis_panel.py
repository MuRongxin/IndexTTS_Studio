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
        self._voice_panel.segment_regenerate.connect(self._on_segment_regenerate_request)
        self._voice_panel.segment_preview.connect(self._preview_wav)

        self._setup_ui()
        # 从工程恢复状态（兜底：sentences_ready 信号可能在构造时尚未连接）
        if self._project.sentences:
            self.set_sentences(self._project.sentences)
        self._refresh_segment_list()
        self._refresh_merge_button()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # ── 音色选择区（嵌入 VoicePanel）──
        layout.addWidget(self._voice_panel, 1)

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

        self._btn_preview_single = QPushButton("▶ 预览")
        self._btn_preview_single.setVisible(False)
        self._btn_preview_single.setToolTip("预览选中的合成片段")
        self._btn_preview_single.setStyleSheet("""
            QPushButton {
                background: #2196f3; color: white;
                padding: 10px 24px; border-radius: 6px;
                font-size: 14px; font-weight: bold;
            }
            QPushButton:hover { background: #1976d2; }
        """)
        self._btn_preview_single.clicked.connect(self._on_preview_single_clicked)
        ctrl.addWidget(self._btn_preview_single)

        self._btn_regen_single = QPushButton("🔄 重新生成该句")
        self._btn_regen_single.setVisible(False)
        self._btn_regen_single.setToolTip("重新合成右侧列表中选中的句子")
        self._btn_regen_single.setStyleSheet("""
            QPushButton {
                background: #ff9800; color: white;
                padding: 10px 24px; border-radius: 6px;
                font-size: 14px; font-weight: bold;
            }
            QPushButton:hover { background: #f57c00; }
            QPushButton:disabled { background: #ccc; }
        """)
        self._btn_regen_single.clicked.connect(self._on_regen_single_clicked)
        ctrl.addWidget(self._btn_regen_single)

        layout.addLayout(ctrl)

        # 日志
        log_label = QLabel("日志:")
        log_label.setStyleSheet("font-weight: bold; margin-top: 8px;")
        layout.addWidget(log_label)

        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        self._log.setMaximumBlockCount(500)
        self._log.setMaximumHeight(120)
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

    def _diff_sentences(self) -> tuple[list[int], list[int], list[int]]:
        """对比当前句子与 wav_map，返回 (未变, 已变, 新增) 的 0-based 索引列表。"""
        unchanged = []
        changed = []
        new_sentences = []
        wav_map = self._project.wav_map

        if not wav_map:
            # 无历史映射，全部算新增
            new_sentences = list(range(len(self._sentences)))
            self._log_msg(f"🔍 WAV 映射为空，{len(new_sentences)} 句需重新合成")
            return unchanged, changed, new_sentences

        for i, sent in enumerate(self._sentences):
            match = None
            for entry in wav_map:
                if entry["index"] == i and entry["text"] == sent:
                    match = entry
                    break
            if match:
                # 检查对应 WAV 是否存在
                wav_path = os.path.join(self._output_dir, match["wav"])
                if os.path.exists(wav_path):
                    unchanged.append(i)
                else:
                    changed.append(i)
            elif any(e["index"] == i for e in wav_map):
                changed.append(i)
            else:
                new_sentences.append(i)

        # 找出已删除的句子（wav_map 中有但 sentences 中没有的）
        deleted = [
            e for e in wav_map
            if e["index"] >= len(self._sentences)
            or self._sentences[e["index"]] != e["text"]
        ]

        # 输出 diff 日志
        if unchanged:
            self._log_msg(f"✅ {len(unchanged)} 句未变，跳过合成")
        if changed:
            self._log_msg(f"🔄 {len(changed)} 句文本已变，需重新合成: {changed[:10]}{'...' if len(changed)>10 else ''}")
        if new_sentences:
            self._log_msg(f"➕ {len(new_sentences)} 句新增，需合成")
        if deleted:
            self._log_msg(f"🗑 {len(deleted)} 句已从文稿移除，对应 WAV 可清理")
            for e in deleted:
                old_wav = os.path.join(self._output_dir, e["wav"])
                if os.path.exists(old_wav):
                    try:
                        os.remove(old_wav)
                    except Exception:
                        pass
            self._log_msg(f"🗑 已清理 {len(deleted)} 个过期 WAV")

        return unchanged, changed, new_sentences

    def _start(self):
        if not self._sentences:
            self._log_msg("⚠ 请先在文稿面板拆分句子")
            return
        if not self._audio_name:
            self._log_msg("⚠ 请先在音色面板上传参考音频")
            return

        # 对比句子变化
        self._diff_sentences()

        self._voice_panel.clear_segments()
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
        filename = os.path.basename(path) if os.path.exists(path) else f"sentence_{index:02d}.wav"
        self._voice_panel.add_segment(index, filename)

    def _on_finished(self, wav_map=None):
        self._btn_start.setEnabled(True)
        self._btn_stop.setEnabled(False)

        if self._was_canceled:
            self._status_label.setText("已停止")
            self._log_msg("━━━━━━━━━━ 已取消 ━━━━━━━━━━")
            return

        # 保存 WAV 映射
        if wav_map:
            self._project.wav_map = wav_map
            self._project.save()
            self._log_msg(f"📝 WAV 映射已保存: {len(wav_map)} 条")

        self._progress.setValue(self._progress.maximum())
        self._status_label.setText("合成完成 ✓")
        self._log_msg("━━━━━━━━━━ 完成 ━━━━━━━━━━")
        self._btn_merge.setEnabled(True)
        self.synthesis_done.emit(self._output_dir)

    def _on_segment_regenerate_request(self, index: int):
        """右侧片段被选中/双击，显示/隐藏按钮。"""
        self._regen_index = index
        visible = index is not None and index >= 0
        self._btn_preview_single.setVisible(visible)
        self._btn_regen_single.setVisible(visible)

    def _on_regen_single_clicked(self):
        """点击「重新生成该句」按钮。"""
        if hasattr(self, '_regen_index') and self._regen_index >= 0:
            self._regenerate_single(self._regen_index)

    def _preview_wav(self, wav_path: str):
        """预览合成片段 WAV。"""
        self._voice_panel.preview_segment(wav_path)

    def _on_preview_single_clicked(self):
        """点击「预览」按钮，播放选中的合成片段。"""
        if not hasattr(self, '_regen_index') or self._regen_index < 0:
            return
        from index_tts_gui.core.merger import collect_sentence_wavs
        wavs = collect_sentence_wavs(self._output_dir)
        idx = self._regen_index
        for wav in wavs:
            name = os.path.basename(wav)
            if name.startswith(f"sentence_{idx:02d}_"):
                self._voice_panel.preview_segment(wav)
                return
        self._log_msg(f"⚠ 未找到第 {idx+1} 句的音频文件")

    def _regenerate_single(self, index: int):
        """重新合成单句（后台线程）。"""
        if index < 0 or index >= len(self._sentences):
            return
        from PySide6.QtCore import QThread
        from index_tts_gui.core.merger import sanitize_for_filename

        sentence = self._sentences[index]
        audio_name = self._audio_name
        output_dir = self._output_dir
        client = self._client
        log_msg = self._log_msg

        class _SingleSynthThread(QThread):
            def run(self):
                try:
                    audio_bytes = client.synthesize(sentence, audio_name)
                    text_part = sanitize_for_filename(sentence)
                    wav_path = os.path.join(
                        output_dir, f"sentence_{index:02d}_{text_part}.wav"
                    )
                    with open(wav_path, "wb") as f:
                        f.write(audio_bytes)
                    log_msg(f"🔄 重新合成: 第 {index+1} 句 → {os.path.basename(wav_path)}")
                except Exception as e:
                    log_msg(f"✗ 重新合成第 {index+1} 句失败: {e}")

        self._single_thread = _SingleSynthThread()
        self._single_thread.start()

    def _log_msg(self, msg: str):
        self._log.appendPlainText(msg)
        # 强制刷新，防止长操作时日志区冻结
        from PySide6.QtWidgets import QApplication
        QApplication.processEvents()

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
        self._project.pauses = []
        self._project.save()
        self._voice_panel.clear_segments()
        self._refresh_merge_button()

    def _merge_full_audio(self):
        """把 output_dir 里的片段合并成完整音频，在后台线程执行。"""
        if not self._sentences:
            self._log_msg("⚠ 没有句子，无法合并")
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
        self._log_msg(f"✗ 合并失败: {msg}")
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
        self._refresh_segment_list()
        self._refresh_merge_button()

    def _refresh_segment_list(self):
        """扫描输出目录，将已有 WAV 文件显示在右侧片段列表。"""
        self._voice_panel.clear_segments()
        if not os.path.isdir(self._output_dir):
            return
        from index_tts_gui.core.merger import parse_sentence_wav_name
        wavs = sorted(
            f for f in os.listdir(self._output_dir)
            if f.startswith("sentence_") and f.endswith(".wav")
        )
        for f in wavs:
            parsed = parse_sentence_wav_name(f)
            idx = parsed[0] if parsed else 0
            self._voice_panel.add_segment(idx, f)

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
