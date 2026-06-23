"""IndexTTS Studio 入口"""
import sys
import os

# 确保项目根在 path 里
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication
from index_tts_gui.core.logger import setup_logging
from index_tts_gui.ui.main_window import MainWindow


def main():
    setup_logging()

    app = QApplication(sys.argv)
    app.setApplicationName("IndexTTS Studio")
    app.setOrganizationName("IndexTTS")

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
