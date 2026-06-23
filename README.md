# IndexTTS Studio

开源桌面配音工具：导入文稿 → 克隆音色 → 合成语音 → 编辑字幕 → 导出 SRT。

![License](https://img.shields.io/badge/license-MIT-blue)
![Python](https://img.shields.io/badge/python-3.10%2B-green)
![Platform](https://img.shields.io/badge/platform-Linux%20%7C%20macOS%20%7C%20Windows-lightgrey)

## 界面

```
┌──────────────────────────────────────────────┐
│  IndexTTS    │  📝 文稿                        │
│  Studio      │                                 │
│              │  右侧显示当前选中面板内容        │
│  📝 文稿      │                                 │
│  🎤 音色      │                                 │
│  ⚙ 合成      │                                 │
│  📄 字幕      │                                 │
│              │                                 │
│  ⚙ 设置      │                                 │
└──────────────────────────────────────────────┘
```

左侧垂直导航栏切换四个功能面板；「设置」按钮在左下角，用于配置 API。

## 功能

### 📝 文稿
- 粘贴 / 打开 `.txt` / `.md`
- 一键拆分预览，支持三种模式：
  - **规则**：按句末标点 + 最大句长智能切分
  - **LLM**：调用 Xiaomi MiMo / DeepSeek 等大模型按语义拆分
  - **自动**：优先 LLM，失败自动回退规则
- 支持含中英混排、引号嵌套的复杂文本

### 🎤 音色
- 拖放参考音频 `.wav`（3~15 秒最佳）
- 内置播放器试听
- 一键上传到 IndexTTS API

### ⚙ 合成
- 后台线程逐句调用 API，UI 不冻结
- 进度条 + 深色实时日志
- 随时停止
- 合成完自动合并为 `full_dub.wav` 并跳转到字幕面板

### 📄 字幕
- 表格视图：序号 / 开始 / 结束 / 文本
- 双击单元格直接编辑时间或文字
- **✂ 手动切分**：选中行 → 点按钮 → 从中间标点一分为二
- **🔄 重新生成**：从分句 WAV 重新分析停顿
- **📤 导出 SRT**：标准格式，可直接导入剪辑软件
- 底部播放条，播放时高亮当前字幕行

## 安装

```bash
# 克隆项目
git clone <repo-url> && cd index-tts-studio

# 创建环境
python3 -m venv venv
source venv/bin/activate     # Windows: venv\Scripts\activate

# 安装
pip install -e . -i https://pypi.tuna.tsinghua.edu.cn/simple
```

> **依赖**：系统需安装 `ffmpeg`（已在 Ubuntu/Debian 中预装；macOS: `brew install ffmpeg`；Windows: [下载](https://ffmpeg.org/)）
>
> **安全提示**：默认 IndexTTS API 地址使用明文 HTTP。如部署到公网，请改用 HTTPS 或在内网使用，并在 `config.json` 中配置自有服务端点。

## 启动

```bash
python run.py
```

## 典型工作流

| 步骤 | 操作 |
|------|------|
| ① | 文稿面板：粘贴稿件 → 点「拆分预览」 |
| ② | 音色面板：拖入参考 WAV → 「上传到 API」 |
| ③ | 合成面板：「开始合成」→ 等进度条走完 |
| ④ | 字幕面板：检查 / 手动切分 / 编辑 → 「导出 SRT」 |

## 项目结构

```
index-tts-studio/
├── run.py                 # 启动入口
├── pyproject.toml         # 包元数据
├── LICENSE (MIT)
├── README.md
├── src/index_tts_gui/
│   ├── main.py            # 应用入口
│   ├── core/              # 纯逻辑（无 UI 依赖）
│   │   ├── tts_client.py  # IndexTTS API 封装
│   │   ├── splitter.py    # 中文分句
│   │   ├── merger.py      # ffmpeg 音频合并
│   │   └── subtitler.py   # 能量检测停顿 + SRT 生成
│   └── ui/                # PySide6 界面
│       ├── main_window.py     # 主窗口 + 左侧导航
│       ├── settings_dialog.py # API / LLM 设置对话框
│       ├── editor.py          # 文稿编辑（支持 LLM 拆分）
│       ├── split_worker.py    # 后台 LLM 拆分线程
│       ├── voice_panel.py     # 音色管理
│       ├── synthesis_panel.py # 合成控制
│       ├── synthesis_worker.py# 后台合成线程
│       └── subtitle_view.py   # 字幕编辑 + 播放
└── venv/                  # Python 虚拟环境
```

## API 配置

默认使用 IndexTTS 服务，地址 `http://117.50.216.139:8300`。点击左侧「设置」按钮打开设置对话框：
- **服务商**：选择 TTS provider（默认 `index_tts`）
- **API URL**：服务端点地址
- **超时**：检查 / 上传 / 合成各自的超时秒数

配置保存在启动目录的 `config.json` 中。

IndexTTS 端点参考：
- `POST /v1/upload_audio` — 上传参考音频
- `GET /v1/check/audio?file_name=xxx` — 检查音频
- `POST /v2/synthesize` — 合成语音

如需接入其它 TTS API，可继承 `src/index_tts_gui/core/tts_client.py` 中的 `BaseTTSClient` 并实现三个方法，然后在 `FACTORY` 中注册新的 provider。

## LLM 智能拆分（可选）

在「设置」→「LLM 智能拆分」中启用后，可在「文稿」面板选择 LLM 或自动拆分模式。

内置预设：
- **MiMo Flash**: `https://api.xiaomimimo.com/v1`, `mimo-v2.5-flash`
- **DeepSeek Flash**: `https://api.deepseek.com`, `deepseek-v4-flash`

也支持自定义任意 OpenAI 兼容端点。LLM 拆分会把文稿发送到对应服务商，请注意隐私和费用；无配置或调用失败时自动回退规则拆分。

## License

MIT — 可自由使用、修改、商用。
