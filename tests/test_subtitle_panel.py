"""测试 SubtitlePanel 的数据加载与表格行为"""
import sys
import os

import pytest
from PySide6.QtWidgets import QApplication, QTableWidgetSelectionRange

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from index_tts_gui.ui.subtitle_view import SubtitlePanel
from index_tts_gui.core.project import Project
from index_tts_gui.core.subtitle import SubtitleEntry


@pytest.fixture(scope="session")
def app():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    yield app


@pytest.fixture
def panel(app, tmp_path):
    project_dir = tmp_path / "test_project"
    project = Project(project_dir=str(project_dir))
    p = SubtitlePanel(project)
    p.show()
    return p


def test_load_entries_populates_table(panel):
    entries = [
        SubtitleEntry(1, 1.0, 3.0, "第一句"),
        SubtitleEntry(2, 3.0, 5.5, "第二句"),
    ]
    panel.load_entries(entries)

    assert panel._table.rowCount() == 2
    assert panel._table.item(0, 0).text() == "1"
    assert panel._table.item(0, 4).text() == "第一句"
    assert "00:00:05.500" in panel._table.item(1, 2).text()


def test_table_has_five_columns(panel):
    assert panel._table.columnCount() == 5


def test_select_row_loads_text_editor(panel):
    entries = [SubtitleEntry(1, 0.0, 2.0, "测试文本")]
    panel.load_entries(entries)
    panel.select_row(1)

    assert panel._current_edit_index == 1
    assert panel._text_edit.toPlainText() == "测试文本"


def test_split_selected(panel):
    entries = [SubtitleEntry(1, 0.0, 10.0, "前半句后半句")]
    panel.load_entries(entries)
    panel.select_row(1)
    panel._split_selected()

    assert panel._track.count == 2
    assert panel._table.rowCount() == 2


def test_merge_selected(panel):
    entries = [
        SubtitleEntry(1, 0.0, 2.0, "第一句"),
        SubtitleEntry(2, 2.0, 4.0, "第二句"),
    ]
    panel.load_entries(entries)
    panel._table.setRangeSelected(QTableWidgetSelectionRange(0, 0, 1, 4), True)
    panel._merge_selected()

    assert panel._track.count == 1
    assert panel._table.rowCount() == 1
    assert "第一句" in panel._track.items[0].text
    assert "第二句" in panel._track.items[0].text


def test_get_entries_roundtrip(panel):
    entries = [
        SubtitleEntry(1, 0.0, 2.0, "A"),
        SubtitleEntry(2, 2.0, 4.0, "B"),
    ]
    panel.load_entries(entries)
    out = panel.get_entries()
    assert len(out) == 2
    assert out[0].text == "A"
    assert out[1].start_sec == 2.0
