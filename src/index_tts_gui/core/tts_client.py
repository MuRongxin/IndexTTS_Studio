"""
TTS API 客户端抽象层。

默认提供 IndexTTS 实现；新增服务商时继承 BaseTTSClient 并注册到 FACTORY。
"""
from __future__ import annotations

import base64
import os
from abc import ABC, abstractmethod
from typing import Any

import requests


DEFAULT_API_URL = "http://117.50.216.139:8300"
DEFAULT_TIMEOUT = {"check": 10, "upload": 30, "synthesize": 120}


class BaseTTSClient(ABC):
    """TTS 服务客户端抽象基类。"""

    @abstractmethod
    def health_check(self) -> str:
        """检查服务是否可达，返回状态文本。"""
        ...

    @abstractmethod
    def check_audio(self, file_name: str) -> bool:
        """检查参考音频是否已上传/可用。"""
        ...

    @abstractmethod
    def upload_audio(self, file_path: str) -> dict:
        """上传参考音频，返回服务端信息。"""
        ...

    @abstractmethod
    def synthesize(
        self, text: str, audio_name: str, emo_text: str | None = None
    ) -> bytes:
        """合成语音，返回 WAV 字节。"""
        ...


class IndexTTSClient(BaseTTSClient):
    """IndexTTS API 封装。"""

    def __init__(
        self,
        base_url: str = DEFAULT_API_URL,
        timeout: dict[str, int] | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = {**DEFAULT_TIMEOUT, **(timeout or {})}

    def health_check(self) -> str:
        """检查 IndexTTS 服务是否可达。"""
        try:
            resp = requests.get(
                f"{self.base_url}/v1/check/audio",
                timeout=self.timeout["check"],
            )
            # 只要服务有响应（即使是 400/404），说明它在线
            if resp.status_code < 500:
                return f"TTS 服务可连接（HTTP {resp.status_code}）"
            return f"TTS 服务异常（HTTP {resp.status_code}）"
        except requests.exceptions.ConnectionError as e:
            raise RuntimeError(f"无法连接到 TTS 服务: {e}") from e
        except requests.exceptions.Timeout as e:
            raise RuntimeError(f"连接 TTS 服务超时") from e
        except Exception as e:
            raise RuntimeError(f"检测失败: {e}") from e

    def check_audio(self, file_name: str) -> bool:
        resp = requests.get(
            f"{self.base_url}/v1/check/audio",
            params={"file_name": file_name},
            timeout=self.timeout["check"],
        )
        return resp.json().get("exists", False)

    def upload_audio(self, file_path: str) -> dict:
        file_name = os.path.basename(file_path)
        with open(file_path, "rb") as f:
            resp = requests.post(
                f"{self.base_url}/v1/upload_audio",
                files={"audio": (file_name, f, "audio/wav")},
                data={"full_path": file_name},
                timeout=self.timeout["upload"],
            )
        return resp.json()

    def synthesize(
        self, text: str, audio_name: str, emo_text: str | None = None
    ) -> bytes:
        payload = {
            "text": text,
            "audio_path": audio_name,
        }
        if emo_text:
            payload["emo_text"] = emo_text

        resp = requests.post(
            f"{self.base_url}/v2/synthesize",
            json=payload,
            timeout=self.timeout["synthesize"],
        )

        if resp.status_code != 200:
            raise RuntimeError(f"合成失败 [{resp.status_code}]: {resp.text[:200]}")

        content_type = resp.headers.get("content-type", "")
        if "json" in content_type:
            result = resp.json()
            if "audio" in result:
                return base64.b64decode(result["audio"])
            raise RuntimeError(f"JSON 中无音频: {result}")

        return resp.content


# 兼容旧导入：TTSClient 指向 IndexTTSClient
TTSClient = IndexTTSClient

# Provider 工厂
FACTORY: dict[str, type[BaseTTSClient]] = {
    "index_tts": IndexTTSClient,
}


def create_client(
    provider: str = "index_tts",
    api_url: str = DEFAULT_API_URL,
    timeout: dict[str, int] | None = None,
    **kwargs: Any,
) -> BaseTTSClient:
    """根据 provider 名称创建对应 TTSClient 实例。"""
    provider = provider.lower().strip()
    if provider not in FACTORY:
        raise ValueError(
            f"未知的 TTS provider: {provider}，可用: {list(FACTORY.keys())}"
        )
    return FACTORY[provider](base_url=api_url, timeout=timeout, **kwargs)


def list_providers() -> list[str]:
    """返回已注册的 provider 列表。"""
    return list(FACTORY.keys())
