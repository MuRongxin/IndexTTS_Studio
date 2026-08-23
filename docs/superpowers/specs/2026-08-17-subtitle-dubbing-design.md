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

---

# 追加：配音页校准功能（2026-08-23，已获用户批准）

## 目标

配音产物 `dub_full.wav` 被用户在外部调整段间间隔后，能反向校准出新的字幕时间轴——与「字幕」页的「🔄 校准字幕」同款能力。

**硬约束（用户明确要求）：所有改动只新增，不影响任何现有功能。** 不修改 `speech_aligner.py` / `calibrate_worker.py` / `subtitle_view.py` / `merger.py` 等现有文件，只调用其公开接口。

## 新增 `ui/dub_calibrate_worker.py`（仿 `CalibrateWorker`）

输入：修改后的音频路径、`dub_dir`、是否导出 ASS。流程：

1. 解析 `dub/dub_shifted.srt` → 校准基准时间轴（与 `dub_full.wav` 布局一致；以文件为基准，重启 app 后仍可校准）
2. 收集 `dub/dub_*.wav` 按序号排序，数量与条目不一致则报错
3. `get_wav_duration` 取各段实际时长
4. **片头归零**：`lead = 首条 start_sec`，基准时间轴整体平移到 0 起点；段后间隔作为 pauses 传给 `align_sentences` 作插值先验
5. `align_sentences(修改后音频, dub 分段, 文本, pauses)` → 每段在修改后音频中的绝对位置 + 置信度
6. `recalibrate_entries(归零基准, 归零 starts, durations, new_starts)` → 校准后条目（绝对时间轴；用户裁掉片头静音也能正确映射）
7. 写出 `dub/dub_calibrated.srt`（输入为 ASS 时加 `dub_calibrated.ass`），不覆盖 `dub_shifted.*`
8. `finished` 信号带回校准后条目列表

信号与取消模式照抄现有 worker（`log / progress / finished / error / cancel()`）。

## 改动 `ui/subtitle_dub_panel.py`（仅限本页面）

- 控制区加「🔄 校准字幕」按钮：`dub/dub_shifted.srt` 存在才可用，否则禁用并提示「请先完成配音」
- 点击 → 文件对话框选修改后的音频 → 启动 worker，按钮变「校准中…」
- 完成 → 预览表格的开始/结束列更新为校准后时间，表格组标题改为「字幕预览（校准后）」，日志框与输出路径区显示新文件
- `cancel_workers()` 纳入校准 worker；工程切换时按钮状态随 `dub_shifted.srt` 是否存在重新判定

## 边界处理

- 分段 wav 数量与字幕条目数不一致 → 报错中止
- 低置信段落 → 沿用现有对齐器的插值回退，日志提示哪些条是插值结果
- 校准不改动 `dub_full.wav` 与各分段文件，可反复校准

## 测试

- `tests/test_dub_calibrate_worker.py`：基准解析、片头归零映射、数量不一致报错、输出文件写出（对齐部分 mock `align_sentences`）
