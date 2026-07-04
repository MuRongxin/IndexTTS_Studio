# 🎬 IndexTTS Studio

> **开源桌面配音工具** — 让长文稿一键变成带时间轴的配音
>
> 📝 文稿拆分 → 🎙 音色克隆 → 🔊 语音合成 → 🎯 字幕校准 → 📤 导出 SRT/ASS

<div align="center">

![License](https://img.shields.io/badge/license-MIT-blue?style=flat-square)
![Python](https://img.shields.io/badge/python-3.10%2B-green?style=flat-square)
![Tests](https://img.shields.io/badge/tests-162%20passed-brightgreen?style=flat-square)
![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey?style=flat-square)

</div>

---

## ✨ 功能一览

| 模块 | 能力 |
|---|---|
| 📝 **文稿拆分** | 三种模式：📏 规则 · 🤖 LLM（MiMo/DeepSeek）· 🔄 自动回退<br>长文稿自动分块，UI 实时显示进度 |
| 🎙 **音色管理** | 拖放 WAV 即可 · ▶️ 试听 · 多音色列表 · ⬆️ 一键上传 · 🎚️ 变速 0.5x–2.0x |
| ⚙️ **合成引擎** | 后台线程逐句合成 · 🛑 可中途取消 · ♻️ 增量合成（仅重做变更句）· 🎯 单句重生成 |
| 🔗 **智能合并** | 自动询问 LLM 句间停顿 · 按停顿 ffmpeg 拼接 · 📝 同步生成字幕时间戳 |
| 🎯 **字幕校准** | 导入剪辑软件改过间隔的音频 → 🔍 自动重新校准所有字幕时间戳 · 💾 保留原始版本可恢复 |
| 📄 **字幕编辑** | 🎨 时间轴（波形 + 字幕块 + 拖拽切分/合并）· 📋 表格 · ✏️ 文本编辑器 · ↶ Ctrl+Z 撤销 · ⚡ 实时字速/停顿校验 |
| 📤 **导出** | 📝 SRT · 🎨 ASS（含字体/字号/颜色/描边/对齐/淡入淡出） |
| 🕒 **历史记录** | 启动自动加载上次工程 · 最近工程列表 |

---

## 🖼️ 界面

```
┌─────────────┬─────────────────────────────────────────┐
│   侧边栏    │     右侧内容区（QStackedWidget）          │
│             │                                         │
│  📝 文稿    │   根据左侧选中项切换显示：                  │
│  🎙 合成    │     ┌──────────────────────────────┐   │
│  📄 字幕    │     │  文稿 / 合成 / 字幕 面板切换   │   │
│             │     └──────────────────────────────┘   │
│  ───────    │                                         │
│  ➕ 新建    │                                         │
│  📂 打开    │                                         │
│  💾 保存    │                                         │
│  ⚙️ 设置   │   （弹出对话框）                          │
└─────────────┴─────────────────────────────────────────┘
```

---

## 🚀 快速开始

### 安装

```bash
git clone https://github.com/MuRongxin/IndexTTS_Studio.git
cd IndexTTS_Studio

python3 -m venv venv
source venv/bin/activate
pip install -e ".[dev]"
```

> ⚠️ **系统依赖**：`ffmpeg`（Ubuntu `apt install ffmpeg` / macOS `brew install ffmpeg` / Windows [下载](https://ffmpeg.org/)）

### 启动

任选其一：

```bash
python run.py                  # 🚀 源码直接运行
index-tts-studio               # 📦 安装后的 console script
```

🎁 首次运行自动创建 `config.json`（API 配置）和 `projects/default/`（默认工程）。

---

## 🎬 工作流

```
  📝 文稿                        🎙 合成
  ┌─────────┐                  ┌──────────┐
  │ 粘贴/导入 │ ─── 拆分 ──→   │  选参考音频  │ ──→ TTS ──→ sentence_01.wav ...
  │ 长文分块  │                  │  试听/上传  │      ⬇
  └─────────┘                  └──────────┘      ffmpeg 合并
                                                     ⬇
                                              full_dub.wav + 字幕
                                                     ⬇
  🎯 校准（可选）                                    📤 导出
  ┌────────────────┐                            ┌──────────┐
  │ 剪辑软件改间隔  │ ───→ 校准字幕 ───→        │ SRT / ASS │
  │ 导出新音频     │   自动更新时间戳          └──────────┘
  └────────────────┘
```

| 步骤 | 操作 |
|---:|------|
| ① | 📝 **文稿** — 粘贴/导入文本 → 选拆分模式（规则/LLM/自动）→ 点击拆分 |
| ② | 🎙 **合成** — 拖入参考音频 WAV → 试听 → 上传 → 开始合成 → 等待完成 |
| ③ | 🔗 **合并** — 自动询问 LLM 句间停顿 → 拼接 + 生成字幕 → 加载完整音频 |
| ④ | 🎯 **校准** *(可选)* — 在剪辑软件调 `full_dub.wav` 间隔 → 字幕页点 "🔄 校准字幕" |
| ⑤ | 📤 **导出** — 检查/编辑字幕 → 切分/合并/去标点 → 导出 SRT 或 ASS |

---

## ⚙️ 配置

点击左侧「⚙️ 设置」：

### TTS API

| 项 | 说明 |
|---|---|
| **服务商** | 默认 `index_tts` |
| **API URL** | 首次启动需在「⚙️ 设置」中填写；留空不会向任何默认公网地址发送数据 |

### LLM 智能拆分 *(可选)*

- 🧠 内置 **MiMo** / **DeepSeek** 预设，切换时自动换对应的 API Key
- 🔌 支持自定义 OpenAI 兼容端点
- ✍️ Prompt 模板可自定义

💾 配置保存在 `config.json`（已 gitignore）。`config.example.json` 是模板。

---

## 🎯 校准字幕原理

校准基于 **FFT 互相关**：

1. 对每句原始 `sentence_XX_*.wav` 在用户调整后的 `full_dub.wav` 中定位
2. 按"句内平移、句间按比例"构建时间映射函数
3. 用映射函数刷新所有字幕条目的时间戳

| 优点 | 说明 |
|---|---|
| 🚀 **不持久化指纹** | 按需从原始 WAV 重新提取，`project.json` 保持精简 |
| 📈 **高鲁棒性** | 对人声处理（混响/EQ/压缩）比简单 abs 包络更稳定 |
| 🎯 **可干预** | 低置信度句子高亮，用户可手动微调 |

---

## 📁 项目结构

```
src/index_tts_gui/
├── main.py                      # 🎬 应用入口
├── core/                        # 🧠 纯逻辑，不依赖 PySide6
│   ├── llm_service.py           # 🤖 LLM 统一服务（拆分 + 停顿 + 分块）
│   ├── llm_client.py            #     OpenAI SDK 封装
│   ├── tts_client.py            # 🔊 TTS API 客户端（抽象 + IndexTTS 实现）
│   ├── splitter.py              # ✂️ 规则/LLM 拆分器
│   ├── merger.py                # 🔗 ffmpeg 音频合并 + WAV 文件名解析
│   ├── subtitler.py             # 📝 字幕生成 + SRT 输出
│   ├── subtitle.py              # 📋 字幕数据模型（Entry/Track/Item/Style）
│   ├── pause_advisor.py         # 💭 LLM 停顿顾问（委托给 LLMService）
│   ├── pause_rules.py           # 📏 标点停顿规则
│   ├── speech_aligner.py        # 🎯 音频校准：FFT 互相关 + 时间映射
│   ├── audio_speed.py           # 🎚️ 音频变速（ffmpeg atempo）
│   ├── project.py               # 💾 工程持久化（project.json）
│   ├── io_ass.py                # 🎨 ASS 字幕导出
│   └── logger.py                # 📋 日志配置
└── ui/                          # 🖼️ PySide6 界面层
    ├── main_window.py           # 🏠 主窗口 + 导航 + 工程切换
    ├── settings_dialog.py       # ⚙️ API / LLM 设置对话框
    ├── editor.py                # ✏️ 文稿编辑 + 拆分预览
    ├── split_worker.py          # 🔄 后台 LLM 拆分线程
    ├── voice_panel.py           # 🎙 参考音频管理（多音色 + 变速）
    ├── voice_upload_worker.py   # ⬆️ 参考音频上传线程
    ├── synthesis_panel.py       # ⚙️ 合成控制 + 增量 + 单句重生成
    ├── synthesis_worker.py      # 🔄 后台合成线程
    ├── merge_worker.py          # 🔄 后台合并线程
    ├── calibrate_worker.py      # 🔄 后台校准线程
    ├── subtitle_view.py         # 📄 字幕编辑 + 时间轴 + 校准入口
    ├── subtitle_regenerate_worker.py  # 🔄 后台字幕重建
    ├── timeline_canvas.py       # 🎨 时间轴画布（波形 + 字幕块 + 播放头）
    ├── audio_engine.py          # 🌊 音频波形提取
    ├── audio_load_worker.py     # 🔄 后台波形加载
    ├── log_status_bar.py        # 📊 底部日志状态栏（marquee）
    └── log_viewer.py            # 📋 完整日志查看对话框
```

---

## 🧪 测试

```bash
QT_QPA_PLATFORM=offscreen pytest tests/ -v
```

**162 个测试**覆盖：

| 类别 | 数量 | 文件 |
|---|---:|---|
| 🧠 核心 | 8 | `test_core.py` |
| ✂️ LLM 服务 | 19 | `test_llm_service.py` / `test_llm_split.py` |
| 🔗 合并 | 15 | `test_merger.py` |
| 🎯 校准算法 | 31 | `test_speech_aligner.py` |
| 🔄 校准 Worker | 9 | `test_calibrate_worker.py` |
| 📄 字幕 | 41 | `test_subtitle.py` / `test_subtitler.py` / `test_subtitle_panel.py` |
| 💾 工程 | 15 | `test_project.py` / `test_project_extra.py` |
| 🎨 ASS 导出 | 2 | `test_io_ass.py` |
| 🎚️ 变速 | 7 | `test_audio_speed.py` |
| 💭 停顿顾问 | - | `test_pause_advisor.py` |
| ⚙️ Worker | 16 | `test_workers.py` |
| ✏️ 编辑器 | 7 | `test_editor_table.py` |

---

## 📜 License

**MIT** — 可自由使用、修改、商用。

---

<div align="center">

**⭐ 如果这个项目对你有帮助，欢迎点 Star！**

Made with ❤️ by [MuRongxin](https://github.com/MuRongxin)

</div>
