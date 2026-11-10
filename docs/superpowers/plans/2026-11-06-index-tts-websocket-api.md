# IndexTTS WebSocket API Implementation Plan

> **注意（2026-11-10）**：本计划实现的是旧 v1 纯文本协议；服务端已升级为
> v2 传输层（`/v2/ws` + challenge/hello 握手 + 二进制封帧），旧 `/ws` 会被拒绝。
> 当前实现见 `docs/superpowers/specs/2026-11-06-index-tts-websocket-api-design.md`。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增 IndexTTS2 WebSocket Provider，使现有上传、选择和合成流程兼容 `117.50.80.8:8000`。

**Architecture:** 在独立模块中实现同步 WebSocket 客户端，通过现有 `BaseTTSClient` 工厂注册。客户端负责参考音频 SHA1 缓存回退和 PCM 到 WAV 的转换，UI 与 worker 无需改动。

**Tech Stack:** Python 3.10+、requests、websocket-client、现有 PySide6 桌面应用。

**项目约束:** 遵循 `AGENTS.md`，本计划不新增或运行测试，不执行 git commit。真实服务当前可关闭，在线验证推迟到服务可用时。

---

### Task 1: 声明依赖并实现 WebSocket 客户端

**Files:**
- Modify: `pyproject.toml:15-27`
- Create: `src/index_tts_gui/core/tts_websocket_client.py`

- [ ] **Step 1: 添加同步 WebSocket 依赖**

在 `pyproject.toml` 的 `requests>=2.28` 后加入：

```toml
    "websocket-client>=1.6",
```

- [ ] **Step 2: 创建客户端及地址、健康检查逻辑**

创建 `tts_websocket_client.py`，导入 `base64`、`hashlib`、`json`、`os`、`wave`、`websocket` 和 `requests`，复用 `tts_client._request`。实现构造器，保存剥离末尾斜杠的 `base_url`、合并后的超时和 `_audio_paths: dict[str, str]`。实现：

```python
def _websocket_url(self) -> str:
    if self.base_url.startswith("https://"):
        return "wss://" + self.base_url[8:] + "/ws"
    if self.base_url.startswith("http://"):
        return "ws://" + self.base_url[7:] + "/ws"
    raise ValueError("WebSocket TTS API URL 必须使用 http:// 或 https://")
```

`health_check()` 请求 `/health`，非 200、非 JSON、`status != "healthy"` 或 `model_loaded` 非真时抛出带服务端详情的 `RuntimeError`，成功时返回模型与采样率文本。

- [ ] **Step 3: 保留现有上传与检查接口语义**

`upload_audio(file_path)` 记录 `os.path.basename(file_path)` 到绝对路径的映射，读取文件并返回：

```python
{"file_name": file_name, "sha1": hashlib.sha1(audio_data).hexdigest()}
```

`check_audio(file_name)` 在映射存在且文件仍是有效文件时返回 `True`，否则返回 `False`。另设私有 `_audio_data(audio_name)`：优先读取映射路径，其次把 `audio_name` 当本地路径读取；找不到或不可读时抛出 `RuntimeError`。不增加任何 UI 操作。

- [ ] **Step 4: 实现 WebSocket 请求与缓存未命中重试**

实现私有 `_request_audio(text, audio_data, sha1, use_base64)`：

1. 用 `websocket.create_connection(self._websocket_url(), timeout=self.timeout["synthesize"])` 建立连接，并在 `finally` 中关闭。
2. 循环调用 `recv()`，将文本帧用 `json.loads` 解析；`connection_status`、`queue_status`、`progress_update` 等状态继续等待，`error` 抛出内部缓存未命中异常或 `RuntimeError`。
3. 首次使用 `{"text": text, "reference_audio_sha1": sha1}`；Base64 请求使用 `{"text": text, "reference_audio_b64": base64.b64encode(audio_data).decode("ascii")}`。
4. 保存二进制帧；等待后续 `status == "complete"`，确保已收到非空、长度为偶数的 PCM，然后用 `io.BytesIO` 和 `wave.open` 写入单声道、 sampwidth 2、framerate 22050 的 WAV 并返回字节。
5. 在超时、连接、非法 JSON、异常帧或未完成即断开时抛出 `RuntimeError`，不把原始 PCM 当 WAV 返回。

公开 `synthesize(text, audio_name, emo_text=None)` 读取音频、计算 SHA1，先走 SHA1 请求，仅对 `cache_miss` 用 Base64 重试。`emo_text` 不发送。

### Task 2: 注册 Provider 并切换当前配置

**Files:**
- Modify: `src/index_tts_gui/core/tts_client.py:261-265`
- Modify: `config.json:2-3`

- [ ] **Step 1: 注册工厂而不改 UI**

在 `tts_client.py` 导入 `IndexTTSWebSocketClient`，并在 `FACTORY` 添加：

```python
    "index_tts_ws": IndexTTSWebSocketClient,
```

为消除模块循环，将共用 `_request` 与 `DEFAULT_TIMEOUT` 保持原处；新模块只导入这两项，`tts_client` 在类定义之后、工厂定义之前导入新客户端。现有两个 Provider 和设置页逻辑不改。

- [ ] **Step 2: 只修改当前 TTS 配置**

将 `config.json` 的前两项设为：

```json
  "provider": "index_tts_ws",
  "api_url": "http://117.50.80.8:8000",
```

保留窗口状态、工程路径、LLM 配置和所有密钥原值。`config.example.json` 继续保留通用空地址。

- [ ] **Step 3: 安装更新后的项目依赖**

Run: `venv/bin/python -m pip install -e .`

Expected: `websocket-client` 安装成功，项目以 editable 模式重新安装，无依赖解析错误。

### Task 3: 静态与本地协议验证

**Files:**
- Verify: `src/index_tts_gui/core/tts_websocket_client.py`
- Verify: `src/index_tts_gui/core/tts_client.py`

- [ ] **Step 1: 编译修改的模块**

Run: `venv/bin/python -m py_compile src/index_tts_gui/core/tts_client.py src/index_tts_gui/core/tts_websocket_client.py`

Expected: 退出码 0，无语法错误。

- [ ] **Step 2: 用本地模拟服务执行真实客户端调用**

用临时内存 WebSocket 传输替换 `websocket.create_connection`，模拟握手、`cache_miss`、Base64 请求、PCM 二进制帧和 `complete`，不额外引入服务端依赖。直接调用工厂创建 `index_tts_ws` 客户端，先触发缓存回退，再调用第二次验证 SHA1 路径，并通过现有 `VoiceUploadWorker.run()` 验证上传返回值能启用音色。断言 WAV 头为 `RIFF`，`wave` 读取到 22050 Hz、单声道、16-bit、非空帧。临时文件在命令结束时清理，不在仓库留下测试或输出文件。

Expected: 上传映射、缓存回退、二次合成和 WAV 转换均成功；现有 `index_tts`、`index_tts2` 仍在 `list_providers()` 中。

- [ ] **Step 3: 检查改动和产生文件**

Run: `git status --short`

Expected: 仅设计/计划文档及上述实现、配置文件有预期改动；若编译产生未忽略的缓存文件，只删除本次产生的文件。

### Task 4: 远端在线验收（需要用户临时开机）

**Files:**
- Verify: 当前 `config.json`

- [ ] **Step 1: 验证远端健康检查**

Run: `venv/bin/python -c "from index_tts_gui.core.tts_client import create_client_from_config; print(create_client_from_config().health_check())"`

Expected: 输出 IndexTTS2 已加载及 22050 Hz 信息。

- [ ] **Step 2: 用现有本地参考音频做短文本合成**

通过 `create_client_from_config()` 调用 `upload_audio("作为愚人众的十一执行官.wav")`，再调用 `synthesize("你好。", "作为愚人众的十一执行官.wav")`；在内存中用 `wave` 验证 WAV，不落盘。连续合成两次以覆盖首次 Base64 上传和后续 SHA1 缓存命中。

Expected: 两次均返回非空的 22050 Hz、16-bit、单声道 WAV，无服务端错误或超时。

- [ ] **Step 3: 清理并报告**

Run: `git status --short`

Expected: 没有验证过程产生的音频、日志或临时文件。若远端关机导致本任务无法执行，明确报告仅在线验收未执行，不声称远端兼容已验证。
