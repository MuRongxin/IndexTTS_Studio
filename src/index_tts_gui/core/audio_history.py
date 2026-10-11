"""按完整句子文本保存不可变的合成音频，供混合版本字幕校准使用。"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import tempfile
from datetime import datetime

from index_tts_gui.core.merger import (
    collect_sentence_wavs, parse_sentence_wav_name, sanitize_for_filename,
)


logger = logging.getLogger("index_tts")
HISTORY_DIR = "audio_history"


def _text_dir(output_dir: str, text: str) -> str:
    key = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return os.path.join(output_dir, HISTORY_DIR, key)


def save_take(output_dir: str, wav_path: str, text: str, batch: str = "") -> str:
    """归档一份 WAV；相同文本、相同文件内容去重。失败抛错，禁止静默覆盖旧版。"""
    digest = hashlib.sha256()
    with open(wav_path, "rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    directory = _text_dir(output_dir, text)
    os.makedirs(directory, exist_ok=True)
    stem = digest.hexdigest()
    archived = os.path.join(directory, stem + ".wav")
    metadata_path = os.path.join(directory, stem + ".json")
    if os.path.isfile(archived) and os.path.isfile(metadata_path):
        return archived

    # 单独的临时文件避免中断后留下半个 WAV，也避免多个批次争用固定 .tmp。
    temporary = ""
    try:
        if not os.path.isfile(archived):
            with tempfile.NamedTemporaryFile(dir=directory, suffix=".tmp", delete=False) as target:
                temporary = target.name
                with open(wav_path, "rb") as source:
                    shutil.copyfileobj(source, target)
            os.replace(temporary, archived)
            temporary = ""
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=directory, suffix=".tmp", delete=False,
        ) as target:
            temporary = target.name
            json.dump({
                "version": 1,
                "text": text,
                "batch": batch,
                "created_at": datetime.now().isoformat(timespec="seconds"),
            }, target, ensure_ascii=False)
        os.replace(temporary, metadata_path)
        temporary = ""
    finally:
        if temporary and os.path.exists(temporary):
            os.remove(temporary)
    return archived


def archive_current_takes(output_dir: str, sentences: list[str], wav_map: list[dict] | None = None) -> int:
    """在清理当前产物前保存已有版本，优先使用工程映射中的完整文本。"""
    known = {
        entry.get("wav"): entry for entry in (wav_map or [])
        if isinstance(entry, dict) and entry.get("wav") and entry.get("text")
    }
    saved = 0
    for path in collect_sentence_wavs(output_dir):
        name = os.path.basename(path)
        entry = known.get(name)
        if entry is not None:
            text, batch = entry["text"], entry.get("batch", "")
        else:
            parsed = parse_sentence_wav_name(name)
            index = parsed[0] - 1 if parsed else -1
            if not 0 <= index < len(sentences):
                continue
            text, batch = sentences[index], ""
            # 文件名中的截断文本不足以还原旧文稿，只归档能确认归属的文件。
            if parsed[1] != sanitize_for_filename(text):
                continue
        save_take(output_dir, path, text, batch)
        saved += 1
    return saved


def candidate_takes(output_dir: str, text: str, current_wav: str) -> list[str]:
    """返回同文本的历史候选，排除当前 WAV 的归档副本和不完整记录。"""
    current_copy = save_take(output_dir, current_wav, text)
    directory = _text_dir(output_dir, text)
    candidates = []
    for name in sorted(os.listdir(directory)):
        if not name.endswith(".json"):
            continue
        path = os.path.join(directory, name[:-5] + ".wav")
        if path == current_copy or not os.path.isfile(path):
            continue
        try:
            with open(os.path.join(directory, name), encoding="utf-8") as source:
                metadata = json.load(source)
            if isinstance(metadata, dict) and metadata.get("text") == text:
                candidates.append(path)
        except (OSError, ValueError):
            logger.warning("忽略损坏的历史音频记录: %s", name)
    return candidates
