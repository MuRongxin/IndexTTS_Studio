"""IndexTTS2 云端 TTS WebSocket API 客户端（v2 传输层）。

v2 传输层由服务端的共享 ASGI 中间件（``/workspace/tts_manager/tts_protocol_v2.py``）
提供，与旧版“纯文本 JSON + 二进制 PCM”协议不兼容：

- 只接受 ``/v2/ws`` 路径，旧 ``/ws`` 会在模型处理前被拒绝（HTTP 403）；
- 握手（文本帧，10 秒内完成）::

      server -> {"op":"challenge","protocol":"ztts2","nonce":"<32 hex>"}
      client -> {"op":"hello","protocol":"ztts2","client_nonce":"<32 hex>",
                 "proof":"sha256(b'ZL-TTS/2\\0' + server_nonce + client_nonce)"}
      server -> {"op":"ready","protocol":"ztts2"}

- 握手完成后所有消息都是二进制帧::

      offset 0  4 bytes  b"ZTT2"
      offset 4  1 byte   kind: 1 = JSON, 2 = PCM 音频
      offset 5  4 bytes  大端无符号 payload 长度
      offset 9  N bytes  payload
      kind=1 的 payload 是 zlib 压缩的 UTF-8 JSON；kind=2 是 22050Hz/16bit/单声道 PCM。

参考音频仍可通过 JSON 里的 ``reference_audio_b64``（首次上传）或
``reference_audio_sha1``（服务端缓存命中）传入；缓存未命中时自动用 Base64 重试。
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import secrets
import struct
import wave
import zlib

import websocket

from index_tts_gui.core.tts_client import (
    DEFAULT_API_URL,
    DEFAULT_TIMEOUT,
    BaseTTSClient,
    _request,
)


#: v2 传输层协议标识与路径（与服务端 tts_protocol_v2.py 保持一致）
PROTOCOL = "ztts2"
PROTOCOL_PATH = "/v2/ws"
MAGIC = b"ZTT2"
KIND_JSON = 1
KIND_AUDIO = 2
MAX_WIRE_BYTES = 32 * 1024 * 1024
MAX_JSON_BYTES = 48 * 1024 * 1024
HANDSHAKE_TIMEOUT_S = 10
OUTPUT_PCM_SAMPLE_RATE = 22050


class _ReferenceAudioCacheMiss(RuntimeError):
    """服务端参考音频缓存未命中：调用方应改用 Base64 重新上传。"""


def _proof(server_nonce: bytes, client_nonce: bytes) -> str:
    """v2 握手 proof：sha256(b"ZL-TTS/2\\0" + server_nonce + client_nonce)。"""
    return hashlib.sha256(b"ZL-TTS/2\x00" + server_nonce + client_nonce).hexdigest()


def _encode_json_frame(message: dict) -> bytes:
    """把请求对象编码成 v2 二进制 JSON 帧。"""
    payload = zlib.compress(
        json.dumps(message, ensure_ascii=False).encode("utf-8"), level=3
    )
    if len(payload) > MAX_WIRE_BYTES:
        raise ValueError("TTS WebSocket 请求帧超过大小限制")
    return MAGIC + bytes((KIND_JSON,)) + struct.pack("!I", len(payload)) + payload


def _decode_frame(data: bytes) -> tuple[int, bytes]:
    """拆出 v2 帧头，返回 (kind, payload)。"""
    if not isinstance(data, bytes) or len(data) < 9 or data[:4] != MAGIC:
        raise ValueError("TTS WebSocket 返回了非法帧（缺少 ZTT2 帧头）")
    kind = data[4]
    length = struct.unpack("!I", data[5:9])[0]
    if length != len(data) - 9:
        raise ValueError(
            f"TTS WebSocket 帧长度不匹配: 帧头={length} 实际={len(data) - 9}"
        )
    if length > MAX_WIRE_BYTES:
        raise ValueError("TTS WebSocket 帧超过大小限制")
    return kind, data[9:]


def _decode_json_payload(payload: bytes) -> dict:
    """解压并解析 v2 JSON 帧 payload。"""
    decompressor = zlib.decompressobj()
    decoded = decompressor.decompress(payload, MAX_JSON_BYTES + 1)
    if (
        len(decoded) > MAX_JSON_BYTES
        or not decompressor.eof
        or decompressor.unused_data
    ):
        raise ValueError("TTS WebSocket JSON 帧解压失败或超过大小限制")
    message = json.loads(decoded.decode("utf-8"))
    if not isinstance(message, dict):
        raise ValueError(f"TTS WebSocket 返回的 JSON 不是对象: {message!r}")
    return message


class IndexTTSWebSocketClient(BaseTTSClient):
    """IndexTTS2 WebSocket v2 服务客户端。

    对上层保持与其它 Provider 一致的 ``BaseTTSClient`` 接口：
    上传/检查音色只做本地映射，真正的参考音频在合成时随请求发送。
    """

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
        self._audio_paths: dict[str, str] = {}

    def _websocket_url(self) -> str:
        """把配置里的 http(s) 地址转换成 v2 传输层的 ws(s) 地址。"""
        if self.base_url.startswith("https://"):
            return "wss://" + self.base_url[8:] + PROTOCOL_PATH
        if self.base_url.startswith("http://"):
            return "ws://" + self.base_url[7:] + PROTOCOL_PATH
        raise ValueError("WebSocket TTS API URL 必须使用 http:// 或 https://")

    def health_check(self) -> str:
        resp = _request(
            "GET",
            f"{self.base_url}/health",
            what="检测 TTS 服务",
            timeout=self.timeout["check"],
        )
        if resp.status_code != 200:
            raise RuntimeError(
                f"TTS 服务异常（HTTP {resp.status_code}）: {resp.text[:200]}"
            )
        try:
            data = resp.json()
        except ValueError as e:
            raise RuntimeError(f"健康检查返回非 JSON: {resp.text[:200]}") from e
        if data.get("status") != "healthy" or not data.get("model_loaded"):
            raise RuntimeError(f"TTS 模型未就绪: {data}")
        sample_rate = data.get("output_pcm_sample_rate", OUTPUT_PCM_SAMPLE_RATE)
        return f"TTS 服务可连接（{data.get('model_type', 'IndexTTS2')}，{sample_rate} Hz）"

    def check_audio(self, file_name: str) -> bool:
        path = self._audio_paths.get(file_name)
        return bool(path and os.path.isfile(path))

    def upload_audio(self, file_path: str) -> dict:
        """记录本地参考音频并返回文件名/SHA1（v2 在合成时随请求发送）。"""
        path = os.path.abspath(file_path)
        with open(path, "rb") as f:
            audio_data = f.read()
        file_name = os.path.basename(file_path)
        self._audio_paths[file_name] = path
        return {
            "code": 200,
            "msg": "参考音频已就绪",
            "file_name": file_name,
            "sha1": hashlib.sha1(audio_data).hexdigest(),
        }

    def _audio_data(self, audio_name: str) -> bytes:
        path = self._audio_paths.get(audio_name, audio_name)
        try:
            with open(path, "rb") as f:
                return f.read()
        except OSError as e:
            raise RuntimeError(
                f"无法读取参考音频 {audio_name}，请先选择或上传本地音色: {e}"
            ) from e

    def _handshake(self, connection) -> None:
        """完成 v2 challenge/hello/ready 握手（使用文本帧）。"""
        try:
            raw = connection.recv()
        except websocket.WebSocketTimeoutException as e:
            raise RuntimeError(
                f"TTS WebSocket 握手超时（{HANDSHAKE_TIMEOUT_S} 秒未收到 challenge）"
            ) from e
        if not isinstance(raw, str):
            raise RuntimeError("TTS WebSocket 握手失败：challenge 不是文本帧")
        try:
            challenge = json.loads(raw)
        except ValueError as e:
            raise RuntimeError(
                f"TTS WebSocket challenge 不是合法 JSON: {raw[:200]}"
            ) from e
        if (
            not isinstance(challenge, dict)
            or challenge.get("op") != "challenge"
            or challenge.get("protocol") != PROTOCOL
        ):
            raise RuntimeError(f"TTS WebSocket challenge 格式异常: {raw[:200]}")

        nonce_hex = challenge.get("nonce")
        if not isinstance(nonce_hex, str) or len(nonce_hex) != 32:
            raise RuntimeError(f"TTS WebSocket challenge nonce 非法: {nonce_hex!r}")
        try:
            server_nonce = bytes.fromhex(nonce_hex)
        except ValueError as e:
            raise RuntimeError(f"TTS WebSocket challenge nonce 非法: {nonce_hex!r}") from e

        client_nonce = secrets.token_bytes(16)
        hello = {
            "op": "hello",
            "protocol": PROTOCOL,
            "client_nonce": client_nonce.hex(),
            "proof": _proof(server_nonce, client_nonce),
        }
        connection.send(json.dumps(hello, separators=(",", ":")))

        try:
            raw = connection.recv()
        except websocket.WebSocketTimeoutException as e:
            raise RuntimeError(
                f"TTS WebSocket 握手超时（{HANDSHAKE_TIMEOUT_S} 秒未收到 ready）"
            ) from e
        if not isinstance(raw, str):
            raise RuntimeError("TTS WebSocket 握手失败：ready 不是文本帧")
        try:
            ready = json.loads(raw)
        except ValueError as e:
            raise RuntimeError(f"TTS WebSocket ready 不是合法 JSON: {raw[:200]}") from e
        if (
            not isinstance(ready, dict)
            or ready.get("op") != "ready"
            or ready.get("protocol") != PROTOCOL
        ):
            raise RuntimeError(f"TTS WebSocket 握手未就绪: {raw[:200]}")

    @staticmethod
    def _pcm_to_wav(pcm_data: bytes) -> bytes:
        if not pcm_data:
            raise RuntimeError("TTS 合成完成但未返回 PCM 音频")
        if len(pcm_data) % 2:
            raise RuntimeError("TTS 返回的 16-bit PCM 数据长度异常")
        output = io.BytesIO()
        with wave.open(output, "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(OUTPUT_PCM_SAMPLE_RATE)
            wav_file.writeframes(pcm_data)
        return output.getvalue()

    def _request_audio(
        self, text: str, audio_data: bytes, sha1: str, use_base64: bool
    ) -> bytes:
        """建立 v2 连接、握手、发送封帧请求并收齐 PCM。"""
        connection = None
        try:
            connection = websocket.create_connection(
                self._websocket_url(), timeout=self.timeout["synthesize"]
            )
            # 握手阶段按协议 10 秒超时；完成后恢复合成超时用于等待音频
            connection.settimeout(HANDSHAKE_TIMEOUT_S)
            self._handshake(connection)
            connection.settimeout(self.timeout["synthesize"])

            request = {"text": text}
            if use_base64:
                request["reference_audio_b64"] = base64.b64encode(audio_data).decode(
                    "ascii"
                )
            else:
                request["reference_audio_sha1"] = sha1
            connection.send_binary(_encode_json_frame(request))

            pcm_parts: list[bytes] = []
            while True:
                data = connection.recv()
                if data is None or data == "":
                    raise RuntimeError("TTS WebSocket 在完成前断开连接")
                if isinstance(data, str):
                    raise RuntimeError(
                        f"TTS WebSocket 协议错误：握手后收到未封帧文本 {data[:200]}"
                    )
                kind, payload = _decode_frame(data)
                if kind == KIND_AUDIO:
                    if payload:
                        pcm_parts.append(payload)
                    continue
                if kind != KIND_JSON:
                    raise RuntimeError(f"TTS WebSocket 返回未知帧类型: {kind}")

                message = _decode_json_payload(payload)
                status = message.get("status")
                if status == "error":
                    detail = message.get("message", "未知服务端错误")
                    if message.get("cache_miss") and not use_base64:
                        raise _ReferenceAudioCacheMiss(detail)
                    raise RuntimeError(f"TTS 合成失败: {detail}")
                if status == "complete":
                    return self._pcm_to_wav(b"".join(pcm_parts))
                # connection_status / queue_status / progress_update 等继续等待
        except _ReferenceAudioCacheMiss:
            raise
        except RuntimeError:
            raise
        except websocket.WebSocketBadStatusException as e:
            raise RuntimeError(
                "TTS WebSocket 握手被拒绝，请确认服务端已启用 v2 传输层"
                f"（{PROTOCOL_PATH}）: {e}"
            ) from e
        except websocket.WebSocketTimeoutException as e:
            raise RuntimeError(
                f"TTS WebSocket 超时（{self.timeout['synthesize']} 秒）: {e}"
            ) from e
        except (websocket.WebSocketException, OSError, ValueError, zlib.error) as e:
            raise RuntimeError(f"TTS WebSocket 合成失败: {e}") from e
        finally:
            if connection is not None:
                try:
                    connection.close()
                except Exception:
                    pass

        raise RuntimeError("TTS WebSocket 未返回完成消息")

    def synthesize(
        self, text: str, audio_name: str, emo_text: str | None = None
    ) -> bytes:
        """合成语音。v2 服务不支持 emo_text，因此不发送该字段。"""
        audio_data = self._audio_data(audio_name)
        sha1 = hashlib.sha1(audio_data).hexdigest()
        try:
            return self._request_audio(text, audio_data, sha1, use_base64=False)
        except _ReferenceAudioCacheMiss:
            # 服务端没有该 SHA1 的缓存：用 Base64 上传参考音频后重试
            return self._request_audio(text, audio_data, sha1, use_base64=True)
