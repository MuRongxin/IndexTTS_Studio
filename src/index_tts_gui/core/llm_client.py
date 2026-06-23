"""
OpenAI 兼容的 LLM 客户端。

使用官方 openai Python SDK 调用 Xiaomi MiMo、DeepSeek 等
OpenAI /v1/chat/completions 服务，确保认证、参数与官方示例一致。
"""
from __future__ import annotations

from typing import Any

from openai import (
    OpenAI, APIError, APIConnectionError, AuthenticationError, APITimeoutError,
)

from index_tts_gui.core.logger import get_logger


DEFAULT_TIMEOUT = 60

logger = get_logger()


class LLMError(RuntimeError):
    """LLM 调用异常。"""
    pass


class LLMClient:
    """调用 OpenAI 兼容 chat completions API。"""

    def __init__(
        self,
        api_url: str,
        api_key: str,
        model: str,
        timeout: int = DEFAULT_TIMEOUT,
    ):
        self.base_url = api_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self._client: OpenAI | None = None

    def _get_client(self) -> OpenAI:
        if self._client is None:
            self._client = OpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
                timeout=self.timeout,
            )
        return self._client

    def chat_completion(
        self,
        messages: list[dict[str, str]],
        max_completion_tokens: int = 2048,
        temperature: float = 0.3,
        **extra: Any,
    ) -> str:
        """
        调用 chat completions，返回模型生成的文本内容。

        使用与官方 SDK 一致的参数：max_completion_tokens、messages、model 等。
        """
        if not self.api_key:
            logger.error("LLM 未配置 API Key")
            raise LLMError("未配置 API Key")

        # 记录请求摘要，便于复现和定位
        total_chars = sum(len(m.get("content", "")) for m in messages)
        logger.info(
            "调用 LLM: base_url=%s model=%s max_completion_tokens=%s "
            "messages=%d total_chars=%d temperature=%s",
            self.base_url, self.model, max_completion_tokens,
            len(messages), total_chars, temperature,
        )
        for i, m in enumerate(messages):
            role = m.get("role", "unknown")
            content_preview = m.get("content", "")[:200].replace("\n", " ")
            logger.debug("LLM message[%d] role=%s: %s", i, role, content_preview)

        try:
            completion = self._get_client().chat.completions.create(
                model=self.model,
                messages=messages,
                max_completion_tokens=max_completion_tokens,
                temperature=temperature,
                stream=False,
                **extra,
            )
        except AuthenticationError as e:
            logger.exception("LLM 认证失败")
            raise LLMError(f"认证失败，请检查 API Key: {e}") from e
        except APIConnectionError as e:
            logger.exception("LLM 连接失败")
            raise LLMError(f"连接失败，请检查 API URL 和网络: {e}") from e
        except APITimeoutError as e:
            logger.exception("LLM 请求超时")
            raise LLMError(f"请求超时（{self.timeout} 秒），可在设置中调大超时时间: {e}") from e
        except APIError as e:
            logger.error("LLM API 错误: status=%s body=%s", e.status_code, e.body)
            raise LLMError(f"API 错误 [{e.status_code}]: {e.message}") from e
        except Exception as e:
            logger.exception("LLM 请求失败")
            raise LLMError(f"请求失败: {e}") from e

        # 记录响应元数据，便于排查空返回
        choice = completion.choices[0]
        finish_reason = getattr(choice, "finish_reason", None)
        content = choice.message.content
        if content is None:
            content = ""
        content = content.strip()

        logger.info(
            "LLM 响应: model=%s finish_reason=%s choices=%d content_len=%d",
            getattr(completion, "model", None),
            finish_reason,
            len(completion.choices),
            len(content),
        )
        logger.debug("LLM 响应内容: %s", content[:1000])

        return content

    def test_connection(self) -> str:
        """
        测试连接是否可用，返回成功提示或抛出 LLMError。
        """
        messages = [
            {"role": "user", "content": "Hi"},
        ]
        content = self.chat_completion(
            messages=messages, max_completion_tokens=10, temperature=0.3
        )
        return f"连接成功，模型返回: {content[:50]}"

    @classmethod
    def is_configured(cls, cfg: dict) -> bool:
        """判断配置是否足以发起 LLM 调用。"""
        return bool(
            cfg.get("api_url", "").strip()
            and cfg.get("api_key", "").strip()
            and cfg.get("model", "").strip()
        )
