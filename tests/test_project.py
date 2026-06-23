"""测试 Project 工程数据管理"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from index_tts_gui.core.project import Project


def test_project_output_dir(tmp_path):
    project_dir = str(tmp_path / "proj")
    p = Project(project_dir=project_dir)
    assert p.output_dir == os.path.join(project_dir, "output_tts")


def test_project_save_and_load(tmp_path):
    project_dir = str(tmp_path / "proj")
    p = Project(
        project_dir=project_dir,
        name="test",
        source_text="你好。",
        sentences=["你好。", "世界。"],
        audio_name="ref.wav",
    )
    p.save()

    assert os.path.exists(os.path.join(project_dir, "project.json"))
    assert os.path.exists(os.path.join(project_dir, "output_tts"))

    loaded = Project.load(project_dir)
    assert loaded is not None
    assert loaded.name == "test"
    assert loaded.source_text == "你好。"
    assert loaded.sentences == ["你好。", "世界。"]
    assert loaded.audio_name == "ref.wav"


def test_create_default_migrates_old_output(tmp_path):
    """测试首次创建 default 工程时迁移旧的 output_tts。"""
    root = str(tmp_path / "root")
    os.makedirs(root)
    old_output = os.path.join(root, "output_tts")
    os.makedirs(old_output)
    old_file = os.path.join(old_output, "sentence_01.wav")
    with open(old_file, "w") as f:
        f.write("dummy")

    project = Project.create_default(root)
    assert project.output_dir == os.path.join(root, "projects", "default", "output_tts")
    assert os.path.exists(os.path.join(project.output_dir, "sentence_01.wav"))


def test_create_default_migrates_split_result(tmp_path):
    """测试首次创建 default 工程时迁移 split_result.txt。"""
    root = str(tmp_path / "root")
    os.makedirs(root)
    split_file = os.path.join(root, "split_result.txt")
    with open(split_file, "w", encoding="utf-8") as f:
        f.write("第一句。\n第二句\n")

    project = Project.create_default(root)
    assert project.sentences == ["第一句。", "第二句"]


def test_project_save_and_load_pauses(tmp_path):
    project_dir = str(tmp_path / "proj")
    p = Project(
        project_dir=project_dir,
        name="test",
        sentences=["A", "B"],
        pauses=[0.5, 0.0],
    )
    p.save()

    loaded = Project.load(project_dir)
    assert loaded is not None
    assert loaded.pauses == [0.5, 0.0]


def test_project_switching_persists_sentences(tmp_path):
    """测试切换工程后句子能正确持久化到新工程。"""
    proj_a_dir = str(tmp_path / "projects" / "A")
    proj_b_dir = str(tmp_path / "projects" / "B")

    proj_a = Project(project_dir=proj_a_dir, name="A", sentences=["A1", "A2"])
    proj_a.save()

    proj_b = Project(project_dir=proj_b_dir, name="B", sentences=["B1"])
    proj_b.save()

    # 模拟切换到 B 后修改句子并保存
    proj_b.sentences = ["B1", "B2", "B3"]
    proj_b.save()

    # 重新加载 B 验证持久化
    loaded_b = Project.load(proj_b_dir)
    assert loaded_b is not None
    assert loaded_b.sentences == ["B1", "B2", "B3"]

    # A 不应受影响
    loaded_a = Project.load(proj_a_dir)
    assert loaded_a is not None
    assert loaded_a.sentences == ["A1", "A2"]
