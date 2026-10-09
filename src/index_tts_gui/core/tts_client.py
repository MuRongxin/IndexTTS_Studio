"""
TTS API 客户端抽象层。

默认提供 IndexTTS 实现；新增服务商时继承 BaseTTSClient 并注册到 FACTORY。
"""
from __future__ import annotations

import base64
import json
import os
from abc import ABC, abstractmethod
from typing import Any

import requests


DEFAULT_API_URL = ""
DEFAULT_TIMEOUT = {"check": 10, "upload": 30, "synthesize": 120}


class BaseTTSClient(ABC):
    """TTS 服务客户端抽象基类。

    各方法在请求失败或服务端返回异常时统一抛出 RuntimeError。
    """

    #: 是否支持上传参考音频；不支持的服务（如 index_tts2）要求音色
    #: 以服务器端路径/文件名形式直接提供
    supports_upload: bool = True

    @abstractmethod
    def health_check(self) -> str:
        """检查服务是否可达，返回状态文本。失败时抛出 RuntimeError。"""
        ...

    @abstractmethod
    def check_audio(self, file_name: str) -> bool:
        """检查参考音频是否已上传/可用。失败时抛出 RuntimeError。"""
        ...

    @abstractmethod
    def upload_audio(self, file_path: str) -> dict:
        """上传参考音频，返回服务端信息。失败时抛出 RuntimeError。"""
        ...

    @abstractmethod
    def synthesize(
        self, text: str, audio_name: str, emo_text: str | None = None
    ) -> bytes:
        """合成语音，返回 WAV 字节。失败时抛出 RuntimeError。"""
        ...


class IndexTTSClient(BaseTTSClient):
    """IndexTTS API 封装。"""

    def __init__(
        self,
        base_url: str = DEFAULT_API_URL,
        timeout: dict[str, int] | None = None,
    ):
        base_url = base_url.strip()
        if not base_url:
            raise ValueError("TTS API URL 不能为空，请在设置中配置")
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
        if resp.status_code != 200:
            raise RuntimeError(
                f"检查音频失败 [{resp.status_code}]: {resp.text[:200]}"
            )
        try:
            data = resp.json()
        except requests.exceptions.JSONDecodeError as e:
            raise RuntimeError(
                f"检查音频返回非 JSON: {resp.text[:200]}"
            ) from e
        return data.get("exists", False)

    def upload_audio(self, file_path: str) -> dict:
        file_name = os.path.basename(file_path)
        with open(file_path, "rb") as f:
            resp = requests.post(
                f"{self.base_url}/v1/upload_audio",
                files={"audio": (file_name, f, "audio/wav")},
                data={"full_path": file_name},
                timeout=self.timeout["upload"],
            )
        if resp.status_code != 200:
            raise RuntimeError(
                f"上传音频失败 [{resp.status_code}]: {resp.text[:200]}"
            )
        try:
            return resp.json()
        except requests.exceptions.JSONDecodeError as e:
            raise RuntimeError(
                f"上传音频返回非 JSON: {resp.text[:200]}"
            ) from e

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


class IndexTTS2Client(BaseTTSClient):
    """indextts2.5 api service（rainfall 版）封装。

    协议与 IndexTTS 完全不同：
    - 合成: GET /api/clone?text=…&prompt_path=…&emo_text=…&lang=ZH，直接返回 WAV
    - 无音色上传/检查接口：prompt_path 只能引用服务器端路径
      （绝对路径，或整合包 resources/prompt_audios/ 下的文件名）
    """

    supports_upload = False

    def __init__(
        self,
        base_url: str = DEFAULT_API_URL,
        timeout: dict[str, int] | None = None,
    ):
        base_url = base_url.strip()
        if not base_url:
            raise ValueError("TTS API URL 不能为空，请在设置中配置")
        self.base_url = base_url.rstrip("/")
        self.timeout = {**DEFAULT_TIMEOUT, **(timeout or {})}

    def health_check(self) -> str:
        """检查服务是否可达（根路径返回 HTML 页面）。"""
        try:
            resp = requests.get(
                f"{self.base_url}/", timeout=self.timeout["check"]
            )
            if resp.status_code < 500:
                return f"TTS 服务可连接（HTTP {resp.status_code}）"
            return f"TTS 服务异常（HTTP {resp.status_code}）"
        except requests.exceptions.ConnectionError as e:
            raise RuntimeError(f"无法连接到 TTS 服务: {e}") from e
        except requests.exceptions.Timeout as e:
            raise RuntimeError("连接 TTS 服务超时") from e
        except Exception as e:
            raise RuntimeError(f"检测失败: {e}") from e

    def check_audio(self, file_name: str) -> bool:
        """该服务无音色检查接口；乐观返回 True，合成时由服务端报错兜底。"""
        return True

    def upload_audio(self, file_path: str) -> dict:
        raise RuntimeError(
            "该 API 不支持上传参考音频。请把参考音频放到服务器整合包的 "
            "resources/prompt_audios/ 目录（或使用服务器上的绝对路径），"
            "然后直接填写文件名/路径作为音色。"
        )

    def synthesize(
        self, text: str, audio_name: str, emo_text: str | None = None
    ) -> bytes:
        params = {"text": text, "prompt_path": audio_name, "lang": "ZH"}
        if emo_text:
            params["emo_text"] = emo_text

        try:
            resp = requests.get(
                f"{self.base_url}/api/clone",
                params=params,
                timeout=self.timeout["synthesize"],
            )
        except requests.exceptions.ConnectionError as e:
            raise RuntimeError(f"无法连接到 TTS 服务: {e}") from e
        except requests.exceptions.Timeout as e:
            raise RuntimeError("合成请求超时") from e

        if resp.status_code != 200:
            raise RuntimeError(f"合成失败 [{resp.status_code}]: {resp.text[:200]}")

        content_type = resp.headers.get("content-type", "")
        if "json" in content_type:
            raise RuntimeError(f"合成返回非音频: {resp.text[:200]}")
        if not resp.content.startswith(b"RIFF"):
            raise RuntimeError("合成返回的内容不是 WAV 音频")
        return resp.content

# Provider 工厂
FACTORY: dict[str, type[BaseTTSClient]] = {
    "index_tts": IndexTTSClient,
    "index_tts2": IndexTTS2Client,
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
    if not api_url or not api_url.strip():
        raise ValueError("API URL 不能为空，请在设置中配置 TTS API 地址")
    return FACTORY[provider](base_url=api_url, timeout=timeout, **kwargs)


def list_providers() -> list[str]:
    """返回已注册的 provider 列表。"""
    return list(FACTORY.keys())


CONFIG_FILE = "config.json"


def create_client_from_config(config_path: str = CONFIG_FILE) -> BaseTTSClient:
    """从 config.json 创建 TTS 客户端。

    供面板独立构造时兜底：MainWindow 在 _setup_central 之后才调用
    _apply_api 注入客户端，构造期面板用此函数直接按已保存配置创建，
    避免「未配置 TTS API」的误报。
    """
    cfg: dict = {}
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            pass
    return create_client(
        provider=cfg.get("provider", "index_tts"),
        api_url=cfg.get("api_url", ""),
        timeout=cfg.get("timeout"),
    )
