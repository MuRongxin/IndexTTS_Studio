"""主窗口：左侧导航 + 右侧堆叠面板"""
import json
import logging
import os
from PySide6.QtWidgets import (
    QMainWindow,
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QFrame, QStackedWidget, QSplitter,
    QSizePolicy, QFileDialog, QInputDialog, QMessageBox,
)
from PySide6.QtCore import Qt, QByteArray

from index_tts_gui.core.tts_client import (
    BaseTTSClient,
    create_client,
    DEFAULT_API_URL,
    DEFAULT_TIMEOUT,
)
from index_tts_gui.core.project import Project
from index_tts_gui.ui.editor import ManuscriptPanel
from index_tts_gui.ui.synthesis_panel import SynthesisPanel
from index_tts_gui.ui.subtitle_view import SubtitlePanel
from index_tts_gui.ui.settings_dialog import SettingsDialog
from index_tts_gui.ui.log_status_bar import LogStatusBar, QtLogHandler
from index_tts_gui.ui.styles import global_stylesheet
from index_tts_gui.ui.theme import Theme

CONFIG_FILE = "config.json"

logger = logging.getLogger("index_tts")


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("IndexTTS Studio")
        self.resize(1200, 800)

        self._client: BaseTTSClient | None = None
        self._load_config()
        # 创建/加载默认工程
        self._project = Project.create_default(os.getcwd())
        logger.info("加载工程: %s", self._project.to_dict())
        self._setup_central()
        self._setup_statusbar()
        self._apply_stylesheet()
        self._apply_api()
        self._apply_llm_config()
        self._update_project_label()
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
                "punctuation_fallback": False,
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
        root.setStyleSheet(f"background: {Theme.colors.bg};")
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── 左侧导航栏 ──
        self._sidebar = QFrame()
        self._sidebar.setFixedWidth(200)
        self._sidebar.setObjectName("sidebar")
        self._sidebar.setStyleSheet(f"""
            #sidebar {{
                background: {Theme.colors.surface};
                border-right: 1px solid {Theme.colors.border};
            }}
        """)
        sidebar_layout = QVBoxLayout(self._sidebar)
        sidebar_layout.setContentsMargins(16, 20, 16, 20)
        sidebar_layout.setSpacing(8)

        # Logo / 标题
        title = QLabel("IndexTTS Studio")
        title.setStyleSheet(
            f"color: {Theme.colors.text_primary}; font-size: 18px; font-weight: 700;"
        )
        sidebar_layout.addWidget(title)

        subtitle = QLabel("AI 配音工作台")
        subtitle.setStyleSheet(
            f"color: {Theme.colors.text_secondary}; font-size: {Theme.fonts.size_sm}px; margin-bottom: 8px;"
        )
        sidebar_layout.addWidget(subtitle)

        sidebar_layout.addSpacing(24)

        # 导航按钮
        self._nav_buttons: list[QPushButton] = []
        nav_items = [
            ("文稿", 0),
            ("合成", 1),
            ("字幕", 2),
        ]
        for label, idx in nav_items:
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setProperty("nav_index", idx)
            btn.setProperty("nav_style", "true")
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet(f"""
                QPushButton[nav_style="true"] {{
                    text-align: left; padding: 10px 14px;
                    border: none; border-radius: {Theme.radius.sm}px;
                    color: {Theme.colors.text_secondary};
                    background: transparent;
                    font-size: {Theme.fonts.size_md}px;
                    font-weight: 500;
                }}
                QPushButton[nav_style="true"]:hover {{
                    background: {Theme.colors.bg};
                    color: {Theme.colors.text_primary};
                }}
                QPushButton[nav_style="true"]:checked {{
                    background: {Theme.colors.primary};
                    color: {Theme.colors.text_on_primary};
                    font-weight: 600;
                }}
            """)
            btn.clicked.connect(self._on_nav_clicked)
            sidebar_layout.addWidget(btn)
            self._nav_buttons.append(btn)

        sidebar_layout.addStretch()

        # 工程区域
        project_section = QFrame()
        project_section.setStyleSheet(f"""
            QFrame {{
                background: {Theme.colors.bg};
                border-radius: {Theme.radius.md}px;
            }}
        """)
        project_layout = QVBoxLayout(project_section)
        project_layout.setContentsMargins(12, 12, 12, 12)
        project_layout.setSpacing(8)

        project_header = QLabel("工程")
        project_header.setStyleSheet(
            f"color: {Theme.colors.text_tertiary}; font-size: {Theme.fonts.size_sm}px; font-weight: 600;"
        )
        project_layout.addWidget(project_header)

        self._project_label = QLabel("未命名工程")
        self._project_label.setStyleSheet(
            f"color: {Theme.colors.text_primary}; font-size: {Theme.fonts.size_sm}px; font-weight: 500;"
        )
        self._project_label.setWordWrap(True)
        project_layout.addWidget(self._project_label)

        project_btns = QHBoxLayout()
        project_btns.setSpacing(6)
        for label, slot in [("新建", self._new_project), ("打开", self._open_project), ("保存", self._save_project)]:
            btn = QPushButton(label)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setProperty("compact", "true")
            btn.setStyleSheet(f"""
                QPushButton[compact="true"] {{
                    background: {Theme.colors.surface};
                    color: {Theme.colors.text_secondary};
                    border: 1px solid {Theme.colors.border};
                    border-radius: {Theme.radius.sm}px;
                    padding: 4px 8px;
                    font-size: {Theme.fonts.size_sm}px;
                }}
                QPushButton[compact="true"]:hover {{
                    background: {Theme.colors.surface_hover};
                    color: {Theme.colors.text_primary};
                    border-color: {Theme.colors.text_tertiary};
                }}
            """)
            btn.clicked.connect(slot)
            project_btns.addWidget(btn)
        project_layout.addLayout(project_btns)

        sidebar_layout.addWidget(project_section)
        sidebar_layout.addSpacing(12)

        # 设置按钮
        btn_settings = QPushButton("设置")
        btn_settings.setCursor(Qt.PointingHandCursor)
        btn_settings.setProperty("nav_style", "true")
        btn_settings.setStyleSheet(f"""
            QPushButton[nav_style="true"] {{
                text-align: left; padding: 10px 14px;
                border: 1px solid {Theme.colors.border};
                border-radius: {Theme.radius.sm}px;
                color: {Theme.colors.text_secondary};
                background: {Theme.colors.surface};
                font-size: {Theme.fonts.size_md}px;
            }}
            QPushButton[nav_style="true"]:hover {{
                background: {Theme.colors.bg};
                color: {Theme.colors.text_primary};
            }}
        """)
        btn_settings.clicked.connect(self._open_settings)
        sidebar_layout.addWidget(btn_settings)

        layout.addWidget(self._sidebar)

        # ── 右侧内容区 ──
        content = QWidget()
        content.setStyleSheet(f"background: {Theme.colors.bg};")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(20, 20, 20, 20)
        content_layout.setSpacing(0)

        self._stack = QStackedWidget()
        self._stack.setStyleSheet(f"background: {Theme.colors.bg};")
        self._stack.setSizePolicy(
            QSizePolicy.Expanding, QSizePolicy.Expanding
        )

        self.manuscript_panel = ManuscriptPanel(self._project)
        self._stack.addWidget(self.manuscript_panel)

        self.synthesis_panel = SynthesisPanel(self._project)
        self._stack.addWidget(self.synthesis_panel)

        self.subtitle_panel = SubtitlePanel(self._project)
        self.subtitle_panel.set_manuscript_source(
            self.manuscript_panel.get_text
        )
        self._stack.addWidget(self.subtitle_panel)

        content_layout.addWidget(self._stack)
        layout.addWidget(content)

        # 信号链
        self.manuscript_panel.sentences_ready.connect(
            self.synthesis_panel.set_sentences
        )
        self.synthesis_panel.synthesis_done.connect(self._on_synthesis_done)
        self.synthesis_panel.merge_done.connect(self._on_merge_done)

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

    def _update_project_label(self):
        if hasattr(self, "_project_label") and self._project:
            self._project_label.setText(f"工程: {self._project.name}")
            self.setWindowTitle(f"IndexTTS Studio - {self._project.name}")

    def _new_project(self):
        name, ok = QInputDialog.getText(self, "新建工程", "工程名称:")
        if not ok or not name.strip():
            return
        name = name.strip()
        project_dir = os.path.join(os.getcwd(), "projects", name)
        if os.path.exists(project_dir):
            QMessageBox.warning(self, "工程已存在", f"工程「{name}」已存在。")
            return
        new_project = Project(project_dir=project_dir, name=name)
        new_project.ensure_dirs()
        new_project.save()
        self._switch_project(new_project, is_new=True)
        self.status_bar.showMessage(f"已新建工程: {name}")

    def _open_project(self):
        project_dir = QFileDialog.getExistingDirectory(
            self, "打开工程", os.path.join(os.getcwd(), "projects")
        )
        if not project_dir:
            return
        loaded = Project.load(project_dir)
        if loaded is None:
            # 如果目录没有 project.json，询问是否在此创建新工程
            reply = QMessageBox.question(
                self, "创建工程",
                f"所选目录没有工程文件，是否在此创建新工程？\n{project_dir}"
            )
            if reply == QMessageBox.Yes:
                name = os.path.basename(project_dir)
                loaded = Project(project_dir=project_dir, name=name)
                loaded.ensure_dirs()
                loaded.save()
            else:
                return
        self._switch_project(loaded)
        self.status_bar.showMessage(f"已打开工程: {loaded.name}")

    def _save_project(self):
        if hasattr(self, "_project"):
            self._project.save()
            self.status_bar.showMessage(f"工程已保存: {self._project.name}")

    def _switch_project(self, project: Project, is_new: bool = False):
        """切换当前工程并刷新所有面板。"""
        # 先保存旧工程
        if hasattr(self, "_project") and self._project:
            self._project.save()
        self._project = project
        logger.info("切换到工程: %s", self._project.to_dict())

        # 更新各面板
        self.manuscript_panel.set_project(self._project)
        self.synthesis_panel.set_project(self._project)
        self.subtitle_panel.set_project(self._project)

        if is_new:
            self.manuscript_panel.reset_for_new_project()
            self.synthesis_panel.reset_for_new_project()
            self.subtitle_panel.reset_for_new_project()

        self._update_project_label()

    def _on_synthesis_done(self, output_dir: str):
        """合成完成后更新状态，由用户手动点击合并生成完整音频和字幕。"""
        self.status_bar.showMessage(
            f"合成完成，共 {len(self._project.sentences)} 句，可点击「合并完整音频」"
        )

    def _on_merge_done(self, entries: list):
        """合并完成后加载字幕并跳转到字幕页。"""
        self.subtitle_panel.load_entries(entries)
        self._set_current_page(2)
        self.status_bar.showMessage(f"字幕已生成并加载: {len(entries)} 条")

    def closeEvent(self, event):
        self._save_window_state()
        if hasattr(self, "_project"):
            self._project.save()
            logger.info("工程已保存: %s", self._project.project_dir)
        super().closeEvent(event)

    def _apply_stylesheet(self):
        self.setStyleSheet(global_stylesheet() + f"""
            QStatusBar {{
                background: {Theme.colors.surface};
                color: {Theme.colors.text_secondary};
                border-top: 1px solid {Theme.colors.border};
            }}
        """)
