"""合并 worker — 在后台线程执行音频合并与字幕生成"""
import logging
import os
from PySide6.QtCore import QThread, Signal

from index_tts_gui.core.merger import (
    collect_sentence_wavs,
    merge_wavs_with_custom_pauses,
    validate_wav_order,
)
from index_tts_gui.core.pause_rules import compute_pauses
from index_tts_gui.core.llm_service import LLMService, LLMServiceError
from index_tts_gui.core.subtitler import generate_srt_from_sentences_with_pauses
from index_tts_gui.core.fingerprint import (
    compute_file as compute_fingerprint,
    save as save_fingerprints,
)


logger = logging.getLogger("index_tts")


class MergeWorker(QThread):
    """后台合并线程：LLM 停顿建议 → 生成静音 → ffmpeg 合并 → 生成字幕。"""

    log = Signal(str)
    # 进度为百分比语义：current ∈ [0,100]，total 恒为 100。
    # 之前的 4 大步制会在 LLM 期间卡在 75%、ffmpeg 期间停在 100%（实际仍在跑），
    # 改为各阶段映射到区间，长步骤内部有细粒度回调
    progress = Signal(int, int, str)  # current_percent, 100, message
    # 任务结果信号。不能叫 finished：那会遮蔽 QThread 内置的线程退出信号
    result_ready = Signal(list)       # 字幕条目列表
    error = Signal(str)               # 错误信息
    canceled = Signal()               # 用户取消

    def __init__(
        self,
        sentences: list[str],
        output_dir: str,
        llm_cfg: dict,
        pauses: list[float] | None = None,
    ):
        super().__init__()
        self._sentences = sentences
        self._output_dir = output_dir
        self._llm_cfg = llm_cfg or {}
        self._provided_pauses = pauses
        self._canceled = False
        self.pauses: list[float] = []

    def cancel(self):
        # 注意：ffmpeg 由 merger 内部的 subprocess.run 执行，句柄不暴露，
        # 取消只能在下述检查点生效——合并一旦开始会跑完并写出 full_dub.wav，
        # 随后的 _check_canceled 会丢弃结果（磁盘文件与字幕可能短暂不一致，
        # 属已知限制，重新合并即可恢复一致）。
        self._canceled = True

    def _check_canceled(self):
        if self._canceled:
            raise RuntimeError("合并已取消")

    def run(self):
        try:
            self._do_merge()
        except RuntimeError as e:
            if str(e) == "合并已取消":
                self.log.emit("━━━━━━━━━━ 合并已取消 ━━━━━━━━━━")
                self.canceled.emit()
                return
            logger.exception("合并完整音频失败")
            self.error.emit(str(e))
        except Exception as e:
            logger.exception("合并完整音频失败")
            self.error.emit(str(e))

    def _do_merge(self):
        output_path = os.path.join(self._output_dir, "full_dub.wav")

        self.progress.emit(5, 100, "收集音频片段")
        self.log.emit("开始合并完整音频…")
        wavs = collect_sentence_wavs(self._output_dir)
        logger.info("发现音频片段: %d 个", len(wavs))
        if not wavs:
            raise RuntimeError(f"在 {self._output_dir} 下未找到 sentence_*.wav")
        if len(wavs) != len(self._sentences):
            raise RuntimeError(
                f"音频片段数（{len(wavs)}）与句子数（{len(self._sentences)}）不一致"
            )

        self._check_canceled()
        self.progress.emit(10, 100, "校验文件顺序")
        errors = validate_wav_order(wavs, self._sentences)
        if errors:
            for err in errors:
                self.log.emit(f"  - {err}")
            raise RuntimeError("音频文件与当前句子不匹配，请重新合成")

        self._check_canceled()
        # 停顿建议阶段映射到 10%→60%：LLM 分块回调驱动进度
        self.progress.emit(10, 100, "获取停顿建议")
        self.pauses = self._resolve_pauses()

        self._check_canceled()
        # 合并阶段映射到 60%→95%：逐段生成静音的回调驱动进度
        self.progress.emit(60, 100, "合并音频并生成字幕")
        merge_wavs_with_custom_pauses(
            wavs, self.pauses, output_path,
            on_progress=lambda c, t, m: self.progress.emit(
                60 + int(35 * c / max(t, 1)), 100, m
            ),
        )
        self.log.emit(f"✓ 已生成完整音频: {output_path}")

        self._check_canceled()
        self.progress.emit(95, 100, "生成字幕")
        entries = generate_srt_from_sentences_with_pauses(
            self._sentences, wavs, self.pauses
        )
        self.log.emit(f"✓ 已生成字幕: {len(entries)} 条")

        # 落盘声学指纹：描述的正是刚被拼进 full_dub.wav 的这一版 take。
        # 之后即使某句被重新合成，指纹仍描述 full_dub.wav 里的内容 ——
        # 那才是校准要参照的对象。
        self._save_fingerprints(wavs)

        self.result_ready.emit(entries)

    def _save_fingerprints(self, wavs: list[str]) -> None:
        """为每句 take 计算并落盘声学指纹（失败不影响合并结果）。"""
        try:
            fingerprints = {}
            for i, path in enumerate(wavs, 1):
                fps = compute_fingerprint(path)
                if fps:
                    fingerprints[i] = fps
            if not fingerprints:
                logger.warning("未能生成任何声学指纹，校准将只能依赖波形匹配")
                return
            path = save_fingerprints(self._output_dir, fingerprints)
            if path:
                self.log.emit(
                    f"✓ 已保存声学指纹: {len(fingerprints)} 条"
                    f"（{os.path.basename(path)}）"
                )
        except Exception as e:
            logger.warning("保存声学指纹失败: %s", e)

    def _resolve_pauses(self) -> list[float]:
        if self._provided_pauses is not None and len(self._provided_pauses) == len(self._sentences):
            self.log.emit("📐 使用已保存的停顿建议")
            return list(self._provided_pauses)

        service = LLMService(self._llm_cfg)

        if service.is_configured():
            self.log.emit("🤖 正在询问 LLM 停顿建议…")

            def _on_pause_progress(c: int, t: int, m: str):
                self.log.emit(f"  {m}")
                # LLM 分块进度映射到 10%→60% 区间
                self.progress.emit(10 + int(50 * c / max(t, 1)), 100, m)

            try:
                pauses = service.advise_pauses(
                    self._sentences,
                    on_progress=_on_pause_progress,
                )
                self.log.emit(f"📐 LLM 停顿建议完成: {len(pauses)} 个")
                return pauses
            except LLMServiceError as e:
                logger.exception("LLM 停顿顾问失败")
                if service.punctuation_fallback:
                    self.log.emit(f"⚠ LLM 停顿建议失败，回退标点规则: {e}")
                else:
                    raise RuntimeError(
                        f"LLM 停顿建议失败: {e}。可在设置中启用「标点规则回退」作为备用方案。"
                    )
        elif not service.punctuation_fallback:
            raise RuntimeError(
                "未配置 LLM，且未启用标点规则回退。请在设置中配置 LLM 或启用「标点规则回退」。"
            )

        # 回退到标点规则
        pauses = compute_pauses(self._sentences)
        self.log.emit(f"📐 标点规则停顿: {pauses}")
        return pauses
