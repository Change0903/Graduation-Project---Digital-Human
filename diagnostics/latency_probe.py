from __future__ import annotations

import argparse
import asyncio
import os
import re
import statistics
import time
from pathlib import Path

import edge_tts
from openai import OpenAI


BASE_DIR = Path(__file__).resolve().parents[1]
OUTPUT_DIR = BASE_DIR / "diagnostics" / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

VOICE = "zh-CN-XiaoxiaoNeural"
LLM_MODEL = os.getenv("DASHSCOPE_MODEL", "qwen3.6-flash")
LLM_BASE_URL = os.getenv(
    "DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
)


def resolve_llm_api_key() -> str:
    """优先读取 API_KEY_ALI，兼容旧变量。"""
    for env_name in ("API_KEY_ALI", "ANTHROPIC_AUTH_TOKEN", "DASHSCOPE_API_KEY", "OPENROUTER_API_KEY"):
        api_key = os.getenv(env_name)
        if api_key:
            return api_key
    raise RuntimeError("未读取到 LLM API Key，无法测试 LLM 耗时。请先设置 API_KEY_ALI。")


def clean_text_for_tts(text: str) -> str:
    """和主项目保持一致，避免测试文本格式影响结果。"""
    text = re.sub(r"（.*?）", "", text)
    text = re.sub(r"\(.*?\)", "", text)
    text = re.sub(r"[^0-9a-zA-Z\u4e00-\u9fa5，。！？；：、“”‘’《》…\-\s]", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def build_client() -> OpenAI:
    return OpenAI(
        api_key=resolve_llm_api_key(),
        base_url=LLM_BASE_URL,
    )


def classify_bottleneck(llm_time: float, tts_time: float) -> str:
    if llm_time <= 0 and tts_time > 0:
        return "当前仅测试了 TTS"
    if tts_time <= 0 and llm_time > 0:
        return "当前仅测试了 LLM"
    if llm_time > tts_time * 1.25:
        return "当前主要慢在 LLM 请求"
    if tts_time > llm_time * 1.25:
        return "当前主要慢在 TTS 生成"
    return "LLM 和 TTS 耗时接近"


def print_section(title: str) -> None:
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)


async def run_tts_probe(text: str, run_index: int) -> dict:
    cleaned = clean_text_for_tts(text)
    if not cleaned:
        raise RuntimeError("TTS 测试文本为空。")

    output_file = OUTPUT_DIR / f"tts_probe_{int(time.time() * 1000)}_{run_index}.mp3"
    start = time.perf_counter()
    await asyncio.to_thread(
        edge_tts.Communicate(cleaned, VOICE, rate="+10%").save_sync,
        str(output_file),
    )
    elapsed = time.perf_counter() - start

    if not output_file.exists() or output_file.stat().st_size <= 0:
        raise RuntimeError("TTS 测试失败：音频文件未生成。")

    return {
        "input_text": cleaned,
        "seconds": elapsed,
        "output_file": str(output_file),
        "size_bytes": output_file.stat().st_size,
    }


def run_llm_probe(client: OpenAI, prompt: str) -> dict:
    start = time.perf_counter()
    completion = client.chat.completions.create(
        model=LLM_MODEL,
        messages=[
            {
                "role": "system",
                "content": "请用中文口语化回复，控制在一句话。",
            },
            {
                "role": "user",
                "content": prompt,
            },
        ],
        extra_body={"enable_thinking": False},
        timeout=20,
    )
    elapsed = time.perf_counter() - start
    reply = (completion.choices[0].message.content or "").strip()
    if not reply:
        raise RuntimeError("LLM 返回了空内容。")

    return {
        "reply_text": reply,
        "seconds": elapsed,
        "returned_model": getattr(completion, "model", "未返回"),
        "usage": getattr(completion, "usage", None),
    }


def format_usage(usage: object) -> str:
    if usage is None:
        return "未返回 usage"

    prompt_tokens = getattr(usage, "prompt_tokens", None)
    completion_tokens = getattr(usage, "completion_tokens", None)
    total_tokens = getattr(usage, "total_tokens", None)

    parts: list[str] = []
    if prompt_tokens is not None:
        parts.append(f"prompt={prompt_tokens}")
    if completion_tokens is not None:
        parts.append(f"completion={completion_tokens}")
    if total_tokens is not None:
        parts.append(f"total={total_tokens}")
    return ", ".join(parts) if parts else "未返回 usage"


async def run_probe(text: str, repeat: int, skip_llm: bool) -> None:
    client = None if skip_llm else build_client()

    llm_times: list[float] = []
    tts_times: list[float] = []
    llm_reply = ""
    tts_error_messages: list[str] = []
    llm_error_messages: list[str] = []

    print_section("数字人语音链路测速")
    print(f"测试轮数: {repeat}")
    print(f"测试文本: {text}")
    print(f"测试语音: {VOICE}")
    print(f"测试模型: {LLM_MODEL}")
    print(f"模型地址: {LLM_BASE_URL}")
    print(f"独立输出目录: {OUTPUT_DIR}")

    for index in range(1, repeat + 1):
        print_section(f"第 {index} 轮")

        if client is not None:
            try:
                llm_result = run_llm_probe(client, text)
                llm_reply = llm_result["reply_text"]
                llm_times.append(llm_result["seconds"])
                print(f"[LLM] 耗时: {llm_result['seconds']:.2f}s")
                print(f"[LLM] 返回模型: {llm_result['returned_model']}")
                print(f"[LLM] usage: {format_usage(llm_result['usage'])}")
                print(f"[LLM] 回复: {llm_reply}")
            except Exception as exc:
                llm_error_messages.append(str(exc))
                llm_reply = ""
                print(f"[LLM] 失败: {exc}")
        else:
            llm_reply = text
            print("[LLM] 已跳过，本轮直接用测试文本做 TTS")

        if not llm_reply:
            print("[TTS] 已跳过，因为当前没有可用文本。")
            continue

        try:
            tts_result = await run_tts_probe(llm_reply, index)
            tts_times.append(tts_result["seconds"])
            print(f"[TTS] 耗时: {tts_result['seconds']:.2f}s")
            print(f"[TTS] 文件: {tts_result['output_file']}")
            print(f"[TTS] 大小: {tts_result['size_bytes']} bytes")
        except Exception as exc:
            tts_error_messages.append(str(exc))
            print(f"[TTS] 失败: {exc}")
            continue

        if client is not None:
            total = llm_result["seconds"] + tts_result["seconds"]
            print(f"[总耗时] {total:.2f}s")

    print_section("测速总结")

    avg_llm = statistics.mean(llm_times) if llm_times else 0.0
    avg_tts = statistics.mean(tts_times) if tts_times else 0.0

    if llm_times:
        print(f"LLM 平均耗时: {avg_llm:.2f}s")
        print(f"LLM 最快/最慢: {min(llm_times):.2f}s / {max(llm_times):.2f}s")
    else:
        print("LLM 平均耗时: 已跳过")

    if tts_times:
        print(f"TTS 平均耗时: {avg_tts:.2f}s")
        print(f"TTS 最快/最慢: {min(tts_times):.2f}s / {max(tts_times):.2f}s")
    else:
        print("TTS 平均耗时: 无成功样本")

    print(f"结论: {classify_bottleneck(avg_llm, avg_tts)}")

    if llm_error_messages:
        print(f"LLM 失败样本: {llm_error_messages[-1]}")
    if tts_error_messages:
        print(f"TTS 失败样本: {tts_error_messages[-1]}")

    print("\n简单判断参考：")
    print("- LLM > 4s：接口或模型响应偏慢")
    print("- TTS > 3s：在线语音生成偏慢")
    print("- 如果两者都慢，通常优先怀疑网络或模型排队")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="独立测试数字人语音链路耗时，不影响主程序。")
    parser.add_argument(
        "--text",
        default="你好，请用一句自然的中文和我打个招呼。",
        help="用于测速的测试文本",
    )
    parser.add_argument(
        "--repeat",
        type=int,
        default=2,
        help="重复测试次数，默认 2 次。",
    )
    parser.add_argument(
        "--skip-llm",
        action="store_true",
        help="只测试 TTS，不测试大模型请求。",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    asyncio.run(run_probe(args.text, args.repeat, args.skip_llm))
