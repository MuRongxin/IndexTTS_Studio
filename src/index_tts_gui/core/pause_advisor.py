"""
LLM 停顿顾问：根据文本语义建议音频片段之间的停顿时长。

本模块保留兼容接口，内部已委托给 LLMService.advise_pauses。
"""
from __future__ import annotations

import logging
from typing import Any

from index_tts_gui.core.llm_client import LLMClient
from index_tts_gui.core.llm_service import LLMService, LLMServiceError


logger = logging.getLogger("index_tts")


class PauseAdvisorError(RuntimeError):
    pass


class LLMPauseAdvisor:
    """调用 LLM 为每句音频建议停顿时长（委托给 LLMService）。"""

    def __init__(
        self,
        api_url: str,
        api_key: str,
        model: str,
        timeout: int = 60,
        prompt_template: str | None = None,
    ):
        self._cfg: dict[str, Any] = {
            "api_url": api_url,
            "api_key": api_key,
            "model": model,
            "timeout": timeout,
        }
        if prompt_template is not None:
            self._cfg["pause_prompt_template"] = prompt_template

    def advise(self, sentences: list[str]) -> list[float]:
        """
        返回每句之后的建议停顿时长列表，最后一句为 0。

        如果 LLM 调用失败，抛出 PauseAdvisorError。
        """
        if not sentences:
            return []

        service = LLMService(self._cfg)
        try:
            pauses = service.advise_pauses(sentences)
        except LLMServiceError as e:
            logger.exception("LLM 停顿顾问调用失败")
            raise PauseAdvisorError(f"LLM 停顿顾问调用失败: {e}") from e
        except Exception as e:
            logger.exception("LLM 停顿顾问异常")
            raise PauseAdvisorError(f"LLM 停顿顾问异常: {e}") from e

        # 确保长度一致
        if len(pauses) != len(sentences):
            raise PauseAdvisorError(
                f"停顿数量不匹配：期望 {len(sentences)}，实际 {len(pauses)}"
            )
        return pauses


def is_configured(llm_cfg: dict[str, Any] | None) -> bool:
    """判断 LLM 是否已配置。"""
    if not llm_cfg:
        return False
    return LLMClient.is_configured(llm_cfg)
