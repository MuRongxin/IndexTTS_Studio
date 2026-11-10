# IndexTTS WebSocket API 兼容设计

> 2026-11-10 更新：服务端已升级为 WebSocket 传输层 v2（`/v2/ws`，challenge/hello 握手 +
> 二进制封帧），旧版 `/ws` 纯文本协议在服务端被直接拒绝。客户端已按 v2 重写。

## 目标

让项目兼容 `117.50.80.8:8000` 上运行的 IndexTTS2 WebSocket 服务，同时保留现有音色选择、上传、合成、播放和保存流程。现有 `index_tts` 与 `index_tts2` Provider 的行为不变。

## 远端协议（v2 传输层）

服务端在 `api_websocket.py` 之上套了一层共享 ASGI 中间件
（`/workspace/tts_manager/tts_protocol_v2.py`）：

- 健康检查：`GET /health`，返回 `status`、`model_loaded`、PCM 采样率等信息（HTTP，不受 v2 影响）。
- 合成连接：仅接受 `ws://<host>:<port>/v2/ws`；旧 `/ws` 等其它 WebSocket 路径在模型处理前被拒绝（403），没有兼容模式。
- 握手（文本帧，10 秒内完成）：
  1. 服务端发送 `{"op":"challenge","protocol":"ztts2","nonce":"<32 hex>"}`；
  2. 客户端生成 16 字节随机 `client_nonce`，发送
     `{"op":"hello","protocol":"ztts2","client_nonce":"<32 hex>","proof":"<64 hex>"}`，
     其中 `proof = sha256(b"ZL-TTS/2\x00" + server_nonce_bytes + client_nonce_bytes)`；
  3. 服务端发送 `{"op":"ready","protocol":"ztts2"}`。
- 握手完成后双向都使用二进制帧：

  | 偏移 | 长度 | 内容 |
  | --- | --- | --- |
  | 0 | 4 | ASCII `ZTT2` |
  | 4 | 1 | kind：`1` JSON，`2` PCM 音频 |
  | 5 | 4 | 大端无符号 payload 长度 |
  | 9 | N | payload |

  kind=1 的 payload 是 zlib 压缩的 UTF-8 JSON；kind=2 是 22050 Hz、16-bit、单声道 PCM。
  单帧上限 32 MiB，解压后 JSON 上限 48 MiB；非法帧以 WebSocket code 1008 断开。
- 参考音频仍通过请求 JSON 的 `reference_audio_b64`（首次上传）或
  `reference_audio_sha1`（服务端缓存命中）传入；缓存未命中时服务端返回带
  `cache_miss: true` 的 error。
- 服务不支持项目的 `emo_text` 参数。

## 方案

新增 `index_tts_ws` Provider 和对应 `IndexTTSWebSocketClient`，实现现有 `BaseTTSClient` 接口。使用同步 `websocket-client` 库适配当前后台 worker 的同步调用方式，避免引入 Qt WebSocket 事件循环和跨线程等待。v2 的封帧、解帧与握手全部收敛在客户端内部，UI、worker 与落盘逻辑无需感知协议细节。

在 `pyproject.toml` 中显式加入 `websocket-client` 依赖。设置页继续使用现有 Provider 下拉框和 API URL 输入框，不增加新的操作步骤或改变现有面板交互。

## 客户端行为

### 连接地址

配置仍保存 HTTP 地址 `http://117.50.80.8:8000`。客户端仅将 scheme 转换为 `ws` 或 `wss`，保留主机、端口及可能存在的基础路径，并追加 `/v2/ws`。健康检查继续使用原 HTTP 地址。

### 健康检查

调用 `/health`，要求 HTTP 200、返回 JSON 且 `model_loaded` 为真。服务不可达、返回异常状态或模型未加载时给出明确错误。

### 上传与音色映射

保留现有“上传到 API”操作。`upload_audio()` 读取本地文件、计算原始字节的 SHA1，并在该客户端实例内记录文件名到本地路径与 SHA1 的映射；返回包含文件名和 SHA1 的字典。上传阶段不建立 WebSocket，也不执行合成。

`check_audio()` 根据该映射判断当前客户端是否已加载此音色。合成时也可直接接受本地音频路径，以兼容现有音色选择逻辑。

### 合成

1. 根据 `audio_name` 找到已选择的本地参考音频并计算 SHA1。
2. 建立 WebSocket 连接，按 v2 握手流程完成 challenge/hello/ready（握手阶段 10 秒超时）。
3. 首次请求以 kind=1 JSON 帧发送 `{"text": ..., "reference_audio_sha1": ...}` 复用服务端缓存。
4. 若服务端返回缓存未命中，自动用 `reference_audio_b64` 在新连接上重试，不要求用户重新操作。
5. 循环接收二进制帧：kind=2 累积 PCM，kind=1 解压解析；忽略连接、排队和进度状态，`error` 失败，`complete` 后校验 PCM 非空且为偶数长度。
6. 将 PCM 封装为标准 WAV 字节返回，使后续播放、测速、对齐和落盘逻辑无需修改。

每次合成使用独立连接，连接和接收操作受现有 `synthesize` 超时配置约束，并确保连接最终关闭。该服务不支持情绪文本，因此不向服务端发送 `emo_text`；其余调用方式不变。

## 错误处理

将连接失败、握手被拒绝/超时、非法帧、解压失败、服务端错误、缓存重试失败、缺少 PCM、缺少完成消息及 PCM 长度异常统一转换为带上下文的 `RuntimeError`，沿用项目现有 worker 错误展示路径。不得把错误 JSON 或原始 PCM 直接保存为 WAV。

## 配置

将当前配置的 Provider 设为 `index_tts_ws`，API URL 设为 `http://117.50.80.8:8000`。示例配置保持空白 API URL 的通用默认，不硬编码个人服务地址。

## 验证

遵循项目 `AGENTS.md`：不新增测试，也不运行现有测试套件。实现后针对真实服务执行短文本合成验证，确认：

- `/health` 检查成功；
- v2 握手（challenge/hello/ready）与连接状态帧正常；
- 首次 Base64 上传后可收到 PCM 帧和 `complete`，返回数据是 22050 Hz、16-bit、单声道、非空 WAV；
- SHA1 缓存命中路径可再次完成合成；
- 通过 `SynthesisWorker` 的批量合成路径能生成多个有效 WAV 并写入 wav_map；
- 原有 Provider 注册和设置页加载方式不变。
