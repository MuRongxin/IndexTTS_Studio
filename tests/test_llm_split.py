"""
纯脚本测试 LLM 拆分是否可用。

用法：
    python test_llm_split.py [文本文件路径]

默认读取当前目录下的 text.txt，也可指定其它文件。
LLM 配置从 config.json 读取。
"""
import json
import os
import sys
import time
import traceback

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from index_tts_gui.core.splitter import create_splitter
from index_tts_gui.core.llm_client import LLMClient


CONFIG_FILE = "config.json"
DEFAULT_TEXT_FILE = "text.txt"


def load_text(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def load_llm_config() -> dict:
    if not os.path.exists(CONFIG_FILE):
        raise FileNotFoundError(f"{CONFIG_FILE} 不存在")

    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    llm = cfg.get("llm", {})
    if not llm.get("enabled", False):
        print("⚠ config.json 中 LLM 未启用，尝试继续用已有配置测试…")

    if not LLMClient.is_configured(llm):
        raise ValueError(
            "LLM 配置不完整，请先在应用「设置」中配置 API URL、API Key 和模型。\n"
            f"当前配置: {json.dumps(llm, indent=2, ensure_ascii=False)}"
        )

    return llm


def main():
    text_path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_TEXT_FILE
    if not os.path.exists(text_path):
        print(f"❌ 文件不存在: {text_path}")
        sys.exit(1)

    text = load_text(text_path)
    print(f"📄 读取文本: {text_path}")
    print(f"   长度: {len(text)} 字")
    print("-" * 60)

    try:
        llm_cfg = load_llm_config()
    except Exception as e:
        print(f"❌ 配置错误: {e}")
        sys.exit(1)

    print("🔧 LLM 配置:")
    print(f"   API URL: {llm_cfg.get('api_url')}")
    print(f"   模型: {llm_cfg.get('model')}")
    print(f"   超时: {llm_cfg.get('timeout', 60)} 秒")
    print("-" * 60)

    print("⏳ 开始 LLM 拆分…")
    start = time.time()

    try:
        splitter = create_splitter(mode="llm", llm_cfg=llm_cfg)
        sentences = splitter.split(text)
        elapsed = time.time() - start

        print(f"✅ 拆分成功！耗时 {elapsed:.2f} 秒，共 {len(sentences)} 句:\n")
        for i, s in enumerate(sentences, 1):
            print(f"[{i}] {s}")
    except Exception as e:
        elapsed = time.time() - start
        print(f"❌ 拆分失败（耗时 {elapsed:.2f} 秒）")
        print(f"\n错误类型: {type(e).__name__}")
        print(f"错误信息: {e}")
        print("\n详细堆栈:")
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
