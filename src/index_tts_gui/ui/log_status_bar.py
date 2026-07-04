"""底部状态栏：滚动显示最新日志，双击打开日志窗口。"""
import logging

from PySide6.QtWidgets import QStatusBar, QLabel, QDialog
from PySide6.QtCore import Qt, Signal, QTimer, QObject

from index_tts_gui.ui.log_viewer import LogViewerDialog


class MarqueeLabel(QLabel):
    """当文本超过显示宽度时自动横向滚动的标签。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._full_text = ""
        self._offset = 0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._scroll_step)
        self._timer.setInterval(120)
        self.setTextInteractionFlags(Qt.NoTextInteraction)
        self.setStyleSheet("""
            QLabel {
                color: #333;
                padding-left: 4px;
                padding-right: 4px;
            }
        """)

    def setText(self, text: str):
        self._full_text = text
        self._offset = 0
        self._update_display()
        self._start_if_needed()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_display()
        self._start_if_needed()

    def _start_if_needed(self):
        if self._text_wider_than_widget():
            if not self._timer.isActive():
                self._timer.start()
        else:
            self._timer.stop()
            self._offset = 0
            self._update_display()

    def _text_wider_than_widget(self) -> bool:
        if not self._full_text:
            return False
        fm = self.fontMetrics()
        return fm.horizontalAdvance(self._full_text) > self.width() - 8

    def _update_display(self):
        if not self._text_wider_than_widget():
            super().setText(self._full_text)
            return

        # 留出空白间隔，循环滚动
        display = (self._full_text + "    " + self._full_text)[self._offset:]
        super().setText(display)

    def _scroll_step(self):
        if not self._full_text:
            return
        self._offset += 1
        cycle = len(self._full_text) + 4
        if self._offset >= cycle:
            self._offset = 0
        self._update_display()


class LogStatusBar(QStatusBar):
    """双击打开日志查看器，并滚动显示最新日志。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._log_label = MarqueeLabel(self)
        self._log_label.setText("就绪")
        self._log_label.setWordWrap(False)
        self.addWidget(self._log_label, 1)
        self.setFixedHeight(26)
        self.setStyleSheet("""
            QStatusBar {
                background: #fafafa;
                border-top: 1px solid #e0e0e0;
            }
        """)
        self.setToolTip("双击打开日志窗口")

    def show_log_message(self, message: str):
        """显示一条日志消息。"""
        self._log_label.setText(message)

    def mouseDoubleClickEvent(self, event):
        self._open_log_viewer()
        super().mouseDoubleClickEvent(event)

    def _open_log_viewer(self):
        dialog = LogViewerDialog(self.window())
        dialog.exec()


class QtLogHandler(QObject, logging.Handler):
    """把日志记录转发到 Qt 信号的 logging handler。"""

    log_record = Signal(str)

    def __init__(self, parent=None):
        QObject.__init__(self, parent)
        logging.Handler.__init__(self)
        self.setFormatter(
            logging.Formatter(
                "%(asctime)s [%(levelname)s] %(name)s - %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )

    def emit(self, record: logging.LogRecord):
        msg = self.format(record)
        self.log_record.emit(msg)
