"""Core 模块测试"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def test_splitter():
    from index_tts_gui.core.splitter import split_sentences
    text = "你好。这是测试！真的吗？"
    result = split_sentences(text)
    assert len(result) == 3, f"Expected 3, got {len(result)}: {result}"
    assert result[0] == "你好。"
    assert result[1] == "这是测试！"
    assert result[2] == "真的吗？"
    print("✓ splitter")


def test_splitter_max_length():
    from index_tts_gui.core.splitter import RuleBasedSplitter
    text = "今天天气很好，我想出去走走，顺便买点菜回来做饭"
    splitter = RuleBasedSplitter(max_length=12)
    result = splitter.split(text)
    assert all(len(s) <= 12 for s in result), result
    assert "".join(result).replace("，", "") == text.replace("，", "")
    print("✓ splitter max_length")


def test_llm_splitter_parse():
    from index_tts_gui.core.splitter import LLMSplitter
    splitter = LLMSplitter(
        api_url="http://localhost", api_key="test", model="test"
    )
    # 模拟 LLM 输出，带编号和空行
    output = "1. 第一句。\n\n2) 第二句！\n- 第三句？"
    result = splitter._parse_output(output)
    assert result == ["第一句。", "第二句！", "第三句？"]
    print("✓ llm_splitter parse")


def test_hybrid_splitter_fallback():
    from index_tts_gui.core.splitter import HybridSplitter, LLMSplitter

    # LLM 配置无效，应回退规则拆分
    bad_llm = LLMSplitter(api_url="", api_key="", model="")
    splitter = HybridSplitter(llm_splitter=bad_llm)
    text = "你好。这是测试！"
    sentences = splitter.split(text)
    assert not splitter.used_llm
    assert len(sentences) == 2
    print("✓ hybrid_splitter fallback")


def test_llm_client_configured():
    from index_tts_gui.core.llm_client import LLMClient

    assert LLMClient.is_configured({
        "api_url": "https://api.xiaomimimo.com/v1",
        "api_key": "sk-test",
        "model": "mimo-v2-flash",
    })
    assert not LLMClient.is_configured({
        "api_url": "",
        "api_key": "sk-test",
        "model": "mimo-v2-flash",
    })
    print("✓ llm_client configured")


def test_subtitler():
    from index_tts_gui.core.subtitler import SubtitleEntry, entries_to_srt
    entries = [
        SubtitleEntry(1, 0.0, 1.5, "你好。"),
        SubtitleEntry(2, 1.5, 3.0, "这是测试。"),
    ]
    srt = entries_to_srt(entries)
    assert "00:00:00,000" in srt
    assert "你好。" in srt
    print("✓ subtitler (SRT format)")


def test_tts_client():
    from index_tts_gui.core.tts_client import (
        BaseTTSClient,
        IndexTTSClient,
        TTSClient,
        create_client,
        list_providers,
    )

    assert issubclass(IndexTTSClient, BaseTTSClient)
    assert TTSClient is IndexTTSClient
    assert "index_tts" in list_providers()

    client = create_client(provider="index_tts", api_url="http://localhost:8300")
    assert isinstance(client, IndexTTSClient)
    assert client.base_url == "http://localhost:8300"

    try:
        create_client(provider="unknown_provider")
        assert False, "应抛出 ValueError"
    except ValueError:
        pass

    # 抽象基类不能直接实例化
    try:
        BaseTTSClient()
        assert False, "应抛出 TypeError"
    except TypeError:
        pass

    print("✓ tts_client")


def test_merger_duration():
    import os
    import pytest
    from index_tts_gui.core.merger import get_wav_duration
    wav = "output_tts/sentence_01.wav"
    if not os.path.exists(wav):
        pytest.skip(f"测试音频不存在: {wav}")
    dur = get_wav_duration(wav)
    assert dur > 0, f"Invalid duration: {dur}"
    print(f"✓ merger (duration: {dur:.2f}s)")


if __name__ == "__main__":
    test_splitter()
    test_splitter_max_length()
    test_llm_splitter_parse()
    test_hybrid_splitter_fallback()
    test_llm_client_configured()
    test_subtitler()
    test_tts_client()
    test_merger_duration()
    print("\n全部通过 ✓")
