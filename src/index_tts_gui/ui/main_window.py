"""主窗口：左侧导航 + 右侧堆叠面板"""
import json
import logging
import os
from PySide6.QtWidgets import (
    QMainWindow,
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QFrame, QStackedWidget, QSplitter,
    QSizePolicy,
)
from PySide6.QtCore import Qt, QByteArray

from index_tts_gui.core.tts_client import (
    BaseTTSClient,
    create_client,
    DEFAULT_API_URL,
    DEFAULT_TIMEOUT,
)
from index_tts_gui.ui.editor import ManuscriptPanel
from index_tts_gui.ui.synthesis_panel import SynthesisPanel
from index_tts_gui.ui.subtitle_view import SubtitlePanel
from index_tts_gui.ui.settings_dialog import SettingsDialog
from index_tts_gui.ui.log_status_bar import LogStatusBar, QtLogHandler

CONFIG_FILE = "config.json"

logger = logging.getLogger("index_tts")


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("IndexTTS Studio")
        self.resize(1200, 800)

        self._client: BaseTTSClient | None = None
        self._load_config()
        self._setup_central()
        # 启动时尝试加载上次保存的拆分结果
        self.manuscript_panel.load_split_result()
        self._setup_statusbar()
        self._apply_stylesheet()
        self._apply_api()
        self._apply_llm_config()
        self._restore_window_state()

    # ── 配置持久化 ──

    def _load_config(self):
        defaults = {
            "provider": "index_tts",
            "api_url": DEFAULT_API_URL,
            "timeout": dict(DEFAULT_TIMEOUT),
            "window_geometry": "",
            "window_state": "",
            "llm": {
                "enabled": True,
                "preset": "deepseek",
                "api_url": "https://api.deepseek.com",
                "api_key": "",
                "model": "deepseek-v4-flash",
                "timeout": 60,
                "max_completion_tokens": 2048,
                "max_sentence_length": 40,
                "user_prompt_template": "",
            },
        }
        self._config = defaults
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                    for k in defaults:
                        if k in loaded:
                            self._config[k] = loaded[k]
                    if isinstance(self._config["timeout"], dict):
                        self._config["timeout"] = {
                            **dict(DEFAULT_TIMEOUT),
                            **self._config["timeout"],
                        }
                    self._validate_llm_config()
            except Exception:
                pass

    def _validate_llm_config(self):
        """校验并修正 LLM 配置中的无效模型名。"""
        from index_tts_gui.core.splitter import LLM_PRESETS

        llm = self._config.get("llm", {})
        preset = llm.get("preset")
        model = llm.get("model", "")
        if preset and preset in LLM_PRESETS:
            allowed = LLM_PRESETS[preset]["models"]
            default = LLM_PRESETS[preset]["default_model"]
            if model not in allowed:
                logger.warning(
                    "配置中模型 %s 不在预设 %s 的可用列表 %s 中，已重置为 %s",
                    model, preset, allowed, default
                )
                llm["model"] = default

    def _save_config(self):
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(self._config, f, indent=2, ensure_ascii=False)
        except Exception:
            pass

    def _save_window_state(self):
        geo = self.saveGeometry().toBase64().data().decode("ascii")
        state = self.saveState().toBase64().data().decode("ascii")
        self._config["window_geometry"] = geo
        self._config["window_state"] = state
        self._save_config()

    def _restore_window_state(self):
        geo = self._config.get("window_geometry", "")
        state = self._config.get("window_state", "")
        if geo:
            try:
                self.restoreGeometry(QByteArray.fromBase64(geo.encode("ascii")))
            except Exception:
                pass
        if state:
            try:
                self.restoreState(QByteArray.fromBase64(state.encode("ascii")))
            except Exception:
                pass

    # ── API Client ──

    def _apply_api(self):
        try:
            self._client = create_client(
                provider=self._config["provider"],
                api_url=self._config["api_url"],
                timeout=self._config["timeout"],
            )
        except Exception as e:
            self.status_bar.showMessage(f"创建 API 客户端失败: {e}")
            return

        if hasattr(self, "synthesis_panel"):
            self.synthesis_panel.set_client(self._client)

    def _apply_llm_config(self):
        llm_cfg = self._config.get("llm", {})
        if hasattr(self, "manuscript_panel"):
            self.manuscript_panel.set_llm_config(llm_cfg)
        if hasattr(self, "synthesis_panel"):
            self.synthesis_panel.set_llm_config(llm_cfg)

    # ── UI ──

    def _setup_statusbar(self):
        self.status_bar = LogStatusBar()
        self.setStatusBar(self.status_bar)

        # 将日志转发到底部状态栏
        self._qt_log_handler = QtLogHandler(self)
        self._qt_log_handler.log_record.connect(self.status_bar.show_log_message)
        logger.addHandler(self._qt_log_handler)

    def _setup_central(self):
        root = QWidget()
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── 左侧导航栏 ──
        self._sidebar = QFrame()
        self._sidebar.setFixedWidth(160)
        self._sidebar.setObjectName("sidebar")
        sidebar_layout = QVBoxLayout(self._sidebar)
        sidebar_layout.setContentsMargins(8, 16, 8, 16)
        sidebar_layout.setSpacing(8)

        title = QLabel("IndexTTS\nStudio")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("color: #fff; font-size: 16px; font-weight: bold;")
        sidebar_layout.addWidget(title)

        sidebar_layout.addSpacing(16)

        self._nav_buttons: list[QPushButton] = []
        nav_items = [
            ("📝 文稿", 0),
            ("🎙 合成", 1),
            ("📄 字幕", 2),
        ]
        for label, idx in nav_items:
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setProperty("nav_index", idx)
            btn.setStyleSheet("""
                QPushButton {
                    text-align: left; padding: 10px 14px;
                    border: none; border-radius: 6px;
                    color: #cfd8dc; background: transparent;
                    font-size: 14px;
                }
                QPushButton:hover { background: #37474f; color: #fff; }
                QPushButton:checked { background: #2979ff; color: #fff; font-weight: bold; }
            """)
            btn.clicked.connect(self._on_nav_clicked)
            sidebar_layout.addWidget(btn)
            self._nav_buttons.append(btn)

        sidebar_layout.addStretch()

        # 设置按钮
        btn_settings = QPushButton("⚙ 设置")
        btn_settings.setStyleSheet("""
            QPushButton {
                text-align: left; padding: 10px 14px;
                border: 1px solid #546e7a; border-radius: 6px;
                color: #cfd8dc; background: transparent;
                font-size: 14px;
            }
            QPushButton:hover { background: #37474f; color: #fff; }
        """)
        btn_settings.clicked.connect(self._open_settings)
        sidebar_layout.addWidget(btn_settings)

        layout.addWidget(self._sidebar)

        # ── 右侧内容区 ──
        self._stack = QStackedWidget()
        self._stack.setSizePolicy(
            QSizePolicy.Expanding, QSizePolicy.Expanding
        )

        self.manuscript_panel = ManuscriptPanel()
        self._stack.addWidget(self.manuscript_panel)

        self.synthesis_panel = SynthesisPanel()
        self._stack.addWidget(self.synthesis_panel)

        self.subtitle_panel = SubtitlePanel()
        self.subtitle_panel.set_manuscript_source(
            self.manuscript_panel.get_text
        )
        self._stack.addWidget(self.subtitle_panel)

        layout.addWidget(self._stack)

        # 信号链
        self.manuscript_panel.sentences_ready.connect(
            self.synthesis_panel.set_sentences
        )
        self.synthesis_panel.synthesis_done.connect(self._on_synthesis_done)

        self.setCentralWidget(root)

        # 默认选中第一项
        self._set_current_page(0)

    def _on_nav_clicked(self):
        btn = self.sender()
        idx = btn.property("nav_index")
        self._set_current_page(idx)

    def _set_current_page(self, index: int):
        self._stack.setCurrentIndex(index)
        for btn in self._nav_buttons:
            btn.setChecked(btn.property("nav_index") == index)

    def _open_settings(self):
        dialog = SettingsDialog(self._config, self)
        if dialog.exec() == SettingsDialog.Accepted:
            self._config = dialog.get_config()
            self._save_config()
            self._apply_api()
            self._apply_llm_config()
            self.status_bar.showMessage("设置已保存")

    def _on_synthesis_done(self, output_dir: str):
        import os
        from index_tts_gui.core.merger import merge_wavs

        wavs = sorted([
            os.path.join(output_dir, f)
            for f in os.listdir(output_dir)
            if f.startswith("sentence_") and f.endswith(".wav")
        ])
        if wavs:
            merged_path = "full_dub.wav"
            self.status_bar.showMessage("正在合并音频…")
            merge_wavs(wavs, merged_path)
            self.status_bar.showMessage(f"音频已合并: {merged_path}")

            from index_tts_gui.core.subtitler import generate_srt
            text = self.manuscript_panel.get_text()
            entries = generate_srt(merged_path, text, wavs)
            self.subtitle_panel.load_entries(entries)
            self._set_current_page(3)
            self.status_bar.showMessage(f"字幕已生成: {len(entries)} 条")

    def closeEvent(self, event):
        self._save_window_state()
        super().closeEvent(event)

    def _apply_stylesheet(self):
        self.setStyleSheet("""
            QMainWindow {
                background-color: #f5f5f5;
            }
            #sidebar {
                background-color: #263238;
                border-right: 1px solid #1c252a;
            }
            QStatusBar {
                background: #e8e8e8;
            }
        """)
