"""合成 worker — 在后台线程调用 TTS API"""
import glob
import logging
import os
import time
from datetime import datetime
from PySide6.QtCore import QThread, Signal

from index_tts_gui.core.merger import sanitize_for_filename
from index_tts_gui.core.tts_client import TTSClient


logger = logging.getLogger("index_tts")

#: 合成失败后每次重试前的退避时长（秒），长度即最大重试次数。
#: 瞬时故障（网络抖动、服务短暂过载）由此吸收，避免单次失败即终判。
RETRY_DELAYS = (1.0, 2.0)

#: 退避轮询粒度（秒）。用 requestInterruption 打断退避，避免取消后
#: 还要干等完整个RETRY_DELAYS 才有反应。
_BACKOFF_SLICE = 0.1


def _interruptible_sleep(thread: QThread, seconds: float) -> bool:
    """可被取消打断的 sleep。返回 True 表示等待期间收到取消请求。"""
    waited = 0.0
    while waited < seconds:
        if thread.isInterruptionRequested():
            return True
        time.sleep(min(_BACKOFF_SLICE, seconds - waited))
        waited += _BACKOFF_SLICE
    return bool(thread.isInterruptionRequested())


class SynthesisWorker(QThread):
    """后台合成线程"""

    progress = Signal(int, int, str)     # current, total, sentence_text
    sentence_done = Signal(int, str)     # index, wav_path
    # 任务结果信号。不能叫 finished：那会遮蔽 QThread 内置的线程退出信号
    result_ready = Signal(list)          # wav_map: list[dict]，含 status=failed 的条目
    error = Signal(str)                  # 错误信息
    log = Signal(str)                    # 日志

    def __init__(
        self,
        sentences: list[str],
        audio_name: str,
        output_dir: str,
        client: TTSClient,
        indices: list[int] | None = None,
    ):
        super().__init__()
        self._sentences = sentences
        self._audio_name = audio_name
        self._output_dir = output_dir
        self._client = client
        self._indices = indices if indices is not None else list(range(len(sentences)))
        self._wav_map: list[dict] = []

        os.makedirs(self._output_dir, exist_ok=True)

    def cancel(self):
        """请求取消。用Qt 的 interruption 机制，这样退避等待也能被打断。"""
        self.requestInterruption()

    @property
    def _canceled(self) -> bool:
        """是否已请求取消（保留旧属性名，兼容既有调用与测试）。"""
        return self.isInterruptionRequested()

    def _remove_stale_takes(self, one_based: int, keep_path: str = ""):
        """删除指定序号的句子 WAV。

        keep_path 例外保留（合成前只删旧文件名残留，保留目标文件）；
        失败时不传 keep_path，旧 take 一并删除，避免旧波形被下游静默使用。
        """
        pattern = os.path.join(self._output_dir, f"sentence_{one_based:02d}_*.wav")
        for path in glob.glob(pattern):
            if path == keep_path:
                continue
            try:
                os.remove(path)
            except OSError:
                pass

    def run(self):
        """线程入口。

        顶层 try 是必需的：异常一旦逃出run()，result_ready 就不会 emit，
        面板那边"开始合成"会永久禁用、"停止"永久可用，且不显示任何错误。
        这里保证任何异常都变成 error + result_ready，UI 一定能恢复。
        """
        try:
            self._run_impl()
        except Exception as e:
            logger.exception("合成线程异常终止")
            self.error.emit(f"合成线程异常终止: {e}")
            self.log.emit(f"✗ 合成线程异常终止: {e}")
            self.result_ready.emit(self._wav_map)

    def _run_impl(self):
        total = len(self._indices)
        # 合成批次标识：写入 wav_map 条目，用于区分不同代际的 take
        batch = datetime.now().isoformat(timespec="seconds")
        logger.info(
            "开始合成: total=%d audio_name=%s output_dir=%s batch=%s",
            total, self._audio_name, self._output_dir, batch,
        )
        self.log.emit(f"开始合成 {total} 句…")

        for loop_i, sentence_index in enumerate(self._indices, 1):
            if self._canceled:
                self.log.emit("已取消")
                logger.info("合成已取消，已完成 %d/%d", loop_i - 1, total)
                break

            sentence = self._sentences[sentence_index]
            # 1-based 用于文件名与显示
            i = sentence_index + 1
            self.progress.emit(loop_i, total, sentence)
            self.log.emit(f"[{loop_i}/{total}] {sentence[:40]}...")
            logger.info("合成第 %d/%d 句: %s", loop_i, total, sentence[:80])

            text_part = sanitize_for_filename(sentence)
            wav_path = os.path.join(
                self._output_dir, f"sentence_{i:02d}_{text_part}.wav"
            )
            # 写新 WAV 前删除同序号的旧文件名残留（文本改动后遗留），
            # 避免合并时数量/校验不一致
            self._remove_stale_takes(i, keep_path=wav_path)

            audio_bytes = None
            last_error = ""
            for attempt in range(len(RETRY_DELAYS) + 1):
                if self._canceled:
                    break
                try:
                    audio_bytes = self._client.synthesize(
                        sentence, self._audio_name
                    )
                    break
                except Exception as e:
                    last_error = str(e)
                    if attempt < len(RETRY_DELAYS):
                        delay = RETRY_DELAYS[attempt]
                        logger.warning(
                            "第 %d 句合成失败（第 %d 次），%.0fs 后重试: %s",
                            i, attempt + 1, delay, e,
                        )
                        self.log.emit(
                            f"  ⚠ 第 {i} 句第 {attempt + 1} 次失败，{delay:.0f}s 后重试: {e}"
                        )
                        # 退避期间也允许取消，否则取消要等最多 3s 才生效
                        if _interruptible_sleep(self, delay):
                            logger.info("退避等待中收到取消请求: 第 %d 句", i)
                            break
                    else:
                        logger.exception("合成第 %d 句失败（已重试 %d 次）", i, len(RETRY_DELAYS))

            if self._canceled:
                self.log.emit("已取消")
                logger.info("合成已取消，已完成 %d/%d", loop_i - 1, total)
                break

            if audio_bytes is not None:
                try:
                    with open(wav_path, "wb") as f:
                        f.write(audio_bytes)
                except Exception as e:
                    logger.exception("写入 WAV 失败: %s", wav_path)
                    last_error = str(e)
                    audio_bytes = None

            if audio_bytes is not None:
                logger.info(
                    "合成成功: %s size=%d bytes",
                    os.path.basename(wav_path), len(audio_bytes)
                )
                self.sentence_done.emit(i, wav_path)
                self._wav_map.append({
                    "index": sentence_index,  # 0-based
                    "text": sentence,
                    "wav": os.path.basename(wav_path),
                    "status": "ok",
                    "batch": batch,
                })
                self.log.emit(f"  ✓ {os.path.basename(wav_path)} ({len(audio_bytes)} bytes)")

            else:
                self.log.emit(f"  ✗ 第 {i} 句失败: {last_error}")
                self.error.emit(f"第 {i} 句合成失败: {last_error}")
                # 显式记录失败（保存时覆盖该句旧条目，不再被计为成功），
                # 并清理该句旧 take——旧波形不得静默流入合并/校准
                self._wav_map.append({
                    "index": sentence_index,
                    "text": sentence,
                    "wav": "",
                    "status": "failed",
                    "batch": batch,
                    "error": last_error,
                })
                self._remove_stale_takes(i)
                # 继续下一句

        if not self._canceled:
            ok = sum(1 for e in self._wav_map if e.get("status") == "ok")
            failed = len(self._wav_map) - ok
            logger.info("合成完成: 共 %d 句，成功 %d，失败 %d", total, ok, failed)
            self.log.emit(f"合成完成！共 {total} 句，成功 {ok}，失败 {failed}")
            self.log.emit(f"📝 写入 WAV 映射: {len(self._wav_map)} 条")
        self.result_ready.emit(self._wav_map)
