"""
OpenAI 兼容的 LLM 客户端。

使用官方 openai Python SDK 调用 Xiaomi MiMo、DeepSeek 等
OpenAI /v1/chat/completions 服务，确保认证、参数与官方示例一致。

代理：openai SDK 默认创建 trust_env=True 的 httpx.Client，httpx 会在构造
客户端时**立即**解析 HTTP_PROXY / HTTPS_PROXY / ALL_PROXY 等环境变量，
只要其中有 httpx 不认识的 scheme（典型如代理软件写下的
``ALL_PROXY=socks://127.0.0.1:10808/``），构造就会直接抛
``ValueError: Unknown scheme for proxy URL``，连请求都发不出去。
这里改为自己解析环境代理并显式传给 httpx：
- 支持 http/https/socks5/socks5h；
- ``socks://`` 是各代理客户端常见的 socks5 简写，自动改写为 socks5://
  （需要 socksio，未安装时给出提示并忽略该代理）；
- 支持 NO_PROXY（含 *、域名后缀、IP、CIDR），本地服务（如 Ollama）直连；
- 不支持的代理只告警、不崩溃，回退直连。
"""
from __future__ import annotations

import importlib.util
import ipaddress
from typing import Any
from urllib.parse import urlsplit
from urllib.request import getproxies

import httpx

from openai import (
    OpenAI, APIError, APIConnectionError, AuthenticationError, APITimeoutError,
)

from index_tts_gui.core.logger import get_logger


DEFAULT_TIMEOUT = 60

# httpx 的 Proxy 只接受这几种 scheme
_HTTP_PROXY_SCHEMES = frozenset({"http", "https"})
_SOCKS_PROXY_SCHEMES = frozenset({"socks5", "socks5h"})

logger = get_logger()


class LLMError(RuntimeError):
    """LLM 调用异常。"""
    pass


def _socks_supported() -> bool:
    """httpx 使用 socks 代理需要可选的 socksio 依赖。"""
    try:
        return importlib.util.find_spec("socksio") is not None
    except (ImportError, ValueError):  # pragma: no cover - 环境异常兜底
        return False


def _mask_proxy_url(raw: str) -> str:
    """隐藏代理 URL 中的账号密码后再写日志。"""
    try:
        parts = urlsplit(raw if "://" in raw else f"http://{raw}")
    except ValueError:
        return raw
    if not (parts.username or parts.password):
        return raw
    try:
        port = parts.port
    except ValueError:
        return raw
    host = parts.hostname or ""
    if port:
        host = f"{host}:{port}"
    return parts._replace(netloc=f"***:***@{host}").geturl()


def _normalize_proxy_url(raw: str) -> str | None:
    """
    把环境变量里的代理地址规范成 httpx 能接受的 URL。

    - 补全缺失的 scheme（如 ``127.0.0.1:10808`` → ``http://...``）；
    - ``socks://`` 改写为 ``socks5://``（socks5 的常见简写）；
    - scheme 不受支持、或 socks 代理缺少 socksio 时返回 None，
      由调用方告警后回退直连。
    """
    raw = raw.strip()
    if not raw:
        return None
    if "://" not in raw:
        raw = f"http://{raw}"
    scheme, _, rest = raw.partition("://")
    scheme = scheme.lower()
    if scheme == "socks":
        scheme = "socks5"
    if scheme not in _HTTP_PROXY_SCHEMES and scheme not in _SOCKS_PROXY_SCHEMES:
        return None
    if scheme in _SOCKS_PROXY_SCHEMES and not _socks_supported():
        return None
    return f"{scheme}://{rest}"


def _proxy_env() -> dict[str, str]:
    """
    读取代理设置：环境变量优先，Windows/macOS 还会读取系统代理配置。

    与 httpx 原先内部使用的 urllib.request.getproxies() 一致，
    返回键为 http/https/all/no 的小写字典。
    """
    try:
        info = getproxies()
    except Exception:  # pragma: no cover - 系统代理读取失败时按无代理处理
        return {}
    return {key: value for key, value in info.items() if isinstance(value, str) and value}


def _no_proxy_entries(env: dict[str, str]) -> list[str]:
    """从代理配置中解析 NO_PROXY 旁路列表。"""
    raw = env.get("no", "")
    return [item.strip().lower() for item in raw.split(",") if item.strip()]


def _host_bypasses_proxy(host: str, entries: list[str]) -> bool:
    """判断主机是否命中 NO_PROXY。"""
    host = host.strip().lower().strip("[]")
    if not entries or not host:
        return False
    try:
        host_ip = ipaddress.ip_address(host)
    except ValueError:
        host_ip = None
    for entry in entries:
        if entry == "*":
            return True
        if "://" in entry:  # 少数配置写成 http://example.com
            entry = entry.split("://", 1)[1].split("/", 1)[0]
        try:
            network = ipaddress.ip_network(entry, strict=False)
        except ValueError:
            network = None
        if network is not None:
            if host_ip is not None and host_ip in network:
                return True
            continue
        if entry.startswith("*"):
            entry = entry[1:]  # 支持 *.example.com / *example.com 写法
        if entry.startswith("."):
            if host == entry[1:] or host.endswith(entry):
                return True
            continue
        if host == entry or host.endswith(f".{entry}"):
            return True
    return False


def _environment_proxy(target_url: str) -> str | None:
    """
    按环境变量解析目标 URL 应使用的代理，返回规范化后的代理 URL。

    优先级与 httpx 一致：目标协议专属代理（HTTPS_PROXY / HTTP_PROXY）
    优先，ALL_PROXY 兜底；命中 NO_PROXY 时直连。
    遇到 httpx 不支持的代理只告警并跳过，绝不抛异常。
    """
    parts = urlsplit(target_url)
    host = parts.hostname or ""
    env = _proxy_env()
    if host and _host_bypasses_proxy(host, _no_proxy_entries(env)):
        logger.debug("LLM 目标主机 %s 命中 NO_PROXY，直连", host)
        return None

    scheme = (parts.scheme or "https").lower()
    keys = [scheme] if scheme in ("http", "https") else []
    keys.append("all")  # 协议专属代理优先，ALL_PROXY 兜底

    seen: set[str] = set()
    for key in keys:
        raw = env.get(key, "").strip()
        if not raw or raw in seen:
            continue
        seen.add(raw)
        proxy = _normalize_proxy_url(raw)
        if proxy is not None:
            logger.debug("LLM 请求使用环境代理 %s=%s", key, _mask_proxy_url(raw))
            return proxy
        logger.warning(
            "忽略不受支持的环境代理 %s=%s：httpx 仅支持 http/https/socks5/socks5h；"
            "使用 socks 代理需先安装 socksio（pip install \"httpx[socks]\"）",
            key, _mask_proxy_url(raw),
        )
    return None


class LLMClient:
    """调用 OpenAI 兼容 chat completions API。"""

    def __init__(
        self,
        api_url: str,
        api_key: str,
        model: str,
        timeout: int = DEFAULT_TIMEOUT,
        reasoning_effort: str = "",
    ):
        self.base_url = api_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        # 思考强度："" 表示不传参（服务端默认）；low/medium/high/max 走
        # reasoning_effort；"off" 走 MiMo 风格 thinking.type=disabled
        self.reasoning_effort = reasoning_effort.strip().lower()
        self._client: OpenAI | None = None

    def _get_client(self) -> OpenAI:
        if self._client is None:
            proxy = _environment_proxy(self.base_url)
            if proxy:
                http_client = httpx.Client(
                    proxy=proxy,
                    timeout=self.timeout,
                    follow_redirects=True,
                )
            else:
                # 显式传入 transport：Client(transport=...) 时 httpx 不会再去
                # 解析环境代理（避免 ALL_PROXY=socks://... 这类不支持的 scheme
                # 在构造阶段直接抛错），同时仍保留 SSL_CERT_FILE/SSL_CERT_DIR
                # 等 trust_env 行为。
                http_client = httpx.Client(
                    transport=httpx.HTTPTransport(trust_env=True),
                    timeout=self.timeout,
                    follow_redirects=True,
                )
            self._client = OpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
                timeout=self.timeout,
                http_client=http_client,
            )
        return self._client

    def close(self) -> None:
        """关闭底层 HTTP 连接池（可选；进程退出时也会被回收）。"""
        if self._client is not None:
            try:
                self._client.close()
            except Exception:  # pragma: no cover - 关闭失败不影响主流程
                pass
            self._client = None

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
            "messages=%d total_chars=%d temperature=%s reasoning_effort=%s",
            self.base_url, self.model, max_completion_tokens,
            len(messages), total_chars, temperature,
            self.reasoning_effort or "(默认)",
        )
        for i, m in enumerate(messages):
            role = m.get("role", "unknown")
            content_preview = m.get("content", "")[:200].replace("\n", " ")
            logger.debug("LLM message[%d] role=%s: %s", i, role, content_preview)

        if self.reasoning_effort and "reasoning_effort" not in extra:
            if self.reasoning_effort == "off":
                # MiMo 风格：思考开关用 thinking.type，不支持 effort 档位
                extra["extra_body"] = {"thinking": {"type": "disabled"}}
            else:
                extra["reasoning_effort"] = self.reasoning_effort

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
        logger.info("LLM 响应内容: %s", content)

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

    @staticmethod
    def _get_api_key(cfg: dict) -> str:
        """从配置中取出当前预设对应的 API key。"""
        preset = cfg.get("preset", "")
        return (cfg.get(f"{preset}_key", "") or cfg.get("api_key", "")).strip()

    @classmethod
    def is_configured(cls, cfg: dict) -> bool:
        """判断配置是否足以发起 LLM 调用。"""
        return bool(
            cfg.get("api_url", "").strip()
            and cls._get_api_key(cfg)
            and cfg.get("model", "").strip()
        )
