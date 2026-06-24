"""
LLM 停顿顾问：根据文本语义建议音频片段之间的停顿时长。
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from index_tts_gui.core.llm_client import LLMClient, LLMError


logger = logging.getLogger("index_tts")


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


class PauseAdvisorError(RuntimeError):
    pass


class LLMPauseAdvisor:
    """调用 LLM 为每句音频建议停顿时长。"""

    def __init__(
        self,
        api_url: str,
        api_key: str,
        model: str,
        timeout: int = 60,
        prompt_template: str | None = None,
    ):
        self._client = LLMClient(
            api_url=api_url,
            api_key=api_key,
            model=model,
            timeout=timeout,
        )
        self._prompt_template = prompt_template or DEFAULT_PAUSE_PROMPT

    def advise(self, sentences: list[str]) -> list[float]:
        """
        返回每句之后的建议停顿时长列表，最后一句为 0。

        如果 LLM 调用失败，抛出 PauseAdvisorError。
        """
        if not sentences:
            return []

        prompt = self._prompt_template.format(
            sentences_json=json.dumps(sentences, ensure_ascii=False, indent=2)
        )
        messages = [
            {"role": "user", "content": prompt},
        ]

        logger.info(
            "LLM 停顿顾问: sentences=%d prompt_len=%d",
            len(sentences), len(prompt),
        )

        # 句子多时，JSON 输出更长，需要更多 token；同时给思考模型留出推理空间
        dynamic_max_tokens = max(1024, min(8192, len(sentences) * 80 + 1024))

        try:
            content = self._client.chat_completion(
                messages=messages,
                max_completion_tokens=dynamic_max_tokens,
                temperature=0.3,
            )
        except LLMError as e:
            logger.exception("LLM 停顿顾问调用失败")
            raise PauseAdvisorError(f"LLM 停顿顾问调用失败: {e}") from e

        logger.info("LLM 停顿顾问响应: content_len=%d", len(content))
        logger.debug("LLM 停顿顾问原始响应:\n%s", content)

        return self._parse(content, len(sentences))

    def _parse(self, content: str, expected_count: int) -> list[float]:
        """解析 LLM 返回的 JSON 数组。"""
        content = content.strip()

        # 优先尝试提取 ```json ... ``` 或 [...] 中的 JSON 数组
        candidates = []

        # 1. markdown 代码块
        code_block = re.search(
            r"```(?:json)?\s*([\s\S]*?)```", content, re.IGNORECASE
        )
        if code_block:
            candidates.append(code_block.group(1).strip())

        # 2. 最外层方括号数组
        bracket = re.search(r"\[[\s\S]*\]", content)
        if bracket:
            candidates.append(bracket.group(0).strip())

        # 3. 整个内容（兜底）
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
                continue

        if pauses is None or not isinstance(pauses, list):
            logger.warning(
                "LLM 停顿顾问返回无法解析为数组: %s", content[:300]
            )
            raise PauseAdvisorError(
                f"无法解析停顿建议: {last_err}"
            ) from last_err

        if len(pauses) != expected_count:
            logger.warning(
                "LLM 停顿顾问数量不匹配: 期望=%d 实际=%d 原始=%s",
                expected_count, len(pauses), content[:300]
            )
            raise PauseAdvisorError(
                f"停顿数量不匹配：期望 {expected_count}，实际 {len(pauses)}"
            )

        # 归一化并校验范围
        result = []
        for i, p in enumerate(pauses):
            try:
                val = float(p)
            except (TypeError, ValueError):
                raise PauseAdvisorError(f"第 {i+1} 个停顿值不是数字: {p}")
            if val < 0:
                val = 0.0
            if val > 2.0:
                val = 2.0
            result.append(round(val, 2))

        # 最后一句强制为 0
        if result:
            result[-1] = 0.0
        return result


def is_configured(llm_cfg: dict[str, Any] | None) -> bool:
    """判断 LLM 是否已配置。"""
    if not llm_cfg:
        return False
    return LLMClient.is_configured(llm_cfg)
