import pytest

from index_tts_gui.core.subtitle import (
    SubtitleEntry,
    SubtitleItem,
    SubtitleStyle,
    SubtitleTrack,
    parse_time_str,
    seconds_to_time_str,
)


def test_subtitle_item_duration_and_validity():
    item = SubtitleItem(index=1, start_time=1.0, end_time=3.5, text="hello")
    assert item.duration == 2.5
    assert item.is_valid()

    item.end_time = item.start_time
    assert item.duration == 0.0
    assert not item.is_valid()


def test_subtitle_style_copy():
    style = SubtitleStyle(font_size=32, primary_color="#FF0000")
    copied = style.copy()
    assert copied.font_size == 32
    assert copied.primary_color == "#FF0000"
    copied.font_size = 40
    assert style.font_size == 32


def test_track_add_and_sort():
    track = SubtitleTrack()
    track.add_item(SubtitleItem(0, 5.0, 6.0, "second"))
    track.add_item(SubtitleItem(0, 1.0, 2.0, "first"))
    track.add_item(SubtitleItem(0, 3.0, 4.0, "middle"))

    assert [item.index for item in track.items] == [1, 2, 3]
    assert [item.start_time for item in track.items] == [1.0, 3.0, 5.0]


def test_track_get_item_at_time():
    track = SubtitleTrack()
    track.add_item(SubtitleItem(0, 1.0, 3.0, "A"))
    track.add_item(SubtitleItem(0, 3.0, 5.0, "B"))

    assert track.get_item_at_time(2.0).text == "A"
    assert track.get_item_at_time(3.0).text == "B"
    assert track.get_item_at_time(0.5) is None


def test_track_split_item():
    track = SubtitleTrack()
    track.add_item(SubtitleItem(0, 0.0, 10.0, "hello world"))
    track.split_item(1, 4.0)

    assert track.count == 2
    assert track.items[0].end_time == 4.0
    assert track.items[1].start_time == 4.0
    assert track.items[0].text == "hello"
    assert track.items[1].text == "world"


def test_track_merge_items():
    track = SubtitleTrack()
    track.add_item(SubtitleItem(0, 0.0, 2.0, "hello"))
    track.add_item(SubtitleItem(0, 2.0, 5.0, "world"))
    track.merge_items(1, 2)

    assert track.count == 1
    assert track.items[0].start_time == 0.0
    assert track.items[0].end_time == 5.0
    assert track.items[0].text == "hello world"


def test_track_remove_item():
    track = SubtitleTrack()
    track.add_item(SubtitleItem(0, 0.0, 1.0, "A"))
    track.add_item(SubtitleItem(0, 1.0, 2.0, "B"))
    track.remove_item(1)

    assert track.count == 1
    assert track.items[0].index == 1
    assert track.items[0].text == "B"


def test_track_from_and_to_entries():
    entries = [
        SubtitleEntry(1, 1.0, 2.0, "first"),
        SubtitleEntry(2, 3.0, 4.0, "second"),
    ]
    track = SubtitleTrack.from_entries(entries)
    assert track.count == 2
    assert track.items[1].text == "second"

    out = track.to_entries()
    assert len(out) == 2
    assert out[0].start_sec == 1.0
    assert out[1].end_sec == 4.0


def test_seconds_to_time_str():
    assert seconds_to_time_str(3661.123) == "01:01:01.123"
    assert seconds_to_time_str(0.0) == "00:00:00.000"
    assert seconds_to_time_str(-5.0) == "00:00:00.000"


def test_parse_time_str():
    assert parse_time_str("01:01:01.123") == pytest.approx(3661.123, abs=0.001)
    assert parse_time_str("01:01:01,123") == pytest.approx(3661.123, abs=0.001)
    assert parse_time_str("61.5") == pytest.approx(61.5, abs=0.001)
    assert parse_time_str("01:30.5") == pytest.approx(90.5, abs=0.001)
    assert parse_time_str("") == -1.0
    assert parse_time_str("abc") == -1.0
