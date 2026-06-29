"""工程（Project）数据管理。

一个工程对应一次配音任务，包含：
- 原始文稿
- 拆分后的句子
- 参考音频/音色名
- 该工程的合成输出目录

全局配置（API key、provider 等）仍由 config.json 管理，不属于工程。
"""
from __future__ import annotations

import json
import logging
import os
import shutil
from datetime import datetime
from typing import Any


logger = logging.getLogger("index_tts")


PROJECT_FILE = "project.json"
DEFAULT_PROJECT_NAME = "default"
DEFAULT_PROJECTS_DIR = "projects"
DEFAULT_OUTPUT_SUBDIR = "output_tts"


class Project:
    """TTS 配音工程。"""

    def __init__(
        self,
        project_dir: str,
        name: str = DEFAULT_PROJECT_NAME,
        source_text: str = "",
        sentences: list[str] | None = None,
        audio_name: str = "",
        pauses: list[float] | None = None,
        audio_list: list[dict] | None = None,
        wav_map: list[dict] | None = None,
        subtitles: list[dict] | None = None,
        created_at: str | None = None,
        updated_at: str | None = None,
    ):
        self.project_dir = project_dir
        self.name = name
        self.source_text = source_text
        self.sentences = list(sentences) if sentences else []
        self.audio_name = audio_name
        self.pauses = list(pauses) if pauses else []
        self.audio_list: list[dict] = list(audio_list) if audio_list else []
        self.wav_map: list[dict] = list(wav_map) if wav_map else []
        self.subtitles: list[dict] = list(subtitles) if subtitles else []
        self.created_at = created_at or datetime.now().isoformat()
        self.updated_at = updated_at or datetime.now().isoformat()

    @property
    def output_dir(self) -> str:
        """该工程的合成输出目录。"""
        return os.path.join(self.project_dir, DEFAULT_OUTPUT_SUBDIR)

    @property
    def project_file_path(self) -> str:
        return os.path.join(self.project_dir, PROJECT_FILE)

    def ensure_dirs(self):
        """确保工程目录和输出目录存在。"""
        os.makedirs(self.project_dir, exist_ok=True)
        os.makedirs(self.output_dir, exist_ok=True)

    def save(self):
        """把工程状态原子地写入 project.json。"""
        self.ensure_dirs()
        self.updated_at = datetime.now().isoformat()
        data = {
            "version": 1,
            "name": self.name,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "source_text": self.source_text,
            "sentences": self.sentences,
            "audio_name": self.audio_name,
            "audio_list": self.audio_list,
            "wav_map": self.wav_map,
            "pauses": self.pauses,
            "subtitles": self.subtitles,
        }
        tmp_path = self.project_file_path + ".tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            os.replace(tmp_path, self.project_file_path)
        except Exception as e:
            logger.exception("保存工程失败: %s", self.project_file_path)
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass
            raise RuntimeError(f"保存工程失败: {e}") from e

    @classmethod
    def load(cls, project_dir: str) -> Project | None:
        """从 project_dir 加载工程，失败返回 None。"""
        path = os.path.join(project_dir, PROJECT_FILE)
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return cls(
                project_dir=project_dir,
                name=data.get("name", DEFAULT_PROJECT_NAME),
                source_text=data.get("source_text", ""),
                sentences=data.get("sentences", []),
                audio_name=data.get("audio_name", ""),
                audio_list=data.get("audio_list", []),
                wav_map=data.get("wav_map", []),
                pauses=data.get("pauses", []),
                subtitles=data.get("subtitles", []),
                created_at=data.get("created_at"),
                updated_at=data.get("updated_at"),
            )
        except Exception as e:
            logger.warning("加载工程失败: %s - %s", path, e)
            return None

    @classmethod
    def create_default(cls, root_dir: str) -> Project:
        """
        在项目根目录创建/加载默认工程。

        首次创建时会做一次性旧数据迁移：
        - split_result.txt -> sentences
        - output_tts/ -> projects/default/output_tts/
        """
        projects_dir = os.path.join(root_dir, DEFAULT_PROJECTS_DIR)
        project_dir = os.path.join(projects_dir, DEFAULT_PROJECT_NAME)
        os.makedirs(project_dir, exist_ok=True)

        # 尝试加载已有工程
        project = cls.load(project_dir)
        if project is not None:
            return project

        # 新建工程，并做旧数据迁移
        project = cls(project_dir=project_dir, name=DEFAULT_PROJECT_NAME)

        # 迁移 split_result.txt
        old_split_file = os.path.join(root_dir, "split_result.txt")
        if os.path.exists(old_split_file):
            try:
                with open(old_split_file, "r", encoding="utf-8") as f:
                    project.sentences = [
                        line.strip() for line in f if line.strip()
                    ]
            except Exception as e:
                logger.warning("迁移 split_result.txt 失败: %s", e)

        # 迁移旧的 output_tts/ 到工程目录
        old_output_dir = os.path.join(root_dir, DEFAULT_OUTPUT_SUBDIR)
        if os.path.exists(old_output_dir) and os.path.isdir(old_output_dir):
            try:
                if os.listdir(old_output_dir):
                    os.makedirs(project.output_dir, exist_ok=True)
                    for name in os.listdir(old_output_dir):
                        src = os.path.join(old_output_dir, name)
                        dst = os.path.join(project.output_dir, name)
                        if os.path.exists(dst):
                            logger.warning(
                                "迁移旧 output_tts 时目标已存在，跳过: %s", name
                            )
                            continue
                        shutil.move(src, dst)
                    # 如果旧目录已空则删除
                    try:
                        os.rmdir(old_output_dir)
                    except OSError:
                        pass
            except Exception as e:
                # 迁移失败也不影响使用，直接创建新的输出目录
                logger.warning("迁移旧 output_tts 失败: %s", e)

        project.ensure_dirs()
        project.save()
        return project

    def to_dict(self) -> dict[str, Any]:
        """用于调试。"""
        return {
            "name": self.name,
            "project_dir": self.project_dir,
            "output_dir": self.output_dir,
            "source_text_len": len(self.source_text),
            "sentences_count": len(self.sentences),
            "audio_name": self.audio_name,
            "pauses_count": len(self.pauses),
        }
