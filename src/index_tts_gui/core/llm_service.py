"""
LLM 服务统一入口。

将原先分散在 LLMClient / LLMSplitter / LLMPauseAdvisor 中的逻辑
收敛为一个类，外部只需传入 config dict 即可调用所有 LLM 功能。
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from index_tts_gui.core.llm_client import LLMClient, LLMError


logger = logging.getLogger("index_tts")

# ── 预设 ──

LLM_PRESETS: dict[str, dict[str, Any]] = {
    "mimo": {
        "api_url": "https://api.xiaomimimo.com/v1",
        "models": ["mimo-v2.5", "mimo-v2.5-pro"],
        "default_model": "mimo-v2.5",
    },
    "deepseek": {
        "api_url": "https://api.deepseek.com",
        "models": ["deepseek-v4-flash", "deepseek-v4-pro"],
        "default_model": "deepseek-v4-flash",
    },
}

# ── Prompt 默认值 ──

DEFAULT_SPLIT_SYSTEM_PROMPT = (
    "你是一位中文 TTS 配音专家。"
    "你的任务是把文稿拆成适合语音合成的短句，必须按用户要求的格式直接输出句子。"
)

DEFAULT_SPLIT_PROMPT = """请将以下文稿按语义和朗读节奏拆分成适合单次 TTS 合成的句子。
要求：
1. 每句控制在 {max_length} 字以内（除非原文本身就是一句完整长句）；
2. 优先在语义完整、语气停顿处拆分；
3. 不要改写原文，保持原意；
4. 只输出句子，每行一句，不要编号、不要解释、不要加任何前缀；
5. 如果文稿只有一句话，也直接输出这句话。

文稿：
{text}"""

DEFAULT_PAUSE_PROMPT = """你是一位配音导演。以下是已拆分的配音句子，每句附有序号。

请为每句**之后**建议一个停顿时长（秒），让整段配音听起来自然、有节奏感。

输出格式：JSON 数组，每个元素含 i（句子序号）和 p（停顿秒数）：
[{"i": 0, "p": 0.35}, {"i": 1, "p": 0.50}, ...]

要求：
1. 仅输出 JSON 数组，不要任何解释或 markdown；
2. 序号 i 与下面给出的序号一一对应；
3. 最后一句的停顿 p 必须为 0；
4. 数值范围 0.0 ~ 2.0，建议精确到 0.05；
5. 根据语义完整性、标点符号、情感转折决定停顿，不要每句都相同。

句子列表：
{sentences_indexed}

请直接输出 JSON 数组："""


class LLMServiceError(RuntimeError):
    pass


class LLMService:
    """LLM 服务统一入口。

    从 config dict 读取配置，提供拆分与停顿建议。
    """

    def __init__(self, cfg: dict[str, Any] | None = None):
        self._cfg = cfg or {}

    # ── 配置读取 ──

    @property
    def api_url(self) -> str:
        preset = self._cfg.get("preset", "")
        if preset and preset in LLM_PRESETS:
            return self._cfg.get("api_url", "") or LLM_PRESETS[preset]["api_url"]
        return self._cfg.get("api_url", "")

    @property
    def api_key(self) -> str:
        preset = self._cfg.get("preset", "")
        key = self._cfg.get(f"{preset}_key", "") or self._cfg.get("api_key", "")
        return key.strip()

    @property
    def model(self) -> str:
        return self._cfg.get("model", "")

    @property
    def timeout(self) -> int:
        return self._cfg.get("timeout", 60)

    @property
    def max_completion_tokens(self) -> int:
        return self._cfg.get("max_completion_tokens", 2048)

    @property
    def max_sentence_length(self) -> int:
        return self._cfg.get("max_sentence_length", 30)

    @property
    def punctuation_fallback(self) -> bool:
        return self._cfg.get("punctuation_fallback", False)

    def is_configured(self) -> bool:
        return bool(self.api_url and self.api_key and self.model)

    def _make_client(self) -> LLMClient:
        if not self.is_configured():
            raise LLMServiceError("LLM 未配置：请检查 api_url / api_key / model")
        return LLMClient(
            api_url=self.api_url,
            api_key=self.api_key,
            model=self.model,
            timeout=self.timeout,
        )

    # ── 测试连接 ──

    def test(self) -> str:
        client = self._make_client()
        return client.test_connection()

    # ── 文本拆分 ──

    CHUNK_SIZE = 2000  # 每块最大字符数

    def split_text(
        self,
        text: str,
        max_length: int | None = None,
        on_progress: callable | None = None,
    ) -> list[str]:
        """用 LLM 将文稿拆分为句子列表。长文稿自动分块处理。

        Args:
            text: 原文
            max_length: 单句最大字数
            on_progress: 进度回调 (current: int, total: int, message: str)
        """
        stripped = text.strip()
        if not stripped:
            return []

        max_len = max_length or self.max_sentence_length

        # 短文稿直接发送
        if len(stripped) <= self.CHUNK_SIZE:
            if on_progress:
                on_progress(1, 1, "正在拆分…")
            return self._split_chunk(stripped, max_len)

        # 长文稿分块
        chunks = self._chunk_text(stripped)
        total = len(chunks)
        logger.info("LLMService.split: 分 %d 块处理 (text_len=%d)", total, len(stripped))

        all_sentences: list[str] = []
        for i, chunk in enumerate(chunks):
            msg = f"拆分第 {i+1}/{total} 块…"
            logger.info("LLMService.split: %s (%d 字)", msg, len(chunk))
            if on_progress:
                on_progress(i + 1, total, msg)
            sentences = self._split_chunk(chunk, max_len)
            all_sentences.extend(sentences)

        logger.info("LLMService.split: 共 %d 句", len(all_sentences))
        return all_sentences

    def _split_chunk(self, text: str, max_length: int) -> list[str]:
        """对单块文本执行 LLM 拆分。"""
        system_prompt = self._cfg.get("system_prompt", "") or DEFAULT_SPLIT_SYSTEM_PROMPT
        prompt_template = self._cfg.get("user_prompt_template", "") or DEFAULT_SPLIT_PROMPT

        user_prompt = prompt_template.format(text=text, max_length=max_length)
        messages: list[dict] = [
            {"role": "user", "content": user_prompt},
        ]
        if system_prompt:
            messages.insert(0, {"role": "system", "content": system_prompt})

        client = self._make_client()
        dynamic_max_tokens = max(4096, min(8192, len(text) * 4 + 2048))

        last_content = ""
        for attempt in range(2):
            content = client.chat_completion(
                messages=messages,
                max_completion_tokens=dynamic_max_tokens,
                temperature=0.3,
            )
            last_content = content
            sentences = self._parse_split_output(content)
            if sentences:
                return sentences
            logger.warning("LLMService.split attempt %d empty: %s", attempt + 1, content[:300])

        raise LLMServiceError(f"LLM 拆分失败，两次均返回空结果: {last_content[:500]}")

    def _chunk_text(self, text: str) -> list[str]:
        """按自然段分块，每块尽量不超过 CHUNK_SIZE。"""
        # 先按双换行（自然段）切分
        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
        if not paragraphs:
            return [text]

        chunks: list[str] = []
        current = ""
        for para in paragraphs:
            if len(current) + len(para) + 2 <= self.CHUNK_SIZE:
                current = f"{current}\n\n{para}" if current else para
            else:
                if current:
                    chunks.append(current)
                # 如果单段就超过限制，按句末标点再切
                if len(para) > self.CHUNK_SIZE:
                    sub_chunks = self._split_long_paragraph(para)
                    chunks.extend(sub_chunks)
                    current = ""
                else:
                    current = para
        if current:
            chunks.append(current)
        return chunks

    def _split_long_paragraph(self, para: str) -> list[str]:
        """超长段落按。！？切分，尽量保持在 CHUNK_SIZE 以内。"""
        parts = re.split(r'(?<=[。！？])', para)
        chunks: list[str] = []
        current = ""
        for part in parts:
            if len(current) + len(part) <= self.CHUNK_SIZE:
                current += part
            else:
                if current:
                    chunks.append(current)
                current = part
        if current:
            chunks.append(current)
        return chunks or [para]

    def _parse_split_output(self, content: str) -> list[str]:
        lines = content.splitlines()
        sentences = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            line = re.sub(r'^(\d+[\.、)])+\s*', '', line)
            line = re.sub(r'^[-*]\s+', '', line)
            if line:
                sentences.append(line)
        return sentences

    # ── 停顿建议 ──

    PAUSE_CHUNK_SIZE = 42  # 单次 LLM 最多处理句子数

    def advise_pauses(
        self, sentences: list[str],
        on_progress: callable | None = None,
    ) -> list[float]:
        """用 LLM 为每句音频建议停顿时长。句数多时分块处理。"""
        if not sentences:
            return []

        # 短列表直接发送
        if len(sentences) <= self.PAUSE_CHUNK_SIZE:
            if on_progress:
                on_progress(1, 1, "询问停顿建议…")
            return self._advise_pauses_chunk(sentences, start_index=0)

        # 长列表分块
        total_chunks = (len(sentences) + self.PAUSE_CHUNK_SIZE - 1) // self.PAUSE_CHUNK_SIZE
        all_pauses: list[float] = []
        for i in range(0, len(sentences), self.PAUSE_CHUNK_SIZE):
            chunk_idx = i // self.PAUSE_CHUNK_SIZE + 1
            chunk = sentences[i : i + self.PAUSE_CHUNK_SIZE]
            msg = f"询问停顿建议: 第 {chunk_idx}/{total_chunks} 块 ({len(chunk)} 句)"
            logger.info("LLMService.pauses: %s", msg)
            if on_progress:
                on_progress(chunk_idx, total_chunks, msg)

            pauses = self._advise_pauses_chunk(chunk, start_index=i)
            if all_pauses:
                if on_progress:
                    on_progress(chunk_idx, total_chunks, "计算块边界停顿…")
                boundary = self._advise_boundary_pause(
                    sentences[i - 1], sentences[i]
                )
                all_pauses[-1] = boundary
            all_pauses.extend(pauses)

        logger.info("LLMService.pauses: 共 %d 句完成", len(all_pauses))
        return all_pauses

    def _advise_boundary_pause(self, prev_sentence: str, next_sentence: str) -> float:
        """询问 LLM 两句之间的停顿时长。"""
        prompt = f"""你是配音导演。下面是两段相邻的配音句子：

前句：{prev_sentence}
后句：{next_sentence}

请仅输出前句之后应该停顿的秒数（0.0~2.0），不要输出其他内容。"""
        messages = [{"role": "user", "content": prompt}]
        client = self._make_client()
        content = client.chat_completion(
            messages=messages,
            max_completion_tokens=32,
            temperature=0.3,
        )
        content = content.strip()
        try:
            val = float(re.findall(r"[\d.]+", content)[0])
            return round(max(0.0, min(2.0, val)), 2)
        except (ValueError, IndexError):
            logger.warning("LLMService: 无法解析边界停顿，使用默认 0.3s: %s", content[:50])
            return 0.3

    def _advise_pauses_chunk(self, sentences: list[str], start_index: int = 0) -> list[float]:
        """对单块句子请求停顿建议。数量不对时只重试缺失的序号。"""
        prompt_template = self._cfg.get("pause_prompt_template", "") or DEFAULT_PAUSE_PROMPT
        expected = len(sentences)
        client = self._make_client()

        # 构建序号化句子列表
        indexed_lines = "\n".join(
            f"{start_index + i}: {s}" for i, s in enumerate(sentences)
        )
        prompt = prompt_template.replace("{sentences_indexed}", indexed_lines)
        messages: list[dict] = [{"role": "user", "content": prompt}]
        dynamic_max_tokens = max(1024, min(8192, expected * 80 + 1024))

        all_pauses: dict[int, float] = {}
        missing = set(range(start_index, start_index + expected))

        for attempt in range(3):
            content = client.chat_completion(
                messages=messages,
                max_completion_tokens=dynamic_max_tokens,
                temperature=0.3,
            )
            # 解析已获得的停顿
            try:
                parsed = self._parse_pauses_indexed(content, expected, start_index)
                all_pauses.update(parsed)
                missing -= set(parsed.keys())
            except LLMServiceError as e:
                logger.warning("LLMService.pauses 第 %d 次解析失败: %s", attempt + 1, e)
                parsed = {}

            if not missing:
                return [all_pauses[start_index + i] for i in range(expected)]

            if missing:
                logger.warning(
                    "LLMService.pauses 第 %d 次仍缺 %d 个序号: %s",
                    attempt + 1, len(missing), sorted(missing)[:10],
                )
            if attempt == 2:
                # 用标点规则补缺
                from index_tts_gui.core.merger import _compute_pauses
                fallback = _compute_pauses(sentences)
                for i in missing:
                    local_i = i - start_index
                    if local_i < len(fallback):
                        all_pauses[i] = fallback[local_i]
                logger.warning(
                    "LLMService.pauses 3 次仍缺 %d 个，用标点规则补足", len(missing)
                )
                break

            # 追加纠正消息，只问缺失的句子
            missing_sentences = {
                idx: sentences[idx - start_index]
                for idx in sorted(missing)
            }
            messages.append({"role": "assistant", "content": content})
            messages.append({
                "role": "user",
                "content": (
                    f"还缺少以下句子的停顿建议，请只补充这些：\n"
                    + "\n".join(f"{i}: {t}" for i, t in sorted(missing_sentences.items()))
                    + f"\n\n请输出 JSON 数组，每项含 i 和 p。"
                ),
            })
            dynamic_max_tokens = max(256, len(missing) * 40 + 128)

        return [all_pauses.get(start_index + i, 0.3) for i in range(expected)]

    def _parse_pauses_indexed(
        self, content: str, expected_count: int, start_index: int
    ) -> dict[int, float]:
        """解析 [{"i": 0, "p": 0.35}, ...] 格式的停顿建议，返回 {序号: 停顿值}。"""
        content = content.strip()
        # 去除 LLM 多余追加的标点/句号和尾部非 JSON 字符
        content = re.sub(r'[。！？，、；：\s]+$', '', content)
        # 如果末尾是 ,] 这种残缺格式，补齐 ]
        if content.endswith(','):
            content = content[:-1]
        if not content.endswith(']'):
            # 尝试找到最后一个完整的 } 并闭合
            last_brace = content.rfind('}')
            if last_brace > 0:
                content = content[:last_brace + 1] + ']'
        logger.info("LLMService._parse_pauses_indexed: 处理后内容前200字: %s", content[:200])
        candidates: list[str] = []

        # 1. markdown 代码块
        cb = re.search(r"```(?:json)?\s*([\s\S]*?)```", content, re.IGNORECASE)
        if cb:
            candidates.append(cb.group(1).strip())
        # 2. 方括号数组
        br = re.search(r"\[[\s\S]*\]", content)
        if br:
            candidates.append(br.group(0).strip())
        # 3. 兜底
        candidates.append(content)

        # 解析 [{"i":..., "p":...}] 格式
        items = None
        for raw in candidates:
            try:
                items = json.loads(raw)
                if isinstance(items, list) and all(
                    isinstance(it, dict) and "i" in it and "p" in it for it in items
                ):
                    break
            except json.JSONDecodeError:
                pass
            items = None

        if items is None:
            # 容错：LLM 返回的 JSON 可能被截断，尝试用 raw_decode 解析完整部分
            for raw in candidates:
                raw = raw.strip()
                if not raw.startswith("["):
                    continue
                try:
                    decoder = json.JSONDecoder()
                    items, _ = decoder.raw_decode(raw)
                    if isinstance(items, list):
                        break
                except json.JSONDecodeError:
                    # 最后手段：在最后一个完整 "}]" 处截断
                    last_complete = raw.rfind('"}')
                    if last_complete > 0:
                        try:
                            items = json.loads(raw[:last_complete + 2] + "]")
                            if isinstance(items, list):
                                break
                        except json.JSONDecodeError:
                            pass

        if items is None or not isinstance(items, list):
            raise LLMServiceError(f"无法解析停顿建议: {content[:200]}")

        result: dict[int, float] = {}
        for item in items:
            try:
                idx = int(item["i"])
                val = float(item["p"])
                if start_index <= idx < start_index + expected_count:
                    result[idx] = round(max(0.0, min(2.0, val)), 2)
            except (TypeError, ValueError, KeyError):
                continue

        if not result:
            raise LLMServiceError(f"停顿建议中没有有效数据: {content[:200]}")

        # 最后一句强制为 0
        last_idx = start_index + expected_count - 1
        if last_idx in result:
            result[last_idx] = 0.0

        return result


# ── 兼容旧接口 ──

def list_presets() -> list[str]:
    return list(LLM_PRESETS.keys())


def get_preset(name: str) -> dict:
    return LLM_PRESETS.get(name, {})
