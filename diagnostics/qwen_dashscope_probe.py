from __future__ import annotations

import argparse
import os
import sys
import time

from openai import OpenAI


DEFAULT_MODEL = "qwen3.6-flash"
DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"


def format_usage(usage: object) -> str:
    """把 token 用量整理成便于查看的文本。"""
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


def build_client() -> OpenAI:
    """创建百炼 OpenAI 兼容客户端。"""
    api_key = (
        os.getenv("API_KEY_ALI")
        or os.getenv("ANTHROPIC_AUTH_TOKEN")
        or os.getenv("DASHSCOPE_API_KEY")
        or os.getenv("OPENROUTER_API_KEY")
    )
    if not api_key:
        raise RuntimeError("没有读取到 LLM API Key，请先设置 API_KEY_ALI。")

    return OpenAI(
        api_key=api_key,
        base_url=os.getenv("DASHSCOPE_BASE_URL", DEFAULT_BASE_URL),
    )


def run_non_stream(
    client: OpenAI,
    model: str,
    messages: list[dict[str, str]],
    enable_thinking: bool,
    timeout: float,
) -> int:
    """普通模式：等待完整回复后一次性打印。"""
    start_time = time.perf_counter()
    completion = client.chat.completions.create(
        model=model,
        messages=messages,
        extra_body={"enable_thinking": enable_thinking},
        timeout=timeout,
    )
    elapsed = time.perf_counter() - start_time

    answer = (completion.choices[0].message.content or "").strip()
    if not answer:
        print("[错误] 模型返回了空内容。")
        return 1

    print("\n===== 模型回复 =====")
    print(answer)
    print("\n===== 接口返回 =====")
    print(f"returned_model: {getattr(completion, 'model', '未返回')}")
    print(f"usage: {format_usage(getattr(completion, 'usage', None))}")
    print("\n===== 调用耗时 =====")
    print(f"总耗时: {elapsed:.2f}s")
    return 0


def run_stream(
    client: OpenAI,
    model: str,
    messages: list[dict[str, str]],
    enable_thinking: bool,
    timeout: float,
) -> tuple[int, str]:
    """流式模式：边生成边打印，并统计首 token 时间。"""
    start_time = time.perf_counter()
    first_chunk_time: float | None = None
    saw_any_output = False
    returned_model: str | None = None
    usage = None

    stream = client.chat.completions.create(
        model=model,
        messages=messages,
        extra_body={"enable_thinking": enable_thinking},
        timeout=timeout,
        stream=True,
    )

    print("\n===== 流式输出 =====")
    answer_parts: list[str] = []
    for chunk in stream:
        if returned_model is None:
            returned_model = getattr(chunk, "model", None)
        if getattr(chunk, "usage", None) is not None:
            usage = chunk.usage

        if not chunk.choices:
            continue

        delta = chunk.choices[0].delta
        reasoning = getattr(delta, "reasoning_content", None)
        content = delta.content or ""

        if reasoning:
            if first_chunk_time is None:
                first_chunk_time = time.perf_counter() - start_time
            saw_any_output = True
            print(reasoning, end="", flush=True)

        if content:
            if first_chunk_time is None:
                first_chunk_time = time.perf_counter() - start_time
            saw_any_output = True
            answer_parts.append(content)
            print(content, end="", flush=True)

    total_time = time.perf_counter() - start_time
    print()
    print("\n===== 接口返回 =====")
    print(f"returned_model: {returned_model or '未返回'}")
    print(f"usage: {format_usage(usage)}")
    print("\n===== 调用耗时 =====")
    if first_chunk_time is not None:
        print(f"首块返回: {first_chunk_time:.2f}s")
    print(f"总耗时: {total_time:.2f}s")

    if not saw_any_output:
        print("[错误] 流式模式没有收到任何输出。")
        return 1, ""
    return 0, "".join(answer_parts).strip()


def run_interactive_chat(
    client: OpenAI,
    model: str,
    system_prompt: str,
    enable_thinking: bool,
    timeout: float,
    stream: bool,
) -> int:
    """在终端里进行多轮对话，不影响主项目。"""
    messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]

    print("\n===== 交互模式 =====")
    print("直接输入内容开始对话。")
    print("输入 /clear 清空上下文，输入 /exit 退出。")

    while True:
        try:
            user_input = input("\n你: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\n已退出。")
            return 0

        if not user_input:
            continue
        if user_input.lower() == "/exit":
            print("已退出。")
            return 0
        if user_input.lower() == "/clear":
            messages = [{"role": "system", "content": system_prompt}]
            print("上下文已清空。")
            continue

        messages.append({"role": "user", "content": user_input})

        try:
            if stream:
                print("模型: ", end="", flush=True)
                status, answer = run_stream(
                    client=client,
                    model=model,
                    messages=messages,
                    enable_thinking=enable_thinking,
                    timeout=timeout,
                )
                if status != 0 or not answer:
                    messages.pop()
                    continue
            else:
                start_time = time.perf_counter()
                completion = client.chat.completions.create(
                    model=model,
                    messages=messages,
                    extra_body={"enable_thinking": enable_thinking},
                    timeout=timeout,
                )
                elapsed = time.perf_counter() - start_time
                answer = (completion.choices[0].message.content or "").strip()
                if not answer:
                    print("[错误] 模型返回了空内容。")
                    messages.pop()
                    continue
                print(f"模型: {answer}")
                print(f"returned_model: {getattr(completion, 'model', '未返回')}")
                print(f"usage: {format_usage(getattr(completion, 'usage', None))}")
                print(f"本轮耗时: {elapsed:.2f}s")

            messages.append({"role": "assistant", "content": answer})
        except Exception as exc:
            messages.pop()
            print(f"[调用失败] {type(exc).__name__}: {exc}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="独立测试阿里云百炼 Qwen 模型调用，不影响主项目。"
    )
    parser.add_argument(
        "--model",
        default=os.getenv("DASHSCOPE_MODEL", DEFAULT_MODEL),
        help=f"模型名，默认 {DEFAULT_MODEL}",
    )
    parser.add_argument(
        "--prompt",
        default="你好，请用自然、简短的中文和我打个招呼。",
        help="用户提示词",
    )
    parser.add_argument(
        "--system",
        default="你是一个中文数字人助手，请用口语化、简洁的中文回答。",
        help="系统提示词",
    )
    parser.add_argument(
        "--stream",
        action="store_true",
        help="开启流式输出，便于观察首块返回速度。",
    )
    parser.add_argument(
        "--enable-thinking",
        action="store_true",
        help="开启思考模式。数字人场景通常不建议开启，因为会更慢。",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="请求超时时间，默认 30 秒。",
    )
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="进入终端多轮对话模式。",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    try:
        client = build_client()
    except Exception as exc:
        print(f"[初始化失败] {exc}")
        return 1

    print("===== 百炼独立调用测试 =====")
    print(f"Base URL: {os.getenv('DASHSCOPE_BASE_URL', DEFAULT_BASE_URL)}")
    print(f"Model: {args.model}")
    print(f"Thinking: {'ON' if args.enable_thinking else 'OFF'}")
    print(f"Stream: {'ON' if args.stream else 'OFF'}")
    print(f"Timeout: {args.timeout:.1f}s")

    try:
        if args.interactive:
            return run_interactive_chat(
                client=client,
                model=args.model,
                system_prompt=args.system,
                enable_thinking=args.enable_thinking,
                timeout=args.timeout,
                stream=args.stream,
            )

        messages = [
            {"role": "system", "content": args.system},
            {"role": "user", "content": args.prompt},
        ]

        if args.stream:
            status, _answer = run_stream(
                client=client,
                model=args.model,
                messages=messages,
                enable_thinking=args.enable_thinking,
                timeout=args.timeout,
            )
            return status

        return run_non_stream(
            client=client,
            model=args.model,
            messages=messages,
            enable_thinking=args.enable_thinking,
            timeout=args.timeout,
        )
    except Exception as exc:
        print(f"[调用失败] {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
