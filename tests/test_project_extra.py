"""Project 工程数据边界情况测试"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from index_tts_gui.core.project import Project


def test_audio_list_save_and_load(tmp_path):
    """audio_list 写入 project.json 后能正确加载。"""
    project_dir = str(tmp_path / "proj")
    audio_list = [
        {"id": 1, "name": "intro.wav", "duration": 1.2},
        {"id": 2, "name": "outro.wav", "duration": 2.5},
    ]
    p = Project(project_dir=project_dir, audio_list=audio_list)
    p.save()

    loaded = Project.load(project_dir)
    assert loaded is not None
    assert loaded.audio_list == audio_list


def test_pauses_for_sentences_save_and_load(tmp_path):
    """pauses_for_sentences 写入 project.json 后能正确加载。"""
    project_dir = str(tmp_path / "proj")
    pauses_for_sentences = ["0.5", "1.0", "0.0"]
    p = Project(project_dir=project_dir, pauses_for_sentences=pauses_for_sentences)
    p.save()

    loaded = Project.load(project_dir)
    assert loaded is not None
    assert loaded.pauses_for_sentences == pauses_for_sentences


def test_wav_map_save_and_load(tmp_path):
    """wav_map 写入 project.json 后能正确加载。"""
    project_dir = str(tmp_path / "proj")
    wav_map = [
        {"sentence_index": 0, "wav_path": "output_tts/sentence_01.wav"},
        {"sentence_index": 1, "wav_path": "output_tts/sentence_02.wav"},
    ]
    p = Project(project_dir=project_dir, wav_map=wav_map)
    p.save()

    loaded = Project.load(project_dir)
    assert loaded is not None
    assert loaded.wav_map == wav_map


def test_switching_project_persists_sentences(tmp_path):
    """切换工程后，目标工程的 sentences 能正确持久化，原工程不受影响。"""
    proj_a_dir = str(tmp_path / "projects" / "A")
    proj_b_dir = str(tmp_path / "projects" / "B")

    proj_a = Project(project_dir=proj_a_dir, name="A", sentences=["A1", "A2"])
    proj_a.save()

    proj_b = Project(project_dir=proj_b_dir, name="B", sentences=["B1"])
    proj_b.save()

    # 模拟切换到 B 并修改句子
    loaded_b = Project.load(proj_b_dir)
    assert loaded_b is not None
    loaded_b.sentences = ["B1", "B2"]
    loaded_b.save()

    # 重新加载 B 验证持久化
    reloaded_b = Project.load(proj_b_dir)
    assert reloaded_b is not None
    assert reloaded_b.sentences == ["B1", "B2"]

    # A 不应受影响
    reloaded_a = Project.load(proj_a_dir)
    assert reloaded_a is not None
    assert reloaded_a.sentences == ["A1", "A2"]


def test_load_missing_project_returns_none(tmp_path):
    """project.json 不存在时 load 返回 None。"""
    project_dir = str(tmp_path / "non_existent")
    assert Project.load(project_dir) is None


def test_load_corrupted_project_returns_none(tmp_path):
    """project.json 损坏时 load 返回 None 而不是抛异常。"""
    project_dir = str(tmp_path / "proj")
    os.makedirs(project_dir)
    project_file = os.path.join(project_dir, "project.json")
    with open(project_file, "w", encoding="utf-8") as f:
        f.write("{ invalid json }")

    loaded = Project.load(project_dir)
    assert loaded is None


def test_save_atomic_write_cleans_temp_on_replace_failure(tmp_path, monkeypatch):
    """save() 在 os.replace 失败时应清理临时文件，并保留原 project.json。"""
    project_dir = str(tmp_path / "proj")
    p = Project(project_dir=project_dir, name="test")
    p.save()

    def failing_replace(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", failing_replace)

    with pytest.raises(RuntimeError):
        p.save()

    temp_file = os.path.join(project_dir, "project.json.tmp")
    assert not os.path.exists(temp_file)
    assert os.path.exists(os.path.join(project_dir, "project.json"))


def test_create_default_skips_migration_when_already_exists(tmp_path):
    """default 工程已存在时，create_default 不应再迁移旧数据。"""
    root = str(tmp_path / "root")
    os.makedirs(root)

    # 先创建已存在的 default 工程
    project_dir = os.path.join(root, "projects", "default")
    os.makedirs(project_dir)
    existing = Project(project_dir=project_dir, name="default", sentences=["existing"])
    existing.save()

    # 放置旧数据
    split_file = os.path.join(root, "split_result.txt")
    with open(split_file, "w", encoding="utf-8") as f:
        f.write("old sentence。\n")

    old_output = os.path.join(root, "output_tts")
    os.makedirs(old_output)
    old_file = os.path.join(old_output, "sentence_01.wav")
    with open(old_file, "w") as f:
        f.write("dummy")

    project = Project.create_default(root)
    assert project.sentences == ["existing"]
    assert not os.path.exists(os.path.join(project.output_dir, "sentence_01.wav"))


def test_create_default_migration_skips_existing_destination(tmp_path):
    """迁移旧 output_tts 时，目标位置已存在的文件应被跳过。"""
    root = str(tmp_path / "root")
    os.makedirs(root)

    old_output = os.path.join(root, "output_tts")
    os.makedirs(old_output)
    old_file = os.path.join(old_output, "sentence_01.wav")
    with open(old_file, "w") as f:
        f.write("old content")

    # 预先在目标位置创建同名文件
    project_dir = os.path.join(root, "projects", "default")
    os.makedirs(project_dir)
    output_tts = os.path.join(project_dir, "output_tts")
    os.makedirs(output_tts)
    existing_file = os.path.join(output_tts, "sentence_01.wav")
    with open(existing_file, "w") as f:
        f.write("existing content")

    project = Project.create_default(root)
    assert os.path.exists(existing_file)
    with open(existing_file, "r") as f:
        assert f.read() == "existing content"
    # 旧文件因目标已存在未被迁移，旧目录仍保留
    assert os.path.exists(old_file)
