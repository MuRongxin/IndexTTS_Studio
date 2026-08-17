# 字幕配音页面 — 设计文档

日期：2026-08-17
状态：已获用户批准

## 目标

新增一个「🎬 配音」功能页面：导入外部字幕文件（.srt / .ass），逐条用 Index TTS 合成语音，按原字幕时间轴拼接成完整配音 WAV，并输出偏移后的新字幕文件。

## 已确认的关键需求

1. **时长策略**：音频保持自然时长，**不变速**。各段尽量按原字幕时间戳放置；某段超出时间槽时，**仅顺延真正被它顶到的后续块**（影响最小原则）：溢出先由段间空白吸收，空白不足时后一块紧接前一块之后开始，一旦后续某块之前重新有空档，时间轴立即回到原时间戳。原字幕本就重叠的块在顺序音轨中按背靠背拼接。必须输出偏移后的新字幕文件。
2. **音色**：默认使用项目音色（`project.audio_name`），但配音页面也能单独选择/上传音色（页面级临时音色，不改写项目保存值）。
3. **输出**：对齐（偏移后）时间轴的完整 WAV + 偏移后的新字幕文件（SRT；输入为 ASS 时同时导出 ASS）。

## 模块划分

新增 4 个文件，改动 1 个文件：

| 文件 | 职责 |
|---|---|
| `core/io_subtitle.py` | 纯逻辑：SRT/ASS 字幕解析器 |
| `core/dub_planner.py` | 纯逻辑：偏移时间轴计算（影响最小放置） |
| `ui/subtitle_dub_worker.py` | QThread 后台配音 worker |
| `ui/subtitle_dub_panel.py` | 新页面「🎬 配音」 |
| `ui/main_window.py`（改动） | 注册新页面、注入 client、关闭时 cancel |

## core/io_subtitle.py — 解析器

- `parse_subtitle_file(path) -> list[SubtitleEntry]`：按扩展名分发到 `parse_srt` / `parse_ass`。
- SRT：解析序号 + `HH:MM:SS,mmm --> HH:MM:SS,mmm` 时间行 + 多行文本。
- ASS：只解析 `[Events]` 段的 `Dialogue:` 行，按 `Format:` 行定位 Start/End/Text 列；剥离 `{\...}` override tags，`\N` 转为换行。
- 产出复用现有 `SubtitleEntry(index, start_sec, end_sec, text)`（`core/subtitle.py`）。
- 空文本条目跳过；解析失败抛带中文信息的异常。
- 偏移后字幕导出复用现有代码：SRT 用 `core/subtitler.py:entries_to_srt`，ASS 用 `core/io_ass.py:entries_to_ass`。

## core/dub_planner.py — 偏移规划

纯函数：

```python
plan_dub_timeline(entries, durations) -> list[SubtitleEntry]
build_track_pauses(new_entries) -> list[float]
```

- `new_start[0] = start[0]`；`new_end[i] = new_start[i] + duration[i]`
- `new_start[i+1] = max(start[i+1], new_end[i])`：前一段结束时未到后一段的开始时间 → 后一段保持原时间戳（段间空白吸收溢出）；否则后一段紧接前一段之后开始（只顺延被顶到的块）；后续块之前重新有空档时自动回到原时间戳。原字幕重叠（`start[i+1] < end[i]`）无法在顺序音轨中表达，按背靠背拼接。该放置对每段都是最早可行起点，被移动的块数最少。
- `build_track_pauses` 把新时间轴转成「每段前的静音时长」，供拼接使用

## ui/subtitle_dub_worker.py — 配音 worker

照抄 `SynthesisWorker` 模式（`ui/synthesis_worker.py`）：`progress / sentence_done / finished / error / log` 信号 + `cancel()` 标志位。

流程：

1. 逐条 `client.synthesize(text, audio_name)` → 写 `projects/<name>/output_tts/dub/dub_{i:03d}.wav`
2. 全部完成后用 `merger.get_wav_duration` 取每段实际时长
3. 调 `plan_dub_timeline` 得新时间轴
4. 按 `build_track_pauses` 用静音拼接成 `dub_full.wav`（复用 `core/merger.py` 的静音生成与 concat 逻辑）
5. 导出 `dub_shifted.srt`；输入为 ASS 时同时导出 `dub_shifted.ass`
6. `finished` 信号带回输出路径列表

单条合成失败：记录日志并中止（与 `SynthesisWorker` 一致）；`cancel()` 在每条之间检查，立即响应。

## ui/subtitle_dub_panel.py — 页面

仿 `SynthesisPanel`（`ui/synthesis_panel.py`）的样式与协议，实现 `set_project / reset_for_new_project / cancel_workers / set_client`。

布局（自上而下）：

- **文件区**：字幕文件选择按钮（过滤 `*.srt *.ass`）+ 解析结果显示（条数 / 总时长）
- **音色区**：默认显示项目音色；「更换音色」按钮复用 `VoicePanel` 的上传/试听逻辑；页面级临时音色优先，未设置时回落 `project.audio_name`；不写回项目文件
- **预览表格**：只读（序号 / 原始开始 / 原始结束 / 文本 / 合成状态）
- **控制区**：开始 / 取消按钮 + 深色 monospace 日志框 + 完成后的输出路径展示

## main_window.py 改动点

- `nav_items` 加 `("🎬 配音", 3)`
- `_setup_central()` 中 `self._stack.addWidget(dub_panel)`
- `_apply_api()` 中 `dub_panel.set_client(self._client)`
- `_switch_project()` 中 `dub_panel.set_project(project)`
- `closeEvent()` 的 cancel 循环加上新 panel

## 异常处理

- 未注入 client 或无音色：开始按钮禁用并提示
- 解析失败：日志框显示中文错误，不崩溃
- 空字幕、时长为 0 的条目：跳过并在日志提示
- 输出目录 `output_tts/dub/` 自动创建

## 测试（最后统一编写，遵循 AGENTS.md）

- `tests/test_io_subtitle.py`：SRT/ASS 解析（含 override tags、`\N`、多行文本、坏格式报错）
- `tests/test_dub_planner.py`：无冲突不偏移、溢出被段间空白吸收、超长仅顺延被顶到的块、顺延被短块/空档恢复、原重叠转背靠背、首段保持原始 start
