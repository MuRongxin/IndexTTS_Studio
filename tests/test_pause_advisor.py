"""
独立测试 LLM 停顿顾问。
直接调用 pause_advisor，读取 config.json 中的 LLM 配置。
"""
import json
import os

from index_tts_gui.core.pause_advisor import LLMPauseAdvisor, is_configured
from index_tts_gui.core.llm_client import LLMClient


def main():
    cfg_path = "config.json"
    if not os.path.exists(cfg_path):
        print(f"❌ {cfg_path} 不存在")
        return

    with open(cfg_path, "r", encoding="utf-8") as f:
        config = json.load(f)

    llm_cfg = config.get("llm", {})
    print("LLM 配置:")
    print(f"  preset: {llm_cfg.get('preset')}")
    print(f"  api_url: {llm_cfg.get('api_url')}")
    print(f"  model: {llm_cfg.get('model')}")
    print(f"  api_key: {'已填写' if llm_cfg.get('api_key') else '未填写'}")
    print(f"  timeout: {llm_cfg.get('timeout', 60)}")

    if not is_configured(llm_cfg):
        print("\n❌ LLM 未配置，请先填写 API URL、API Key 和模型")
        return

    sentences = [
        "你好，世界。",
        "今天天气真不错，",
        "我们一起去公园散步吧！",
        "你觉得呢",
    ]

    print(f"\n测试句子（{len(sentences)} 句）:")
    for i, s in enumerate(sentences, 1):
        print(f"  {i}. {s}")

    # 先用简单 prompt 测试 LLM 是否正常响应
    client = LLMClient(
        api_url=llm_cfg["api_url"],
        api_key=llm_cfg["api_key"],
        model=llm_cfg["model"],
        timeout=llm_cfg.get("timeout", 60),
    )

    print("\n--- 测试 1: 简单问候 ---")
    try:
        resp = client.chat_completion(
            messages=[{"role": "user", "content": "Hi, respond with 'hello' only."}],
            max_completion_tokens=20,
            temperature=0.3,
        )
        print(f"原始响应: {repr(resp)}")
    except Exception as e:
        print(f"失败: {e}")

    print("\n--- 测试 2: 直接要 JSON 数组 ---")
    prompt1 = """You are a dubbing director. Given these sentences, return ONLY a JSON array of pause durations in seconds after each sentence. Last element must be 0.

Sentences:
1. 你好，世界。
2. 今天天气真不错，
3. 我们一起去公园散步吧！
4. 你觉得呢

JSON array:"""
    try:
        resp = client.chat_completion(
            messages=[{"role": "user", "content": prompt1}],
            max_completion_tokens=200,
            temperature=0.3,
        )
        print(f"原始响应: {repr(resp)}")
    except Exception as e:
        print(f"失败: {e}")

    print("\n--- 测试 3: 要逗号分隔 ---")
    prompt2 = """You are a dubbing director. Given these sentences, return ONLY pause durations in seconds after each sentence, separated by commas. Last value must be 0. No explanation.

Sentences:
1. 你好，世界。
2. 今天天气真不错，
3. 我们一起去公园散步吧！
4. 你觉得呢

Pause durations:"""
    try:
        resp = client.chat_completion(
            messages=[{"role": "user", "content": prompt2}],
            max_completion_tokens=200,
            temperature=0.3,
        )
        print(f"原始响应: {repr(resp)}")
    except Exception as e:
        print(f"失败: {e}")

    print("\n--- 测试 4: pause_advisor 默认 prompt ---")
    advisor = LLMPauseAdvisor(
        api_url=llm_cfg["api_url"],
        api_key=llm_cfg["api_key"],
        model=llm_cfg["model"],
        timeout=llm_cfg.get("timeout", 60),
    )
    try:
        pauses = advisor.advise(sentences)
        print(f"✅ 解析成功: {pauses}")
    except Exception as e:
        print(f"❌ 失败: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
