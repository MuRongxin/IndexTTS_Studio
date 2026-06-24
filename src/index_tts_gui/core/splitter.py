"""
文稿拆分器抽象层。

提供规则拆分与 LLM 智能拆分，支持 Xiaomi MiMo、DeepSeek 等
OpenAI 兼容服务。
"""
from __future__ import annotations

import logging
import re
from abc import ABC, abstractmethod
from typing import Any

from index_tts_gui.core.llm_client import LLMClient, LLMError


logger = logging.getLogger("index_tts")


DEFAULT_MAX_LENGTH = 30


PUNCT = '。！？；：，、'

# 常见语义停顿词/连词，用于规则兜底时的二次切分
PAUSE_WORDS = [
    "但是", "然而", "不过", "可是", "只是",
    "而且", "并且", "同时", "此外", "另外",
    "所以", "因此", "于是", "因而", "从而",
    "因为", "由于", "既然",
    "如果", "假如", "要是", "倘若",
    "虽然", "尽管", "即使", "纵然",
    "当", "在", "对于", "关于", "至于",
]


class BaseSplitter(ABC):
    """拆分器抽象基类。"""

    @abstractmethod
    def split(self, text: str) -> list[str]:
        """将文本拆分为句子列表。"""
        ...


class RuleBasedSplitter(BaseSplitter):
    """
    基于标点和字数限制的规则拆分器。

    Args:
        max_length: 单句最大字数，超过则在标点/停顿词处二次切分；
                    0 或 None 表示不限制。
    """

    def __init__(self, max_length: int = 0):
        self.max_length = max_length or 0

    def split(self, text: str) -> list[str]:
        text = re.sub(r'\n+', '', text)
        raw = re.split(r'(?<=[。！？])', text)
        raw = [s.strip() for s in raw if s.strip()]

        merged = []
        for s in raw:
            if merged and not re.match(
                r'^[\u4e00-\u9fff\u201c\u2018\u300c\uff08（"“\[]', s
            ):
                merged[-1] += s
            else:
                merged.append(s)

        if self.max_length <= 0:
            return merged

        final = []
        for s in merged:
            final.extend(self._split_by_length(s))
        return final

    def _split_by_length(self, text: str) -> list[str]:
        """对超过 max_length 的句子按字数/停顿词二次切分。"""
        if len(text) <= self.max_length:
            return [text]

        parts = []
        start = 0
        while start < len(text):
            end = start + self.max_length
            if end >= len(text):
                parts.append(text[start:])
                break

            # 1. 在标点处切
            cut = self._find_punctuation_cut(text, end)
            # 2. 在停顿词前切
            if cut <= start:
                cut = self._find_pause_word_cut(text, end)
            # 3. 硬切
            if cut <= start:
                cut = end

            parts.append(text[start:cut])
            start = cut

        return [p.strip() for p in parts if p.strip()]

    def _find_punctuation_cut(self, text: str, around: int) -> int:
        """在 around 附近找标点后的切分位置。"""
        best = -1
        for offset in range(8):
            pos = around - offset
            if 0 <= pos < len(text) and text[pos] in '，、；：':
                best = pos + 1
                break
        return best

    def _find_pause_word_cut(self, text: str, around: int) -> int:
        """在 around 附近找停顿词前的切分位置。"""
        window_start = max(0, around - 10)
        window_end = min(len(text), around + 10)
        window = text[window_start:window_end]

        best = -1
        for word in PAUSE_WORDS:
            idx = window.find(word)
            if idx != -1:
                pos = window_start + idx
                # 切到停顿词前面
                if pos > 0 and pos > window_start:
                    best = pos
                    break
        return best


LLM_PRESETS: dict[str, dict[str, Any]] = {
    "mimo": {
        "api_url": "https://api.xiaomimimo.com/v1",
        # MiMo 官方当前可用模型（根据 2026-06 文档）：
        # - mimo-v2.5：基础版，价格低，适合 flash 场景
        # - mimo-v2.5-pro：旗舰版，能力最强
        "models": ["mimo-v2.5", "mimo-v2.5-pro"],
        "default_model": "mimo-v2.5",
    },
    "deepseek": {
        "api_url": "https://api.deepseek.com",
        # DeepSeek-V4 系列（2026-06 文档）
        "models": ["deepseek-v4-flash", "deepseek-v4-pro"],
        "default_model": "deepseek-v4-flash",
    },
}

DEFAULT_LLM_SYSTEM_PROMPT = "你是一位中文 TTS 配音专家。你的任务是把文稿拆成适合语音合成的短句，必须按用户要求的格式直接输出句子。"

DEFAULT_LLM_PROMPT = """请将以下文稿按语义和朗读节奏拆分成适合单次 TTS 合成的句子。
要求：
1. 每句控制在 {max_length} 字以内（除非原文本身就是一句完整长句）；
2. 优先在语义完整、语气停顿处拆分；
3. 不要改写原文，保持原意；
4. 只输出句子，每行一句，不要编号、不要解释、不要加任何前缀；
5. 如果文稿只有一句话，也直接输出这句话。

文稿：
{text}"""


class LLMSplitter(BaseSplitter):
    """
    基于 LLM 的智能拆分器。

    Args:
        api_url: OpenAI 兼容 API 地址
        api_key: API Key
        model: 模型名
        max_tokens: 最大输出 token 数
        timeout: 请求超时秒数
        max_length: 提示中约束的单句最大字数
        system_prompt: 系统提示词
        user_prompt_template: 用户提示模板，需包含 {text} 和 {max_length}
    """

    def __init__(
        self,
        api_url: str,
        api_key: str,
        model: str,
        max_completion_tokens: int = 2048,
        timeout: int = 30,
        max_length: int = DEFAULT_MAX_LENGTH,
        system_prompt: str = DEFAULT_LLM_SYSTEM_PROMPT,
        user_prompt_template: str = DEFAULT_LLM_PROMPT,
    ):
        self._client = LLMClient(
            api_url=api_url,
            api_key=api_key,
            model=model,
            timeout=timeout,
        )
        self.max_completion_tokens = max_completion_tokens
        self.max_length = max_length
        self.system_prompt = system_prompt
        self.user_prompt_template = user_prompt_template

    def split(self, text: str) -> list[str]:
        stripped = text.strip()
        if not stripped:
            return []

        user_prompt = self.user_prompt_template.format(
            text=stripped,
            max_length=self.max_length,
        )
        messages: list[dict[str, str]] = [
            {"role": "user", "content": user_prompt},
        ]
        if self.system_prompt:
            messages.insert(
                0, {"role": "system", "content": self.system_prompt}
            )

        # MiMo 等思考模型会把大量 token 用于内部推理，输出配额需要给得很足。
        # 按文本长度动态调整，保底 4096，上限 8192。
        dynamic_max_tokens = max(
            4096,
            min(8192, int(len(stripped) * 4) + 2048),
        )

        logger.info(
            "LLM 拆分: text_len=%d max_length=%d max_completion_tokens=%d",
            len(stripped), self.max_length, dynamic_max_tokens,
        )

        last_content = ""
        for attempt in range(2):
            content = self._client.chat_completion(
                messages=messages,
                max_completion_tokens=dynamic_max_tokens,
                temperature=0.3,
            )
            last_content = content
            logger.info(
                "LLM 拆分尝试 %d: content_len=%d",
                attempt + 1, len(content),
            )
            logger.debug("LLM 拆分原始响应 (attempt %d):\n%s", attempt + 1, content)

            sentences = self._parse_output(content)
            logger.info(
                "LLM 拆分解析: 原始行数=%d 解析后句子数=%d",
                len(content.splitlines()), len(sentences),
            )
            if sentences:
                return sentences

            logger.warning(
                "LLM 拆分尝试 %d 返回为空或无法解析: %s",
                attempt + 1, content[:300]
            )

        logger.error("LLM 两次拆分均失败，最后响应: %s", last_content[:500])
        raise LLMError("LLM 返回为空，请检查 Prompt 模板或模型响应")

    def _parse_output(self, content: str) -> list[str]:
        """解析 LLM 输出为句子列表。"""
        lines = content.splitlines()
        sentences = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            # 去除可能的前缀编号，如 "1. "/"1) "/"- "
            line = re.sub(r'^(\d+[\.、)])+\s*', '', line)
            line = re.sub(r'^[-*]\s+', '', line)
            if line:
                sentences.append(line)
        return sentences


class HybridSplitter(BaseSplitter):
    """
    混合拆分器：优先使用 LLM，失败或未配置时回退规则拆分。
    """

    def __init__(
        self,
        llm_splitter: LLMSplitter | None = None,
        rule_splitter: RuleBasedSplitter | None = None,
    ):
        self._llm = llm_splitter
        self._rule = rule_splitter or RuleBasedSplitter()

    def split(self, text: str) -> tuple[list[str], bool]:
        """
        返回 (sentences, used_llm)。

        如果 LLM 可用且成功，则使用 LLM 结果；否则回退规则拆分。
        """
        if self._llm is not None:
            try:
                return self._llm.split(text), True
            except Exception as e:
                logger.warning(
                    "HybridSplitter LLM 失败，回退规则拆分: %s", e, exc_info=True
                )
        return self._rule.split(text), False


# 兼容旧接口：默认使用规则拆分
def split_sentences(text: str, max_length: int = 0) -> list[str]:
    """将中文文本拆分为句子列表（规则拆分）。"""
    return RuleBasedSplitter(max_length=max_length).split(text)


def list_presets() -> list[str]:
    """返回内置 LLM 预设名称。"""
    return list(LLM_PRESETS.keys())


def get_preset(name: str) -> dict[str, str]:
    """获取指定预设的配置。"""
    return LLM_PRESETS.get(name, {})


def create_splitter(
    mode: str = "rule",
    llm_cfg: dict[str, Any] | None = None,
    max_length: int = 0,
) -> BaseSplitter:
    """
    工厂函数：根据模式创建拆分器。

    Args:
        mode: "rule" | "llm" | "auto"
        llm_cfg: LLM 配置字典，需包含 api_url/api_key/model 等
        max_length: 规则拆分时的最大句长
    """
    mode = mode.lower().strip()
    rule = RuleBasedSplitter(max_length=max_length)

    if mode == "rule":
        return rule

    llm: LLMClient | None = None
    if llm_cfg and LLMClient.is_configured(llm_cfg):
        llm = LLMSplitter(
            api_url=llm_cfg["api_url"],
            api_key=LLMClient._get_api_key(llm_cfg),
            model=llm_cfg["model"],
            max_completion_tokens=llm_cfg.get(
                "max_completion_tokens", llm_cfg.get("max_tokens", 2048)
            ),
            timeout=llm_cfg.get("timeout", 30),
            max_length=llm_cfg.get("max_sentence_length", DEFAULT_MAX_LENGTH),
            system_prompt=llm_cfg.get("system_prompt", DEFAULT_LLM_SYSTEM_PROMPT),
            user_prompt_template=llm_cfg.get(
                "user_prompt_template", DEFAULT_LLM_PROMPT
            ),
        )

    if mode == "llm":
        if llm is None:
            raise ValueError("LLM 模式需要有效的 api_url/api_key/model 配置")
        return llm

    if mode == "auto":
        return HybridSplitter(llm_splitter=llm, rule_splitter=rule)

    raise ValueError(f"未知的拆分模式: {mode}")
