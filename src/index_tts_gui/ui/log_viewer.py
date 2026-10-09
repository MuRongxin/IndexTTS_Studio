"""日志查看器对话框。"""
import os

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPlainTextEdit,
    QPushButton, QLabel, QFileDialog, QMessageBox,
)
from PySide6.QtCore import Qt

from index_tts_gui.core.logger import LOG_FILE


class LogViewerDialog(QDialog):
    """查看应用日志文件。"""

    # 日志轮转会保留 4×2MB，全量读入并 setPlainText 会长时间卡住界面，
    # 这里只展示末尾内容（先按字节回溯，再按行数截断）
    _MAX_BYTES = 512 * 1024
    _MAX_LINES = 2000

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("日志")
        self.resize(900, 600)
        self._setup_ui()
        self._load_log()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        top = QHBoxLayout()
        self._path_label = QLabel(f"日志文件: {LOG_FILE}")
        self._path_label.setStyleSheet("color: #666;")
        top.addWidget(self._path_label)
        top.addStretch()

        btn_refresh = QPushButton("🔄 刷新")
        btn_refresh.clicked.connect(self._load_log)
        top.addWidget(btn_refresh)

        btn_copy = QPushButton("📋 复制")
        btn_copy.clicked.connect(self._copy_log)
        top.addWidget(btn_copy)

        btn_export = QPushButton("📤 导出")
        btn_export.clicked.connect(self._export_log)
        top.addWidget(btn_export)

        layout.addLayout(top)

        self._text = QPlainTextEdit()
        self._text.setReadOnly(True)
        self._text.setStyleSheet("""
            QPlainTextEdit {
                background: #1e1e1e; color: #d4d4d4;
                font-family: monospace; font-size: 12px;
            }
        """)
        layout.addWidget(self._text)

        bottom = QHBoxLayout()
        bottom.addStretch()
        btn_close = QPushButton("关闭")
        btn_close.clicked.connect(self.accept)
        bottom.addWidget(btn_close)
        layout.addLayout(bottom)

    def _read_log_tail(self) -> tuple[str, bool]:
        """从文件尾回溯读取末尾日志，返回 (内容, 是否被截断)。"""
        with open(LOG_FILE, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            chunk = min(size, self._MAX_BYTES)
            f.seek(size - chunk, os.SEEK_SET)
            data = f.read(chunk)

        lines = data.decode("utf-8", errors="replace").splitlines()
        truncated = chunk < size
        if truncated and lines:
            # 命中字节上限时首行多半是被切断的半行
            lines = lines[1:]
        if len(lines) > self._MAX_LINES:
            lines = lines[-self._MAX_LINES:]
            truncated = True
        return "\n".join(lines), truncated

    def _load_log(self):
        try:
            content, truncated = self._read_log_tail()
            if truncated:
                content = (
                    f"（日志过长，仅显示末尾部分，完整内容见 {LOG_FILE}）\n"
                    + content
                )
            self._text.setPlainText(content)
            # 滚动到底部
            self._text.verticalScrollBar().setValue(
                self._text.verticalScrollBar().maximum()
            )
        except FileNotFoundError:
            self._text.setPlainText("暂无日志文件。")
        except Exception as e:
            self._text.setPlainText(f"读取日志失败: {e}")

    def _copy_log(self):
        from PySide6.QtWidgets import QApplication
        QApplication.clipboard().setText(self._text.toPlainText())
        QMessageBox.information(self, "已复制", "日志内容已复制到剪贴板")

    def _export_log(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "导出日志", "index_tts_studio.log", "日志文件 (*.log)"
        )
        if path:
            try:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(self._text.toPlainText())
                QMessageBox.information(self, "导出成功", f"已保存到 {path}")
            except Exception as e:
                QMessageBox.warning(self, "导出失败", str(e))
