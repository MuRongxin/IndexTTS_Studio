"""
LLM 服务统一入口。

将原先分散在 LLMClient / LLMSplitter / LLMPauseAdvisor 中的逻辑
收敛为一个类，外部只需传入 config dict 即可调用所有 LLM 功能。
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from index_tts_gui.core.llm_client import LLMClient, LLMError


logger = logging.getLogger("index_tts")

# ── 预设 ──

LLM_PRESETS: dict[str, dict[str, Any]] = {
    "mimo": {
        "api_url": "https://api.xiaomimimo.com/v1",
        "models": ["mimo-v2.5", "mimo-v2.5-pro"],
        "default_model": "mimo-v2.5",
    },
    "deepseek": {
        "api_url": "https://api.deepseek.com",
        "models": ["deepseek-v4-flash", "deepseek-v4-pro"],
        "default_model": "deepseek-v4-flash",
    },
}

# ── Prompt 默认值 ──

DEFAULT_SPLIT_SYSTEM_PROMPT = (
    "你是一位中文 TTS 配音专家。"
    "你的任务是把文稿拆成适合语音合成的短句，必须按用户要求的格式直接输出句子。"
)

DEFAULT_SPLIT_PROMPT = """请将以下文稿按语义和朗读节奏拆分成适合单次 TTS 合成的句子。
要求：
1. 每句控制在 {max_length} 字以内（除非原文本身就是一句完整长句）；
2. 优先在语义完整、语气停顿处拆分；
3. 不要改写原文，保持原意；
4. 只输出句子，每行一句，不要编号、不要解释、不要加任何前缀；
5. 如果文稿只有一句话，也直接输出这句话。

文稿：
{text}"""

DEFAULT_PAUSE_PROMPT = """你是一位配音导演。以下文稿已被拆分成若干短句，每句将单独合成音频，最后需要拼接成完整音频。
请你为每一句之后建议一个停顿时长（秒），让整段配音听起来自然、有节奏感。

要求：
1. 仅输出一个 JSON 数组，数组长度必须等于句子数量；
2. 每个元素是对应句子**之后**的停顿秒数，最后一句必须为 0；
3. 数值范围 0.0 ~ 2.0，建议精确到 0.05；
4. 根据语义完整性、标点符号、情感转折决定停顿，不要每句都相同；
5. 不要输出任何解释、说明或 markdown 代码块。

句子列表：
{sentences_json}

请直接输出 JSON 数组："""


class LLMServiceError(RuntimeError):
    pass


class LLMService:
    """LLM 服务统一入口。

    从 config dict 读取配置，提供拆分与停顿建议。
    """

    def __init__(self, cfg: dict[str, Any] | None = None):
        self._cfg = cfg or {}

    # ── 配置读取 ──

    @property
    def api_url(self) -> str:
        preset = self._cfg.get("preset", "")
        if preset and preset in LLM_PRESETS:
            return self._cfg.get("api_url", "") or LLM_PRESETS[preset]["api_url"]
        return self._cfg.get("api_url", "")

    @property
    def api_key(self) -> str:
        preset = self._cfg.get("preset", "")
        key = self._cfg.get(f"{preset}_key", "") or self._cfg.get("api_key", "")
        return key.strip()

    @property
    def model(self) -> str:
        return self._cfg.get("model", "")

    @property
    def timeout(self) -> int:
        return self._cfg.get("timeout", 60)

    @property
    def max_completion_tokens(self) -> int:
        return self._cfg.get("max_completion_tokens", 2048)

    @property
    def max_sentence_length(self) -> int:
        return self._cfg.get("max_sentence_length", 30)

    @property
    def punctuation_fallback(self) -> bool:
        return self._cfg.get("punctuation_fallback", False)

    def is_configured(self) -> bool:
        return bool(self.api_url and self.api_key and self.model)

    def _make_client(self) -> LLMClient:
        if not self.is_configured():
            raise LLMServiceError("LLM 未配置：请检查 api_url / api_key / model")
        return LLMClient(
            api_url=self.api_url,
            api_key=self.api_key,
            model=self.model,
            timeout=self.timeout,
        )

    # ── 测试连接 ──

    def test(self) -> str:
        client = self._make_client()
        return client.test_connection()

    # ── 文本拆分 ──

    CHUNK_SIZE = 2000  # 每块最大字符数

    def split_text(
        self,
        text: str,
        max_length: int | None = None,
        on_progress: callable | None = None,
    ) -> list[str]:
        """用 LLM 将文稿拆分为句子列表。长文稿自动分块处理。

        Args:
            text: 原文
            max_length: 单句最大字数
            on_progress: 进度回调 (current: int, total: int, message: str)
        """
        stripped = text.strip()
        if not stripped:
            return []

        max_len = max_length or self.max_sentence_length

        # 短文稿直接发送
        if len(stripped) <= self.CHUNK_SIZE:
            if on_progress:
                on_progress(1, 1, "正在拆分…")
            return self._split_chunk(stripped, max_len)

        # 长文稿分块
        chunks = self._chunk_text(stripped)
        total = len(chunks)
        logger.info("LLMService.split: 分 %d 块处理 (text_len=%d)", total, len(stripped))

        all_sentences: list[str] = []
        for i, chunk in enumerate(chunks):
            msg = f"拆分第 {i+1}/{total} 块…"
            logger.info("LLMService.split: %s (%d 字)", msg, len(chunk))
            if on_progress:
                on_progress(i + 1, total, msg)
            sentences = self._split_chunk(chunk, max_len)
            all_sentences.extend(sentences)

        logger.info("LLMService.split: 共 %d 句", len(all_sentences))
        return all_sentences

    def _split_chunk(self, text: str, max_length: int) -> list[str]:
        """对单块文本执行 LLM 拆分。"""
        system_prompt = self._cfg.get("system_prompt", "") or DEFAULT_SPLIT_SYSTEM_PROMPT
        prompt_template = self._cfg.get("user_prompt_template", "") or DEFAULT_SPLIT_PROMPT

        user_prompt = prompt_template.format(text=text, max_length=max_length)
        messages: list[dict] = [
            {"role": "user", "content": user_prompt},
        ]
        if system_prompt:
            messages.insert(0, {"role": "system", "content": system_prompt})

        client = self._make_client()
        dynamic_max_tokens = max(4096, min(8192, len(text) * 4 + 2048))

        last_content = ""
        for attempt in range(2):
            content = client.chat_completion(
                messages=messages,
                max_completion_tokens=dynamic_max_tokens,
                temperature=0.3,
            )
            last_content = content
            sentences = self._parse_split_output(content)
            if sentences:
                return sentences
            logger.warning("LLMService.split attempt %d empty: %s", attempt + 1, content[:300])

        raise LLMServiceError(f"LLM 拆分失败，两次均返回空结果: {last_content[:500]}")

    def _chunk_text(self, text: str) -> list[str]:
        """按自然段分块，每块尽量不超过 CHUNK_SIZE。"""
        # 先按双换行（自然段）切分
        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
        if not paragraphs:
            return [text]

        chunks: list[str] = []
        current = ""
        for para in paragraphs:
            if len(current) + len(para) + 2 <= self.CHUNK_SIZE:
                current = f"{current}\n\n{para}" if current else para
            else:
                if current:
                    chunks.append(current)
                # 如果单段就超过限制，按句末标点再切
                if len(para) > self.CHUNK_SIZE:
                    sub_chunks = self._split_long_paragraph(para)
                    chunks.extend(sub_chunks)
                    current = ""
                else:
                    current = para
        if current:
            chunks.append(current)
        return chunks

    def _split_long_paragraph(self, para: str) -> list[str]:
        """超长段落按。！？切分，尽量保持在 CHUNK_SIZE 以内。"""
        parts = re.split(r'(?<=[。！？])', para)
        chunks: list[str] = []
        current = ""
        for part in parts:
            if len(current) + len(part) <= self.CHUNK_SIZE:
                current += part
            else:
                if current:
                    chunks.append(current)
                current = part
        if current:
            chunks.append(current)
        return chunks or [para]

    def _parse_split_output(self, content: str) -> list[str]:
        lines = content.splitlines()
        sentences = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            line = re.sub(r'^(\d+[\.、)])+\s*', '', line)
            line = re.sub(r'^[-*]\s+', '', line)
            if line:
                sentences.append(line)
        return sentences

    # ── 停顿建议 ──

    PAUSE_CHUNK_SIZE = 50  # 单次 LLM 最多处理句子数

    def advise_pauses(self, sentences: list[str]) -> list[float]:
        """用 LLM 为每句音频建议停顿时长。句数多时分块处理。"""
        if not sentences:
            return []

        # 短列表直接发送
        if len(sentences) <= self.PAUSE_CHUNK_SIZE:
            return self._advise_pauses_chunk(sentences)

        # 长列表分块
        all_pauses: list[float] = []
        for i in range(0, len(sentences), self.PAUSE_CHUNK_SIZE):
            chunk = sentences[i : i + self.PAUSE_CHUNK_SIZE]
            logger.info("LLMService.pauses: 处理第 %d 块 (%d 句)",
                        i // self.PAUSE_CHUNK_SIZE + 1, len(chunk))
            pauses = self._advise_pauses_chunk(chunk)
            if all_pauses:
                # 询问 LLM：上块末句与下块首句之间应该停顿多久
                boundary = self._advise_boundary_pause(
                    sentences[i - 1], sentences[i]
                )
                all_pauses[-1] = boundary
            all_pauses.extend(pauses)

        logger.info("LLMService.pauses: 共 %d 句完成", len(all_pauses))
        return all_pauses

    def _advise_boundary_pause(self, prev_sentence: str, next_sentence: str) -> float:
        """询问 LLM 两句之间的停顿时长。"""
        prompt = f"""你是配音导演。下面是两段相邻的配音句子：

前句：{prev_sentence}
后句：{next_sentence}

请仅输出前句之后应该停顿的秒数（0.0~2.0），不要输出其他内容。"""
        messages = [{"role": "user", "content": prompt}]
        client = self._make_client()
        content = client.chat_completion(
            messages=messages,
            max_completion_tokens=32,
            temperature=0.3,
        )
        content = content.strip()
        try:
            val = float(re.findall(r"[\d.]+", content)[0])
            return round(max(0.0, min(2.0, val)), 2)
        except (ValueError, IndexError):
            logger.warning("LLMService: 无法解析边界停顿，使用默认 0.3s: %s", content[:50])
            return 0.3

    def _advise_pauses_chunk(self, sentences: list[str]) -> list[float]:
        """对单块句子请求停顿建议。"""
        prompt_template = self._cfg.get("pause_prompt_template", "") or DEFAULT_PAUSE_PROMPT
        prompt = prompt_template.format(
            sentences_json=json.dumps(sentences, ensure_ascii=False, indent=2)
        )
        messages = [{"role": "user", "content": prompt}]

        client = self._make_client()
        dynamic_max_tokens = max(1024, min(8192, len(sentences) * 80 + 1024))

        content = client.chat_completion(
            messages=messages,
            max_completion_tokens=dynamic_max_tokens,
            temperature=0.3,
        )
        return self._parse_pauses(content, len(sentences))

    def _parse_pauses(self, content: str, expected_count: int) -> list[float]:
        content = content.strip()
        candidates: list[str] = []

        # 1. markdown 代码块
        cb = re.search(r"```(?:json)?\s*([\s\S]*?)```", content, re.IGNORECASE)
        if cb:
            candidates.append(cb.group(1).strip())
        # 2. 方括号数组
        br = re.search(r"\[[\s\S]*\]", content)
        if br:
            candidates.append(br.group(0).strip())
        # 3. 兜底
        candidates.append(content)

        pauses = None
        last_err = None
        for raw in candidates:
            try:
                pauses = json.loads(raw)
                if isinstance(pauses, list):
                    break
            except json.JSONDecodeError as e:
                last_err = e

        if pauses is None or not isinstance(pauses, list):
            raise LLMServiceError(f"无法解析停顿建议: {last_err}")

        if len(pauses) != expected_count:
            raise LLMServiceError(
                f"停顿数量不匹配：期望 {expected_count}，实际 {len(pauses)}"
            )

        result = []
        for i, p in enumerate(pauses):
            try:
                val = float(p)
            except (TypeError, ValueError):
                raise LLMServiceError(f"第 {i+1} 个停顿值不是数字: {p}")
            result.append(round(max(0.0, min(2.0, val)), 2))

        if result:
            result[-1] = 0.0
        return result


# ── 兼容旧接口 ──

def list_presets() -> list[str]:
    return list(LLM_PRESETS.keys())


def get_preset(name: str) -> dict:
    return LLM_PRESETS.get(name, {})
