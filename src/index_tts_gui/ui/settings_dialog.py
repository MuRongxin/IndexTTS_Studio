"""设置对话框：API 服务商、地址、超时。"""
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
    QComboBox, QLineEdit, QSpinBox, QPushButton,
    QLabel, QDialogButtonBox, QGroupBox, QCheckBox,
    QTextEdit,
)
from PySide6.QtCore import Qt

from index_tts_gui.core.tts_client import (
    DEFAULT_API_URL,
    DEFAULT_TIMEOUT,
    create_client,
    list_providers,
)
from index_tts_gui.core.splitter import (
    LLM_PRESETS,
    DEFAULT_LLM_PROMPT,
    DEFAULT_MAX_LENGTH,
)
from index_tts_gui.ui.log_viewer import LogViewerDialog


class SettingsDialog(QDialog):
    """应用设置对话框。"""

    def __init__(self, config: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("设置")
        self.setMinimumWidth(420)
        self._config = config
        self._setup_ui()
        self._load_config()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(16)
        layout.setContentsMargins(20, 20, 20, 20)

        # 提示
        tip = QLabel("修改 API 配置后保存即可生效。服务商选择决定客户端协议实现。")
        tip.setWordWrap(True)
        tip.setStyleSheet("color: #666; font-size: 12px;")
        layout.addWidget(tip)

        form = QFormLayout()
        form.setSpacing(12)

        # Provider
        self._provider_combo = QComboBox()
        self._provider_combo.addItems(list_providers())
        form.addRow("服务商:", self._provider_combo)

        # API URL
        self._api_input = QLineEdit()
        self._api_input.setPlaceholderText(DEFAULT_API_URL)
        form.addRow("API URL:", self._api_input)

        # Timeouts
        self._timeout_spin = {}
        timeout_layout = QHBoxLayout()
        timeout_layout.setSpacing(8)
        for name, label in [
            ("check", "检查"),
            ("upload", "上传"),
            ("synthesize", "合成"),
        ]:
            spin = QSpinBox()
            spin.setRange(1, 600)
            spin.setSuffix(" 秒")
            self._timeout_spin[name] = spin
            col = QVBoxLayout()
            col.setSpacing(2)
            col.addWidget(QLabel(label))
            col.addWidget(spin)
            timeout_layout.addLayout(col)
        timeout_layout.addStretch()
        form.addRow("超时:", timeout_layout)

        # TTS API 连接测试
        self._tts_test_result = QLabel("")
        self._tts_test_result.setStyleSheet("color: #666; font-size: 12px;")
        self._tts_test_result.setWordWrap(True)

        btn_test_tts = QPushButton("🧪 测试 TTS API 连接")
        btn_test_tts.setStyleSheet("""
            QPushButton {
                background: #4caf50; color: white;
                padding: 6px 14px; border-radius: 4px;
            }
            QPushButton:hover { background: #388e3c; }
        """)
        btn_test_tts.clicked.connect(self._test_tts_connection)

        tts_test_layout = QHBoxLayout()
        tts_test_layout.addWidget(btn_test_tts)
        tts_test_layout.addWidget(self._tts_test_result, 1)
        form.addRow("检测:", tts_test_layout)

        layout.addLayout(form)

        # ── LLM 拆分设置 ──
        llm_group = QGroupBox("LLM 智能拆分")
        llm_layout = QFormLayout(llm_group)
        llm_layout.setSpacing(10)

        self._llm_enabled = QCheckBox("启用 LLM 拆分")
        self._llm_enabled.setToolTip("启用后可在文稿面板选择 LLM 或自动拆分模式")
        llm_layout.addRow(self._llm_enabled)

        self._llm_preset = QComboBox()
        self._llm_preset.addItem("自定义", "custom")
        for name in LLM_PRESETS:
            self._llm_preset.addItem(name.upper(), name)
        self._llm_preset.currentIndexChanged.connect(self._on_llm_preset_changed)
        llm_layout.addRow("预设:", self._llm_preset)

        self._llm_url = QLineEdit()
        self._llm_url.setPlaceholderText("https://api.deepseek.com")
        llm_layout.addRow("LLM API URL:", self._llm_url)

        self._llm_key = QLineEdit()
        self._llm_key.setEchoMode(QLineEdit.Password)
        llm_layout.addRow("API Key:", self._llm_key)

        self._llm_model = QComboBox()
        self._llm_model.setEditable(True)
        self._llm_model.setToolTip("选择 Flash（快/便宜）或 Pro（质量高），也可手动输入模型名")
        llm_layout.addRow("模型:", self._llm_model)

        self._llm_timeout = QSpinBox()
        self._llm_timeout.setRange(5, 300)
        self._llm_timeout.setSuffix(" 秒")
        llm_layout.addRow("超时:", self._llm_timeout)

        self._llm_max_completion_tokens = QSpinBox()
        self._llm_max_completion_tokens.setRange(64, 8192)
        self._llm_max_completion_tokens.setSingleStep(64)
        self._llm_max_completion_tokens.setSuffix(" tokens")
        llm_layout.addRow("最大输出:", self._llm_max_completion_tokens)

        self._llm_max_len = QSpinBox()
        self._llm_max_len.setRange(10, 200)
        self._llm_max_len.setSuffix(" 字")
        llm_layout.addRow("最大句长:", self._llm_max_len)

        self._llm_prompt = QTextEdit()
        self._llm_prompt.setMaximumHeight(120)
        self._llm_prompt.setPlaceholderText("提示模板，需包含 {text} 和 {max_length}")
        llm_layout.addRow("Prompt 模板:", self._llm_prompt)

        # 测试连接按钮
        self._btn_test_llm = QPushButton("🧪 测试 LLM 连接")
        self._btn_test_llm.setStyleSheet("""
            QPushButton {
                background: #4caf50; color: white;
                padding: 6px 14px; border-radius: 4px;
            }
            QPushButton:hover { background: #388e3c; }
        """)
        self._btn_test_llm.clicked.connect(self._test_llm_connection)
        llm_layout.addRow(self._btn_test_llm)

        self._llm_test_result = QLabel("")
        self._llm_test_result.setStyleSheet("color: #666; font-size: 12px;")
        self._llm_test_result.setWordWrap(True)
        llm_layout.addRow(self._llm_test_result)

        layout.addWidget(llm_group)
        layout.addStretch()

        # 按钮
        btn_box = QDialogButtonBox(
            QDialogButtonBox.Save | QDialogButtonBox.Cancel
        )
        btn_box.accepted.connect(self._on_save)
        btn_box.rejected.connect(self.reject)

        self._btn_defaults = QPushButton("恢复默认")
        self._btn_defaults.clicked.connect(self._load_defaults)
        btn_box.addButton(self._btn_defaults, QDialogButtonBox.ResetRole)

        self._btn_logs = QPushButton("📋 查看日志")
        self._btn_logs.clicked.connect(self._open_logs)
        btn_box.addButton(self._btn_logs, QDialogButtonBox.HelpRole)

        layout.addWidget(btn_box)

    def _open_logs(self):
        dialog = LogViewerDialog(self)
        dialog.exec()

    def _load_config(self):
        self._provider_combo.setCurrentText(
            self._config.get("provider", "index_tts")
        )
        self._api_input.setText(self._config.get("api_url", DEFAULT_API_URL))
        timeout = self._config.get("timeout", dict(DEFAULT_TIMEOUT))
        for name, spin in self._timeout_spin.items():
            spin.setValue(timeout.get(name, DEFAULT_TIMEOUT[name]))

        # LLM
        llm = self._config.get("llm", {})
        self._llm_enabled.setChecked(llm.get("enabled", True))
        preset = llm.get("preset", "deepseek")
        idx = self._llm_preset.findData(preset)
        if idx >= 0:
            self._llm_preset.setCurrentIndex(idx)
        self._llm_url.setText(llm.get("api_url", ""))
        self._llm_key.setText(llm.get("api_key", ""))
        # 先触发 preset 填充模型列表，再设置当前模型
        saved_model = llm.get("model", "")
        self._on_llm_preset_changed(self._llm_preset.currentIndex())
        preset = self._llm_preset.currentData()
        if preset and preset != "custom" and preset in LLM_PRESETS:
            allowed = LLM_PRESETS[preset]["models"]
            default = LLM_PRESETS[preset]["default_model"]
            if saved_model in allowed:
                self._llm_model.setCurrentText(saved_model)
            else:
                self._llm_model.setCurrentText(default)
        else:
            self._llm_model.setCurrentText(saved_model)
        self._llm_timeout.setValue(llm.get("timeout", 60))
        self._llm_max_completion_tokens.setValue(
            llm.get("max_completion_tokens", llm.get("max_tokens", 2048))
        )
        self._llm_max_len.setValue(llm.get("max_sentence_length", DEFAULT_MAX_LENGTH))
        self._llm_prompt.setPlainText(
            llm.get("user_prompt_template", DEFAULT_LLM_PROMPT)
        )

    def _load_defaults(self):
        self._provider_combo.setCurrentText("index_tts")
        self._api_input.setText(DEFAULT_API_URL)
        for name, spin in self._timeout_spin.items():
            spin.setValue(DEFAULT_TIMEOUT[name])

        self._llm_enabled.setChecked(True)
        self._llm_preset.setCurrentIndex(self._llm_preset.findData("deepseek"))
        self._on_llm_preset_changed(self._llm_preset.currentIndex())
        self._llm_key.clear()
        self._llm_timeout.setValue(60)
        self._llm_max_completion_tokens.setValue(2048)
        self._llm_max_len.setValue(DEFAULT_MAX_LENGTH)
        self._llm_prompt.setPlainText(DEFAULT_LLM_PROMPT)

    def _test_tts_connection(self):
        from PySide6.QtWidgets import QMessageBox

        url = self._api_input.text().strip()
        provider = self._provider_combo.currentText().strip()
        timeout = {
            name: spin.value()
            for name, spin in self._timeout_spin.items()
        }

        if not url:
            self._tts_test_result.setText("❌ 请先填写 API URL")
            self._tts_test_result.setStyleSheet("color: #d32f2f;")
            return

        self._tts_test_result.setText("正在检测…")
        self._tts_test_result.setStyleSheet("color: #f57c00;")

        try:
            client = create_client(
                provider=provider,
                api_url=url,
                timeout=timeout,
            )
            msg = client.health_check()
            self._tts_test_result.setText(f"✅ {msg}")
            self._tts_test_result.setStyleSheet("color: #4caf50;")
        except Exception as e:
            self._tts_test_result.setText(f"❌ {e}")
            self._tts_test_result.setStyleSheet("color: #d32f2f;")

    def _test_llm_connection(self):
        from PySide6.QtWidgets import QMessageBox
        from index_tts_gui.core.llm_client import LLMClient

        url = self._llm_url.text().strip()
        key = self._llm_key.text().strip()
        model = self._llm_model.currentText().strip()

        if not url or not key or not model:
            self._llm_test_result.setText("❌ 请先填写 LLM API URL、API Key 和模型")
            self._llm_test_result.setStyleSheet("color: #d32f2f;")
            return

        self._llm_test_result.setText("正在测试…")
        self._llm_test_result.setStyleSheet("color: #f57c00;")

        try:
            client = LLMClient(api_url=url, api_key=key, model=model, timeout=15)
            msg = client.test_connection()
            self._llm_test_result.setText(f"✅ {msg}")
            self._llm_test_result.setStyleSheet("color: #4caf50;")
        except Exception as e:
            self._llm_test_result.setText(f"❌ {e}")
            self._llm_test_result.setStyleSheet("color: #d32f2f;")

    def _on_llm_preset_changed(self, index: int):
        preset = self._llm_preset.itemData(index)
        if preset and preset != "custom" and preset in LLM_PRESETS:
            self._apply_llm_preset(preset)

    def _apply_llm_preset(self, preset: str):
        cfg = LLM_PRESETS[preset]
        self._llm_url.setText(cfg["api_url"])

        self._llm_model.blockSignals(True)
        self._llm_model.clear()
        self._llm_model.addItems(cfg["models"])
        self._llm_model.setCurrentText(cfg["default_model"])
        self._llm_model.blockSignals(False)

    def _on_save(self):
        url = self._api_input.text().strip()
        if not url:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "配置错误", "API URL 不能为空")
            return

        self._config["provider"] = self._provider_combo.currentText().strip()
        self._config["api_url"] = url
        self._config["timeout"] = {
            name: spin.value()
            for name, spin in self._timeout_spin.items()
        }

        prompt = self._llm_prompt.toPlainText().strip()
        if not prompt or "{text}" not in prompt or "{max_length}" not in prompt:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(
                self, "Prompt 模板错误",
                "Prompt 模板必须包含 {text} 和 {max_length} 占位符"
            )
            return

        self._config["llm"] = {
            "enabled": self._llm_enabled.isChecked(),
            "preset": self._llm_preset.currentData(),
            "api_url": self._llm_url.text().strip(),
            "api_key": self._llm_key.text().strip(),
            "model": self._llm_model.currentText().strip(),
            "timeout": self._llm_timeout.value(),
            "max_completion_tokens": self._llm_max_completion_tokens.value(),
            "max_sentence_length": self._llm_max_len.value(),
            "user_prompt_template": prompt,
        }
        self.accept()

    def get_config(self) -> dict:
        return self._config
