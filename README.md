# IndexTTS Studio

开源桌面配音工具：导入文稿 → 克隆音色 → 合成语音 → 编辑字幕 → 导出 SRT/ASS。

![License](https://img.shields.io/badge/license-MIT-blue)
![Python](https://img.shields.io/badge/python-3.10%2B-green)

## 功能概览

- **📝 文稿拆分** — 三种模式：规则 / LLM（MiMo / DeepSeek）/ 自动回退。长文稿自动分块，UI 实时显示进度。
- **🎙 音色管理** — 合成面板内嵌参考音频管理，支持拖放 WAV、试听、多音色列表切换、一键上传、变速（0.5x~2.0x）。
- **⚙ 合成引擎** — 后台线程逐句调用 TTS API，支持中途取消、增量合成（仅重新合成已变更句子）、单句重新生成。
- **🔗 合并** — 合并前自动询问 LLM 句间停顿，按停顿 ffmpeg 拼接 + 字幕时间戳生成。
- **🎯 字幕校准** — 在剪辑软件中调整 full_dub.wav 间隔后，加载修改后的音频自动重新校准所有字幕时间戳。
- **📄 字幕编辑** — 时间轴（波形 + 字幕块 + 拖拽切分/合并/删除）+ 表格 + 文本编辑器，Ctrl+Z 撤销，实时字速/停顿校验、字幕原始/校准版本切换。
- **📤 导出** — 支持 SRT 和 ASS（含样式：字体/字号/颜色/描边/对齐/淡入淡出）。
- **🕒 最近工程** — 启动时自动加载上次打开的工程。

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

任选其一：

```bash
python run.py                  # 源码直接运行
index-tts-studio               # 安装后的 console script
```

首次运行会自动创建 `config.json`（API 配置）和 `projects/default/`（默认工程）。

## 工作流

| 步骤 | 操作 |
|------|------|
| ① 文稿 | 粘贴/导入文本 → 选择拆分模式 → 点击拆分 |
| ② 合成 | 在合成面板拖入参考音频 WAV → 试听 → 上传到 API → 点击开始合成 → 等待完成 |
| ③ 合并 | 自动询问 LLM 句间停顿 → 拼接 + 生成字幕 → 加载完整音频到字幕页 |
| ④ 校准 | （可选）在剪辑软件中调整 full_dub.wav 间隔 → 字幕页点"🔄 校准字幕" → 选修改后音频 → 自动更新时间戳 |
| ⑤ 导出 | 检查/编辑字幕 → 切分/合并/去标点 → 导出 SRT 或 ASS |

## 配置

点击左侧「⚙ 设置」打开配置对话框：

**TTS API**
- 服务商 — 默认 `index_tts`
- API URL — 首次启动需在「⚙ 设置」中填写，留空不会向任何默认公网地址发送数据

**LLM 智能拆分**（可选）
- 内置 MiMo / DeepSeek 预设，切换时自动换对应的 API Key
- 支持自定义 OpenAI 兼容端点
- Prompt 模板可自定义

配置保存在 `config.json`（已 gitignore，不会提交）。`config.example.json` 是模板。

## 校准字幕原理

校准基于 FFT 互相关：对每句原始 `sentence_XX_*.wav` 在用户调整后的 `full_dub.wav` 中定位，再按"句内平移、句间按比例"构建时间映射函数，刷新所有字幕条目的时间戳。低置信度的句可手动微调。指纹**不持久化**（按需从原始 WAV 重新提取），`project.json` 保持精简。

## 项目结构

```
src/index_tts_gui/
├── main.py                      # 应用入口
├── core/                        # 纯逻辑，不依赖 PySide6
│   ├── llm_service.py           # LLM 统一服务（拆分 + 停顿 + 分块）
│   ├── llm_client.py            # OpenAI SDK 封装
│   ├── tts_client.py            # TTS API 客户端（BaseTTSClient 抽象 + IndexTTSClient 实现）
│   ├── splitter.py              # 规则/LLM 拆分器
│   ├── merger.py                # ffmpeg 音频合并 + WAV 文件名解析
│   ├── subtitler.py             # 字幕生成 + SRT 输出
│   ├── subtitle.py              # 字幕数据模型（SubtitleEntry/Track/Item/Style）
│   ├── pause_advisor.py         # LLM 停顿顾问（委托给 LLMService）
│   ├── pause_rules.py           # 标点停顿规则
│   ├── speech_aligner.py        # 音频校准：FFT 互相关 + 时间映射
│   ├── project.py               # 工程持久化（project.json）
│   ├── audio_speed.py           # 音频变速（ffmpeg atempo）
│   ├── io_ass.py                # ASS 字幕导出
│   └── logger.py                # 日志配置
└── ui/                          # PySide6 界面层
    ├── main_window.py           # 主窗口 + 左侧导航 + 配置管理 + 工程切换
    ├── settings_dialog.py       # API / LLM 设置对话框
    ├── editor.py                # 文稿编辑 + 拆分预览（带键盘切分/合并）
    ├── split_worker.py          # 后台 LLM 拆分线程
    ├── voice_panel.py           # 参考音频管理（多音色列表 + 变速）
    ├── voice_upload_worker.py   # 参考音频上传后台线程
    ├── synthesis_panel.py       # 合成控制 + 增量合成 + 单句重生成
    ├── synthesis_worker.py      # 后台合成线程
    ├── merge_worker.py          # 后台合并线程（LLM 停顿 → ffmpeg → 字幕）
    ├── calibrate_worker.py      # 后台校准线程
    ├── subtitle_view.py         # 字幕编辑 + 时间轴 + 播放 + 校准入口
    ├── subtitle_regenerate_worker.py  # 后台字幕重建线程
    ├── timeline_canvas.py       # 时间轴画布（波形 + 字幕块 + 播放头 + 交互）
    ├── audio_engine.py          # 音频波形提取
    ├── audio_load_worker.py     # 后台音频波形加载
    ├── log_status_bar.py        # 底部日志状态栏（marquee）
    └── log_viewer.py            # 完整日志查看对话框
```

## 测试

```bash
QT_QPA_PLATFORM=offscreen pytest tests/ -v
```

162 个测试覆盖：核心算法、LLM 服务、字幕处理、合并、合成/校准/上传 worker、UI 面板。

## License

MIT — 可自由使用、修改、商用。
