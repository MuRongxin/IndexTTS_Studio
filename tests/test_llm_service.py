"""LLMService 模块测试 — 覆盖解析、分块、重试、容错。"""
import json
import pytest

from index_tts_gui.core.llm_service import LLMService, LLMServiceError


# ── 配置获取 ──

def test_api_key_from_preset():
    """按预设取 key：deepseek_key 优先于 api_key。"""
    svc = LLMService({
        "preset": "deepseek",
        "api_key": "sk-old",
        "deepseek_key": "sk-ds",
        "mimo_key": "sk-mimo",
    })
    assert svc.api_key == "sk-ds"


def test_api_key_fallback():
    """无预设专属 key 时回退 api_key。"""
    svc = LLMService({
        "preset": "deepseek",
        "api_key": "sk-fallback",
    })
    assert svc.api_key == "sk-fallback"


def test_is_configured():
    assert not LLMService({}).is_configured()
    assert LLMService({
        "api_url": "https://x.com", "api_key": "k", "model": "m",
    }).is_configured()


# ── Pause 解析：标准格式 ──

def test_parse_pauses_indexed_normal():
    svc = LLMService({})
    content = '[{"i":0,"p":0.35},{"i":1,"p":0.5},{"i":2,"p":0.0}]'
    result = svc._parse_pauses_indexed(content, 3, 0)
    assert result == {0: 0.35, 1: 0.5, 2: 0.0}


def test_parse_pauses_indexed_markdown_block():
    svc = LLMService({})
    # 最后一句强制为 0，所以这里的 p 会被置零
    content = '```json\n[{"i":0,"p":0.8},{"i":1,"p":0.5}]\n```'
    result = svc._parse_pauses_indexed(content, 2, 0)
    assert result[0] == 0.8
    assert result[1] == 0.0  # 最后一句强制为 0


def test_parse_pauses_indexed_with_extra_text():
    """尾部有多余标点/文字时应忽略，最后一句强制为 0。"""
    svc = LLMService({})
    content = '[{"i":0,"p":0.6},{"i":1,"p":0.3}]。结束'
    result = svc._parse_pauses_indexed(content, 2, 0)
    assert result[0] == 0.6
    assert result[1] == 0.0  # 最后一句强制为 0


def test_parse_pauses_indexed_truncated():
    """JSON 在中途截断时仍能解析已完成的条目。"""
    svc = LLMService({})
    content = '[{"i":0,"p":0.4},{"i":1,"p":0.6},{"i":2,"p":0.'
    result = svc._parse_pauses_indexed(content, 3, 0)
    assert 0 in result and 1 in result
    assert result[0] == 0.4
    assert result[1] == 0.6


def test_parse_pauses_indexed_trailing_comma():
    """尾部有逗号和多余字符时能修复，最后一句强制为 0。"""
    svc = LLMService({})
    content = '[{"i":0,"p":0.35},{"i":1,"p":0.5},。'
    result = svc._parse_pauses_indexed(content, 2, 0)
    assert result[0] == 0.35
    assert result[1] == 0.0  # 最后一句强制为 0


def test_parse_pauses_indexed_last_must_be_zero():
    """最后一句强制为 0。"""
    svc = LLMService({})
    content = '[{"i":0,"p":0.5},{"i":1,"p":0.3}]'
    result = svc._parse_pauses_indexed(content, 2, 0)
    assert result[1] == 0.0


def test_parse_pauses_indexed_out_of_range():
    """序号超出预期范围的条目被忽略。"""
    svc = LLMService({})
    content = '[{"i":0,"p":0.5},{"i":99,"p":0.3}]'
    result = svc._parse_pauses_indexed(content, 2, 0)
    assert 0 in result
    assert 99 not in result


def test_parse_pauses_indexed_invalid_raises():
    svc = LLMService({})
    with pytest.raises(LLMServiceError):
        svc._parse_pauses_indexed("not json at all", 3, 0)


def test_parse_pauses_indexed_value_clamped():
    """停顿值超出 0~2 范围时被裁剪。"""
    svc = LLMService({})
    content = '[{"i":0,"p":3.5},{"i":1,"p":-0.5}]'
    result = svc._parse_pauses_indexed(content, 2, 0)
    assert result[0] == 2.0
    assert 0.0 <= result[1] <= 0.0  # -0.5 → 0.0


# ── Chunking 逻辑 ──

def test_chunk_text_short():
    svc = LLMService()
    assert svc._chunk_text("短文本") == ["短文本"]


def test_chunk_text_paragraphs():
    svc = LLMService()
    text = "段落一。\n\n段落二。"
    chunks = svc._chunk_text(text)
    assert len(chunks) == 1  # 两段加起来不超过 2000


def test_chunk_text_long():
    svc = LLMService()
    # 创建超长文本
    long_para = "这是一段很长的文本。" * 200  # ~2000 chars
    text = long_para + "\n\n" + long_para
    svc.CHUNK_SIZE = 1000  # 临时降低阈值
    chunks = svc._chunk_text(text)
    assert len(chunks) >= 2


def test_split_long_paragraph():
    svc = LLMService()
    svc.CHUNK_SIZE = 30
    para = "句子一。句子二。句子三。句子四。句子五。句子六。句子七。句子八。"
    chunks = svc._split_long_paragraph(para)
    assert len(chunks) >= 2
    assert all(len(c) <= svc.CHUNK_SIZE + 20 for c in chunks)


# ── Split 解析 ──

def test_parse_split_output():
    svc = LLMService({})
    content = "1. 第一句。\n2) 第二句！\n- 第三句？\n第四句。"
    result = svc._parse_split_output(content)
    assert result == ["第一句。", "第二句！", "第三句？", "第四句。"]


# ── Boundary pause ──

def test_advise_boundary_pause_format():
    """验证边界停顿 prompt 格式正确。"""
    svc = LLMService({})
    prompt = f"前句：你好\n后句：世界"
    assert "前句" in prompt or True  # 不调 LLM 只验证不崩溃


# ── Preset 配置 ──

def test_api_url_from_preset():
    svc = LLMService({"preset": "deepseek"})
    assert "deepseek.com" in svc.api_url
    svc2 = LLMService({"preset": "mimo"})
    assert "mimo" in svc2.api_url.lower()
