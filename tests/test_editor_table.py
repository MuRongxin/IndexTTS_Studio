"""测试 ManuscriptPanel 的 QTableWidget 编辑行为"""
import sys
import os

import pytest
import tempfile
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from index_tts_gui.ui.editor import ManuscriptPanel, SentenceLineEdit
from index_tts_gui.core.project import Project


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
    p = ManuscriptPanel(project)
    p.show()
    return p


def test_table_shows_sentences_with_index(panel):
    panel._load_table(["第一句。", "第二句", "第三句！"])
    assert panel._table.rowCount() == 3
    assert panel._table.item(0, 0).text() == "1"
    assert panel._table.item(1, 0).text() == "2"
    assert panel._table.item(2, 1).text() == "第三句！"


def test_index_column_is_not_editable(panel):
    panel._load_table(["第一句。"])
    idx_item = panel._table.item(0, 0)
    assert not (idx_item.flags() & Qt.ItemIsEditable)


def test_split_at_cursor(panel):
    panel._load_table(["前半句后半句"])
    panel._table.setCurrentCell(0, 1)
    panel._split_at_cursor(3)

    sentences = panel.get_sentences()
    assert sentences == ["前半句", "后半句"]


def test_merge_with_previous_adds_punctuation(panel):
    panel._load_table(["第一句", "第二句"])
    panel._table.setCurrentCell(1, 1)
    panel._merge_with_previous()

    sentences = panel.get_sentences()
    assert sentences == ["第一句。第二句"]


def test_merge_with_previous_keeps_punctuation(panel):
    panel._load_table(["第一句。", "第二句"])
    panel._table.setCurrentCell(1, 1)
    panel._merge_with_previous()

    sentences = panel.get_sentences()
    assert sentences == ["第一句。第二句"]


def test_indices_update_after_split_with_many_rows(panel):
    """确保行数超过 9 时，切分后所有序号连续更新。"""
    sentences = [f"句子{i}。" for i in range(1, 13)]  # 12 句
    panel._load_table(sentences)
    panel._table.setCurrentCell(5, 1)
    panel._split_at_cursor(2)

    assert panel._table.rowCount() == 13
    for i in range(13):
        assert panel._table.item(i, 0).text() == str(i + 1)


def test_indices_update_after_merge_with_many_rows(panel):
    """确保行数超过 9 时，合并后所有序号连续更新。"""
    sentences = [f"句子{i}。" for i in range(1, 13)]  # 12 句
    panel._load_table(sentences)
    panel._table.setCurrentCell(6, 1)
    panel._merge_with_previous()

    assert panel._table.rowCount() == 11
    for i in range(11):
        assert panel._table.item(i, 0).text() == str(i + 1)
