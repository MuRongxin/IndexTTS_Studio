# IndexTTS Studio

开源桌面配音工具：导入文稿 → 克隆音色 → 合成语音 → 编辑字幕 → 导出 SRT/ASS。

![License](https://img.shields.io/badge/license-MIT-blue)
![Python](https://img.shields.io/badge/python-3.10%2B-green)

## 功能概览

- **📝 文稿拆分** — 三种模式：规则 / LLM（MiMo / DeepSeek）/ 自动回退。长文稿自动分块，UI 实时显示进度。
- **🎙 音色管理** — 合成面板内嵌参考音频管理，支持拖放 WAV、试听、多音色列表切换、一键上传。
- **⚙ 合成引擎** — 后台线程逐句调用 TTS API，支持中途取消、增量合成（仅重新合成已变更句子）、自动合并为完整音频。
- **🤖 LLM 停顿建议** — 合并前自动询问 LLM 句间停顿，长句列表分块处理，停顿对齐字幕。
- **📄 字幕编辑** — 时间轴 + 表格 + 文本编辑器，可视化拖拽切分/合并/删除，Ctrl+Z 撤销，实时字速/停顿校验。
- **📤 导出** — 支持 SRT 和 ASS（含样式：字体/字号/颜色/描边/对齐/淡入淡出）。

## 界面

```
┌────────────┬──────────────────────────────────┐
│ 侧边栏     │  右侧内容区（QStackedWidget）       │
│            │                                  │
│ 📝 文稿    │  根据左侧选中项切换显示：            │
│ 🎙 合成    │  文稿面板 / 合成面板 / 字幕面板      │
│ 📄 字幕    │                                  │
│            │                                  │
│ 工程管理    │                                  │
│ 新建/打开   │                                  │
│ 保存       │                                  │
│ ⚙ 设置    │  （弹出对话框）                     │
└────────────┴──────────────────────────────────┘
```

## 安装

```bash
git clone https://github.com/MuRongxin/IndexTTS_Studio.git
cd IndexTTS_Studio

python3 -m venv venv
source venv/bin/activate
pip install -e ".[dev]"
```

> 需要系统安装 `ffmpeg`：Ubuntu `apt install ffmpeg`，macOS `brew install ffmpeg`，Windows [下载](https://ffmpeg.org/)。

## 启动

```bash
python run.py
```

首次运行会自动创建 `config.json`（API 配置）和 `projects/default/`（默认工程）。

## 工作流

| 步骤 | 操作 |
|------|------|
| ① 文稿 | 粘贴/导入文本 → 选择拆分模式 → 点击拆分 |
| ② 合成 | 在合成面板拖入参考音频 WAV → 试听 → 上传到 API → 点击开始合成 → 等待完成 → 点击合并完整音频 |
| ③ 字幕 | 检查/编辑字幕 → 切分/合并/去标点 → 导出 SRT 或 ASS |

## 配置

点击左侧「⚙ 设置」打开配置对话框：

**TTS API**
- 服务商 — 默认 `index_tts`
- API URL — 首次启动需在「⚙ 设置」中填写，留空不会向任何默认公网地址发送数据

**LLM 智能拆分**（可选）
- 内置 MiMo / DeepSeek 预设，切换时自动换对应的 API Key
- 支持自定义 OpenAI 兼容端点
- Prompt 模板可自定义

配置保存在 `config.json`（已 gitignore，不会提交）。

## 项目结构

```
src/index_tts_gui/
├── main.py                      # 应用入口
├── core/                        # 纯逻辑，不依赖 PySide6
│   ├── llm_service.py           # LLM 统一服务（拆分 + 停顿 + 分块）
│   ├── llm_client.py            # OpenAI SDK 封装
│   ├── tts_client.py            # IndexTTS API 客户端
│   ├── splitter.py              # 规则拆分器
│   ├── merger.py                # ffmpeg 音频合并
│   ├── subtitler.py             # 字幕生成与 SRT 输出
│   ├── subtitle.py              # 字幕数据模型（SubtitleTrack/Item/Style）
│   ├── pause_advisor.py         # LLM 停顿顾问（已委托给 LLMService）
│   ├── project.py               # 工程持久化（project.json）
│   └── io_ass.py                # ASS 字幕导出
└── ui/                          # PySide6 界面层
    ├── main_window.py           # 主窗口 + 左侧导航 + 配置管理
    ├── settings_dialog.py       # API / LLM 设置对话框
    ├── editor.py                # 文稿编辑 + 拆分预览
    ├── split_worker.py          # 后台 LLM 拆分线程
    ├── voice_panel.py           # 参考音频管理（多音色列表）
    ├── synthesis_panel.py       # 合成控制 + 增量合成
    ├── synthesis_worker.py      # 后台合成线程
    ├── merge_worker.py          # 后台合并线程（LLM 停顿 → ffmpeg → 字幕）
    ├── subtitle_view.py         # 字幕编辑 + 时间轴 + 播放
    ├── subtitle_regenerate_worker.py  # 后台字幕重建线程
    ├── timeline_canvas.py       # 时间轴画布（波形 + 字幕块 + 交互）
    ├── audio_engine.py          # 音频波形提取
    └── log_status_bar.py        # 底部日志状态栏
```

## License

MIT — 可自由使用、修改、商用。
