"""合并 worker — 在后台线程执行音频合并与字幕生成"""
import logging
import os
from PySide6.QtCore import QThread, Signal

from index_tts_gui.core.merger import (
    collect_sentence_wavs,
    merge_wavs_with_custom_pauses,
    validate_wav_order,
)
from index_tts_gui.core.pause_advisor import (
    LLMPauseAdvisor,
    is_configured as llm_is_configured,
)
from index_tts_gui.core.subtitler import generate_srt_from_sentences_with_pauses


logger = logging.getLogger("index_tts")


class MergeWorker(QThread):
    """后台合并线程：LLM 停顿建议 → 生成静音 → ffmpeg 合并 → 生成字幕。"""

    log = Signal(str)
    progress = Signal(int, int, str)  # current_step, total_steps, message
    finished = Signal(list)           # 字幕条目列表
    error = Signal(str)               # 错误信息

    def __init__(
        self,
        sentences: list[str],
        output_dir: str,
        llm_cfg: dict,
    ):
        super().__init__()
        self._sentences = sentences
        self._output_dir = output_dir
        self._llm_cfg = llm_cfg or {}
        self._canceled = False
        self.pauses: list[float] = []

    def cancel(self):
        self._canceled = True

    def run(self):
        try:
            self._do_merge()
        except Exception as e:
            logger.exception("合并完整音频失败")
            self.error.emit(str(e))

    def _do_merge(self):
        output_path = os.path.join(self._output_dir, "full_dub.wav")

        self.progress.emit(1, 4, "收集音频片段")
        self.log.emit("开始合并完整音频…")
        wavs = collect_sentence_wavs(self._output_dir)
        logger.info("发现音频片段: %d 个", len(wavs))
        if not wavs:
            raise RuntimeError(
                f"在 {self._output_dir} 下未找到 sentence_*.wav"
            )
        if len(wavs) != len(self._sentences):
            raise RuntimeError(
                f"音频片段数（{len(wavs)}）与句子数（{len(self._sentences)}）不一致"
            )

        self.progress.emit(2, 4, "校验文件顺序")
        errors = validate_wav_order(wavs, self._sentences)
        if errors:
            for err in errors:
                self.log.emit(f"  - {err}")
            raise RuntimeError("音频文件与当前句子不匹配，请重新合成")

        self.progress.emit(3, 4, "获取停顿建议")
        self.pauses = self._resolve_pauses()

        self.progress.emit(4, 4, "合并音频并生成字幕")
        merge_wavs_with_custom_pauses(wavs, self.pauses, output_path)
        self.log.emit(f"✓ 已生成完整音频: {output_path}")

        entries = generate_srt_from_sentences_with_pauses(
            self._sentences, wavs, self.pauses
        )
        self.log.emit(f"✓ 已生成字幕: {len(entries)} 条")

        self.finished.emit(entries)

    def _resolve_pauses(self) -> list[float]:
        punctuation_fallback = self._llm_cfg.get("punctuation_fallback", False)

        if llm_is_configured(self._llm_cfg):
            self.log.emit("🤖 正在询问 LLM 停顿建议…")
            try:
                advisor = LLMPauseAdvisor(
                    api_url=self._llm_cfg["api_url"],
                    api_key=self._llm_cfg["api_key"],
                    model=self._llm_cfg["model"],
                    timeout=self._llm_cfg.get("timeout", 60),
                    prompt_template=self._llm_cfg.get(
                        "pause_prompt_template", ""
                    ) or None,
                )
                pauses = advisor.advise(self._sentences)
                self.log.emit(f"📐 LLM 停顿建议: {pauses}")
                return pauses
            except Exception as e:
                logger.exception("LLM 停顿顾问失败")
                if punctuation_fallback:
                    self.log.emit(f"⚠ LLM 停顿建议失败，回退标点规则: {e}")
                else:
                    raise RuntimeError(
                        f"LLM 停顿建议失败: {e}。可在设置中启用「标点规则回退」作为备用方案。"
                    )
        elif not punctuation_fallback:
            raise RuntimeError(
                "未配置 LLM，且未启用标点规则回退。请在设置中配置 LLM 或启用「标点规则回退」。"
            )

        # 回退到标点规则
        from index_tts_gui.core.merger import _compute_pauses
        pauses = _compute_pauses(self._sentences)
        self.log.emit(f"📐 标点规则停顿: {pauses}")
        return pauses
