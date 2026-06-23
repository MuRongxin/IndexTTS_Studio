"""启动脚本：python run.py"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from index_tts_gui.main import main
main()
