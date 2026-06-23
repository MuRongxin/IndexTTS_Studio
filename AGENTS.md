# IndexTTS Studio — Agent 开发指南

本文件面向不了解本项目的 AI 编程助手，记录项目结构、构建方式、代码组织、测试策略与注意事项。所有信息均基于当前仓库中的实际文件，不做推测。

---

## 项目概述

**IndexTTS Studio** 是一个基于 IndexTTS API 的开源桌面配音工具，使用 PySide6 构建 GUI。核心流程为：

1. 在「文稿」面板粘贴/导入中文文本并拆分为句子；
2. 在「音色」面板拖入参考音频 WAV，试听并上传到 IndexTTS API；
3. 在「合成」面板按句调用 API 生成单句 WAV，支持进度显示与中断；
4. 在「字幕」面板基于音频停顿自动生成 SRT，支持手动编辑、切分与导出。

- **语言**：中文（界面、注释、文档均以中文为主）
- **许可证**：MIT
- **Python 版本要求**：≥ 3.10

---

## 仓库结构

```
.
├── pyproject.toml              # 项目配置与依赖
├── run.py                      # 开发启动脚本
├── README.md                   # 面向用户的中文说明
├── src/
│   └── index_tts_gui/          # 主包
│       ├── main.py             # GUI 应用入口
│       ├── core/               # 纯逻辑模块，不依赖 UI
│       │   ├── tts_client.py   # IndexTTS HTTP API 客户端
│       │   ├── splitter.py     # 中文文本分句
│       │   ├── merger.py       # ffmpeg 合并 WAV
│       │   └── subtitler.py    # 字幕生成与 SRT 输出
│       └── ui/                 # PySide6 界面
│           ├── main_window.py     # 主窗口：左侧导航 + 右侧堆叠面板
│           ├── settings_dialog.py # API / LLM 设置对话框
│           ├── editor.py          # 文稿编辑/拆分面板（支持 LLM 拆分）
│           ├── split_worker.py    # 后台 LLM 拆分线程
│           ├── voice_panel.py     # 参考音频管理面板
│           ├── synthesis_panel.py # 合成控制面板
│           ├── synthesis_worker.py# 后台合成线程
│           └── subtitle_view.py   # 字幕编辑/播放/导出面板
├── tests/
│   └── test_core.py            # 核心模块测试脚本
├── 文稿.txt                     # 示例输入文稿
├── output_tts/                 # 默认单句 WAV 输出目录
├── full_dub.wav                # 合并后的完整音频（生成产物）
└── full_dub.srt                # 合并后的字幕文件（生成产物）
```

---

## 技术栈

- **GUI 框架**：PySide6 ≥ 6.5
- **HTTP 请求**：requests ≥ 2.28
- **音频分析**：librosa ≥ 0.10、soundfile ≥ 0.12、numpy ≥ 1.24
- **系统依赖**：`ffmpeg`、`ffprobe`（合并音频与读取时长）
- **构建工具**：setuptools（`pyproject.toml` 配置）

---

## 构建与运行

### 安装依赖

```bash
python3 -m venv venv
source venv/bin/activate
pip install -e . -i https://pypi.tuna.tsinghua.edu.cn/simple
```

> 仓库已包含 `venv/`，可直接激活后使用。

### 启动应用

```bash
python run.py
```

### 命令行入口

安装后也会注册 console script：

```bash
index-tts-studio
```

---

## 测试

当前测试以可直接执行的脚本形式存在，**未使用 pytest/unittest 框架**。

### 运行测试

```bash
source venv/bin/activate
python tests/test_core.py
```

### 测试覆盖

- `splitter.split_sentences`：中文分句正确性
- `subtitler.SubtitleEntry / entries_to_srt`：SRT 时间格式与输出
- `merger.get_wav_duration`：读取 `output_tts/sentence_01.wav` 时长

> `test_merger_duration` 依赖仓库中已有的 `output_tts/sentence_01.wav`。若该文件缺失，此测试会失败。
> `test_tts_client` 验证 `BaseTTSClient` 抽象、`IndexTTSClient` 实例化与 `create_client` 工厂行为。
> `test_splitter_max_length`、`test_llm_splitter_parse`、`test_hybrid_splitter_fallback` 验证拆分器抽象层行为。

---

## 代码组织与模块职责

### `core/` — 纯逻辑层

| 模块 | 职责 |
|------|------|
| `tts_client.py` | TTS API 抽象层：`BaseTTSClient`、`IndexTTSClient`、`create_client`；新增 provider 需注册到 `FACTORY` |
| `llm_client.py` | OpenAI 兼容 LLM 客户端，用于 LLM 智能拆分 |
| `splitter.py` | 拆分器抽象层：`BaseSplitter`、`RuleBasedSplitter`、`LLMSplitter`、`HybridSplitter`；内置 MiMo/DeepSeek 预设 |
| `merger.py` | 使用 `ffprobe` 读取 WAV 时长，使用 `ffmpeg concat` 合并多段音频 |
| `subtitler.py` | 基于句子 WAV 时长生成字幕，对长句使用能量检测找停顿并切分 |

### `ui/` — 界面层

| 模块 | 职责 |
|------|------|
| `main_window.py` | 组织四个 Tab、API 地址栏、状态栏、配置读写 `config.json` |
| `editor.py` | 文稿编辑与拆分预览 |
| `voice_panel.py` | 拖放/选择参考音频、试听、上传 |
| `synthesis_panel.py` | 合成设置、进度条、启停按钮、日志 |
| `synthesis_worker.py` | `QThread` 后台逐句调用 API 并保存 WAV |
| `subtitle_view.py` | 字幕表格编辑、音频播放、手动切分、导出 SRT |

### 数据流

1. `ManuscriptPanel.sentences_ready` → `SynthesisPanel.set_sentences`
2. `VoicePanel.audio_uploaded` → `SynthesisPanel.set_audio_name`
3. `SynthesisPanel.synthesis_done` → `MainWindow._on_synthesis_done`
4. `MainWindow` 调用 `merger.merge_wavs` 生成 `full_dub.wav`，再调用 `subtitler.generate_srt` 生成字幕，最后加载到 `SubtitlePanel`

---

## 关键运行时行为

- **默认 API 地址**：`http://117.50.216.139:8300`（定义于 `tts_client.py` 的 `DEFAULT_API_URL`，可被 `config.json` 覆盖）
- **配置持久化**：启动目录下的 `config.json`，保存 `provider`、`api_url`、`timeout`、`window_geometry`、`window_state`
- **默认输出目录**：`output_tts/`
- **合并产物**：`full_dub.wav`
- **默认导出字幕**：`full_dub.srt`
- **界面布局**：左侧为垂直导航栏（文稿 / 音色 / 合成 / 字幕 / 设置），右侧为 `QStackedWidget` 切换对应面板
- **API 设置**：点击左侧「设置」按钮弹出 `SettingsDialog`，配置 TTS 服务商、API URL、超时，以及 LLM 拆分参数
- **拆分模式**：文稿面板支持「规则 / LLM / 自动」三种拆分模式；LLM 拆分使用 `SplitWorker`（`QThread`）执行，避免阻塞 UI
- **后台线程**：合成在 `SynthesisWorker`（`QThread`）中执行，主界面通过 Qt Signal 更新进度与日志
- **取消机制**：`SynthesisWorker.cancel()` 设置 `_canceled` 标志，下一句开始时退出循环；取消后不触发音频合并

---

## 代码风格与开发约定

- **注释与文档字符串使用中文**。
- 核心逻辑与 UI 分离：`core/` 不引入 `PySide6`。
- 使用类型注解（`list[str]`、`str | None` 等），要求 Python ≥ 3.10。
- 文件名与模块名使用英文小写加下划线。
- UI 中硬编码了较多样式表（QSS），修改界面时注意同步检查各面板的 `setStyleSheet`。
- 字符串拼接与日志输出中混用 emoji，保持与现有界面一致即可。

---

## 安全注意事项

- **明文 HTTP API**：默认 API 地址使用 HTTP，且 `TTSClient` 未实现鉴权，数据在公网传输。
- **本地文件读取**：参考音频路径、输出目录、文稿文件均来自用户选择或硬编码路径，未做路径遍历校验。
- **子进程调用**：`merger.merge_wavs` 使用临时文件列表调用 `ffmpeg -f concat -safe 0`；`subtitler._get_duration` / `merger.get_wav_duration` 调用 `ffprobe`。确保输入路径可信，避免文件名注入。
- **持久化配置**：`config.json` 以明文 JSON 保存在当前工作目录，无敏感信息加密。
- **网络超时**：默认 `check_audio` 10 秒、`upload_audio` 30 秒、`synthesize` 120 秒；均可在 `config.json` / 顶部 API 栏调整。
- **取消合成**：用户点击停止后，`SynthesisPanel` 标记取消状态，`finished` 信号触发后不再合并音频或生成字幕。

---

## 部署与打包

当前项目未配置 PyInstaller、cx_Freeze 或其他打包工具。如需分发：

1. 确保目标系统已安装 `ffmpeg` / `ffprobe`。
2. 在目标环境执行 `pip install -e .`。
3. 运行 `python run.py` 或入口命令 `index-tts-studio`。

生成产物（`output_tts/`、`full_dub.wav`、`full_dub.srt`、`config.json`）均已被 `.gitignore` 排除，不会进入版本控制。
