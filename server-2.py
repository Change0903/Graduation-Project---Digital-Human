from __future__ import annotations

import asyncio
import json
import os
import re
import time
import uuid
from pathlib import Path

import edge_tts
import websockets
from openai import OpenAI

from emotion.worker_client import EmotionWorkerClient


API_KEY = os.getenv("OPENROUTER_API_KEY")
if not API_KEY:
    raise RuntimeError("没有读取到 OPENROUTER_API_KEY，请先在环境变量中设置。")

BASE_DIR = Path(__file__).resolve().parent
CONFIGURED_FRONTEND_WEB_DIR = Path(r"E:\PycharmDemo\Deepseek\digital-human-web\web")
FALLBACK_FRONTEND_WEB_DIR = BASE_DIR / "digital-human-web" / "web"
VOICE = "zh-CN-XiaoxiaoNeural"
LLM_MODEL = "z-ai/glm-4.5-air:free"
HTTP_REFERER = os.getenv("OPENROUTER_SITE_URL", "https://your-site-url.com")
SITE_TITLE = os.getenv("OPENROUTER_SITE_TITLE", "Digital Human Demo")


def resolve_frontend_web_dir() -> Path:
    if CONFIGURED_FRONTEND_WEB_DIR.exists():
        return CONFIGURED_FRONTEND_WEB_DIR
    return FALLBACK_FRONTEND_WEB_DIR


FRONTEND_WEB_DIR = resolve_frontend_web_dir()
AUDIO_DIR = FRONTEND_WEB_DIR / "media" / "audio"
VIDEO_DIR = FRONTEND_WEB_DIR / "media" / "video"
AUDIO_DIR.mkdir(parents=True, exist_ok=True)
VIDEO_DIR.mkdir(parents=True, exist_ok=True)

client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=API_KEY,
)

emotion_worker = EmotionWorkerClient(BASE_DIR)

messages = [
    {
        "role": "system",
        "content": (
            "你是一个温柔、有趣的中文语音助手，正在和用户进行电话聊天。\n"
            "回复要求：\n"
            "1. 像微信语音聊天一样自然，尽量口语化。\n"
            "2. 每次回复控制在 1 到 3 句，短小精悍。\n"
            "3. 语气轻松，像朋友聊天。\n"
            "4. 不要长篇大论，不要讲大道理。\n"
            "5. 用户简单问候时，一句话简短回复即可。\n"
            "6. 用户提问时，直接回答重点。"
        ),
    }
]


def clean_text_for_tts(text: str) -> str:
    text = re.sub(r"（.*?）", "", text)
    text = re.sub(r"\(.*?\)", "", text)
    text = re.sub(r"[^0-9a-zA-Z\u4e00-\u9fa5，。！？；：、“”‘’《》…\-\s]", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


async def tts_edge_to_file(text: str, out_path: Path) -> None:
    cleaned = clean_text_for_tts(text)
    print(f"[TTS] 清理后文本: '{cleaned}' (长度: {len(cleaned)})")

    if not cleaned:
        raise RuntimeError("TTS 文本为空")

    await asyncio.to_thread(
        edge_tts.Communicate(cleaned, VOICE, rate="+10%").save_sync,
        str(out_path),
    )

    if out_path.exists() and out_path.stat().st_size > 0:
        print(f"[TTS] 成功生成: {out_path}")
        return

    if out_path.exists():
        out_path.unlink(missing_ok=True)
    raise RuntimeError("TTS 生成失败：音频文件为空")


async def generate_llm_reply(user_text: str) -> str:
    global messages

    messages.append({"role": "user", "content": user_text})
    try:
        completion = client.chat.completions.create(
            extra_headers={
                "HTTP-Referer": HTTP_REFERER,
                "X-Title": SITE_TITLE,
            },
            model=LLM_MODEL,
            messages=messages,
            timeout=15,
        )
        reply = completion.choices[0].message.content or ""
        reply = reply.strip()
        if not reply:
            raise RuntimeError("LLM 返回了空内容")
        print("[AI] 模型回复:\n", reply)
    except Exception as exc:
        print(f"[LLM] 请求超时或失败：{exc}")
        reply = "抱歉，我刚才没听清，你能再说一遍吗？"

    messages.append({"role": "assistant", "content": reply})
    return reply


async def handle_user_text(websocket, text: str) -> None:
    user_text = text.strip()
    if not user_text:
        return

    print("[用户] 前端发来的文本：", user_text)
    model_reply = await generate_llm_reply(user_text)

    ts = int(time.time() * 1000)
    audio_filename = f"reply_{ts}.mp3"
    audio_abs = AUDIO_DIR / audio_filename
    audio_rel = f"media/audio/{audio_filename}"

    print("[调试] 将要写入音频文件：", audio_abs)
    print("[调试] 语音模式：仅生成音频，不生成视频")

    tts_success = False
    try:
        await asyncio.wait_for(tts_edge_to_file(model_reply, audio_abs), timeout=20)
        print("[TTS] 语音生成成功")
        tts_success = True
    except asyncio.TimeoutError:
        print("[TTS] 语音生成超时")
        if audio_abs.exists() and audio_abs.stat().st_size > 0:
            print(f"[TTS] 检测到超时后文件已生成，继续使用：{audio_abs}")
            tts_success = True
    except Exception as exc:
        print(f"[TTS] 语音生成过程出错：{exc}")
        if audio_abs.exists() and audio_abs.stat().st_size > 0:
            print(f"[TTS] 检测到音频文件已生成，继续使用：{audio_abs}")
            tts_success = True
        else:
            audio_abs.unlink(missing_ok=True)

    print("[调试] exists(audio_abs)?", audio_abs.exists())
    print(f"[调试] TTS状态: {'成功' if tts_success else '失败'}")

    reply_msg = {
        "type": "bot_reply",
        "text": model_reply,
        "mode": "audio_only",
        "audio": audio_rel if tts_success else None,
    }
    if not tts_success:
        reply_msg["audio_error"] = "tts_failed"

    await websocket.send(json.dumps(reply_msg, ensure_ascii=False))


async def handle_emotion_frame(websocket, session_id: str, image_base64: str) -> None:
    if not image_base64:
        return

    try:
        result = await emotion_worker.analyze_frame(image_base64, session_id=session_id)
    except Exception as exc:
        print(f"[情绪] 实时分析失败：{exc}")
        await websocket.send(
            json.dumps(
                {
                    "type": "emotion_error",
                    "error": str(exc),
                },
                ensure_ascii=False,
            )
        )
        return

    if result.get("error"):
        await websocket.send(
            json.dumps(
                {
                    "type": "emotion_error",
                    "error": str(result["error"]),
                },
                ensure_ascii=False,
            )
        )
        return

    payload = {
        "type": "emotion_result",
        "has_face": bool(result.get("has_face")),
        "region": result.get("region"),
        "scores": result.get("scores"),
        "dominant_emotion": result.get("dominant_emotion"),
        "confidence": result.get("confidence"),
        "backend": result.get("backend"),
        "quality_reason": result.get("quality_reason"),
        "brightness": result.get("brightness"),
        "sharpness": result.get("sharpness"),
    }
    await websocket.send(json.dumps(payload, ensure_ascii=False))


async def signaling_handler(websocket) -> None:
    session_id = uuid.uuid4().hex
    print(f"[前端] 有前端连接：session={session_id[:8]}")
    try:
        async for message in websocket:
            try:
                data = json.loads(message)
            except json.JSONDecodeError:
                print("收到非 JSON 消息：", message)
                continue

            msg_type = data.get("type")
            if msg_type == "join":
                role = data.get("role", "?")
                print("前端加入，role =", role)
                await websocket.send(
                    json.dumps(
                        {
                            "type": "server_reply",
                            "text": "信令服务器已收到 join，DeepSeek / TTS 语音模式已接管。",
                            "mode": "audio_only",
                        },
                        ensure_ascii=False,
                    )
                )
            elif msg_type == "user_text":
                await handle_user_text(websocket, str(data.get("text", "")))
            elif msg_type == "emotion_frame":
                await handle_emotion_frame(
                    websocket,
                    session_id=session_id,
                    image_base64=str(data.get("image", "")),
                )
            elif msg_type == "frontend_log":
                level = str(data.get("level", "log")).upper()
                text = str(data.get("text", "")).strip()
                if text:
                    print(f"[前端控制台] [{level}] {text}")
            else:
                print("收到未知类型消息：", data)
    except websockets.ConnectionClosed:
        print(f"[前端] 连接关闭：session={session_id[:8]}")


async def main() -> None:
    print("==================================================")
    print("WebSocket 服务正在启动，监听 ws://localhost:9998")
    print("语音模式：仅生成音频，不生成视频，情绪识别按需启用")
    print("[路径调试] BASE_DIR         =", BASE_DIR)
    print("[路径调试] FRONTEND_WEB_DIR =", FRONTEND_WEB_DIR)
    print("[路径调试] AUDIO_DIR        =", AUDIO_DIR)
    print("[路径调试] VIDEO_DIR        =", VIDEO_DIR)
    print("==================================================")

    try:
        async with websockets.serve(signaling_handler, "localhost", 9998, max_size=2_000_000):
            print("WebSocket 服务已启动，等待前端连接...")
            await asyncio.Future()
    finally:
        await emotion_worker.stop()


if __name__ == "__main__":
    asyncio.run(main())
