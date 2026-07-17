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


def test_smooth_tick_extrapolates_playhead(panel):
    """播放中：平滑计时器用墙钟外推播放头，而不是只跟随 positionChanged 的低频更新。"""
    from PySide6.QtMultimedia import QMediaPlayer
    from PySide6.QtTest import QTest

    panel._player.playbackState = lambda: QMediaPlayer.PlayingState
    panel._player.duration = lambda: 60000

    panel._on_position_changed(1000)  # 锚点 1.0s
    QTest.qWait(80)
    panel._on_smooth_tick()

    assert panel._timeline.playhead_time == pytest.approx(1.08, abs=0.04)


def test_smooth_tick_clamped_to_duration(panel):
    """外推位置不得超过音频总长。"""
    from PySide6.QtMultimedia import QMediaPlayer
    from PySide6.QtTest import QTest

    panel._player.playbackState = lambda: QMediaPlayer.PlayingState
    panel._player.duration = lambda: 1100

    panel._on_position_changed(1050)
    QTest.qWait(80)
    panel._on_smooth_tick()

    assert panel._timeline.playhead_time <= 1.1


def test_smooth_tick_ignored_when_paused(panel):
    """非播放状态下平滑 tick 不移动播放头。"""
    from PySide6.QtMultimedia import QMediaPlayer

    panel._player.playbackState = lambda: QMediaPlayer.PausedState
    panel._player.duration = lambda: 60000

    panel._on_position_changed(1000)
    panel._timeline.set_playhead(1.0)
    panel._on_smooth_tick()

    assert panel._timeline.playhead_time == pytest.approx(1.0, abs=0.01)


def test_position_changed_does_not_snap_playhead_while_playing(panel):
    """播放中：后端延迟回报的小漂移位置不得把平滑前进的播放头拽回去。"""
    from PySide6.QtMultimedia import QMediaPlayer
    from PySide6.QtTest import QTest

    panel._player.playbackState = lambda: QMediaPlayer.PlayingState
    panel._player.duration = lambda: 60000

    panel._on_position_changed(1000)  # 锚点 1.0s
    QTest.qWait(80)
    panel._on_smooth_tick()
    assert panel._timeline.playhead_time == pytest.approx(1.08, abs=0.04)

    # 后端延迟，仍回报 1.0s（漂移 ~80ms，小于重同步阈值）
    panel._on_position_changed(1000)
    assert panel._timeline.playhead_time == pytest.approx(1.08, abs=0.04)


def test_position_changed_resyncs_on_large_drift(panel):
    """播放中：漂移超过阈值（如 seek）时锚点重同步，播放头跟到新位置。"""
    from PySide6.QtMultimedia import QMediaPlayer
    from PySide6.QtTest import QTest

    panel._player.playbackState = lambda: QMediaPlayer.PlayingState
    panel._player.duration = lambda: 60000

    panel._on_position_changed(1000)
    QTest.qWait(80)
    panel._on_smooth_tick()

    panel._on_position_changed(5000)  # seek 到 5s
    panel._on_smooth_tick()
    assert panel._timeline.playhead_time == pytest.approx(5.0, abs=0.05)


def test_smooth_timer_starts_and_stops_with_playback(panel):
    """播放时启动平滑计时器，暂停/停止时关闭。"""
    from PySide6.QtMultimedia import QMediaPlayer

    panel._on_playback_state_changed(QMediaPlayer.PlayingState)
    assert panel._smooth_timer.isActive()
    panel._on_playback_state_changed(QMediaPlayer.PausedState)
    assert not panel._smooth_timer.isActive()


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
