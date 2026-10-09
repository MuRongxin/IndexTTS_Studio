"""
TTS API 客户端抽象层。

默认提供 IndexTTS 实现；新增服务商时继承 BaseTTSClient 并注册到 FACTORY。
"""
from __future__ import annotations

import base64
import json
import logging
import os
from abc import ABC, abstractmethod
from typing import Any

import requests

from index_tts_gui.core.paths import app_root


DEFAULT_API_URL = ""
DEFAULT_TIMEOUT = {"check": 10, "upload": 30, "synthesize": 120}


def _request(method: str, url: str, *, what: str, **kwargs) -> requests.Response:
    """统一的 requests 调用包装：把网络异常转成 RuntimeError。

    类文档约定各方法失败时统一抛 RuntimeError。不包装的话
    requests.ConnectionError / Timeout / RequestException 会原样逃逸，
    在没有顶层 try 的 worker 线程里会直接终止线程而不报错。
    """
    try:
        return requests.request(method, url, **kwargs)
    except requests.exceptions.ConnectionError as e:
        raise RuntimeError(f"无法连接到 TTS 服务（{what}）: {e}") from e
    except requests.exceptions.Timeout as e:
        raise RuntimeError(f"{what}请求超时") from e
    except requests.exceptions.RequestException as e:
        raise RuntimeError(f"{what}失败: {e}") from e


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
        resp = _request(
            "GET",
            f"{self.base_url}/v1/check/audio",
            what="检测 TTS 服务",
            timeout=self.timeout["check"],
        )
        # 只要服务有响应（即使是 400/404），说明它在线
        if resp.status_code < 500:
            return f"TTS 服务可连接（HTTP {resp.status_code}）"
        return f"TTS 服务异常（HTTP {resp.status_code}）"

    def check_audio(self, file_name: str) -> bool:
        resp = _request(
            "GET",
            f"{self.base_url}/v1/check/audio",
            what="检查音频",
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
            resp = _request(
                "POST",
                f"{self.base_url}/v1/upload_audio",
                what="上传音频",
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

        resp = _request(
            "POST",
            f"{self.base_url}/v2/synthesize",
            what="合成",
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

        # 与 IndexTTS2Client.synthesize 一致：非 WAV 响应不能落盘成 .wav
        if not resp.content.startswith(b"RIFF"):
            raise RuntimeError(
                f"合成返回的内容不是 WAV 音频: {resp.content[:16]!r}"
            )
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


def default_config_path() -> str:
    """config.json 的绝对路径（跟随 app_root，不受启动工作目录影响）。"""
    return os.path.join(app_root(), CONFIG_FILE)


def _coerce_timeout(value: Any) -> dict[str, int]:
    """把配置里的 timeout 规整成 {check,upload,synthesize} 数字字典。

    配置是用户手写的，值可能是字符串/None/嵌套错误的类型；直接透传给
    requests 会得到难以定位的报错，这里过滤回默认值。
    """
    out = dict(DEFAULT_TIMEOUT)
    if isinstance(value, dict):
        for key in out:
            raw = value.get(key)
            try:
                if raw is not None:
                    out[key] = int(float(raw))
            except (TypeError, ValueError):
                continue
    return out


def create_client_from_config(config_path: str | None = None) -> BaseTTSClient:
    """从 config.json 创建 TTS 客户端。

    供面板独立构造时兜底：MainWindow 在 _setup_central 之后才调用
    _apply_api 注入客户端，构造期面板用此函数直接按已保存配置创建，
    避免「未配置 TTS API」的误报。
    """
    if config_path is None:
        config_path = default_config_path()
    cfg: dict = {}
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except (OSError, ValueError) as e:
            logging.getLogger("index_tts").warning(
                "读取配置失败 %s: %s（将使用默认配置）", config_path, e
            )
            cfg = {}
    if not isinstance(cfg, dict):
        logging.getLogger("index_tts").warning(
            "配置内容不是对象（%s），已忽略", config_path
        )
        cfg = {}
    return create_client(
        provider=cfg.get("provider", "index_tts"),
        api_url=cfg.get("api_url", ""),
        timeout=_coerce_timeout(cfg.get("timeout")),
    )
