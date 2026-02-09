import os
import sys
import asyncio
import json
import time
import re
import unicodedata
import subprocess

import websockets
from openai import OpenAI
import edge_tts

# ========= 1. OpenRouter / DeepSeek 配置 =========
API_KEY = os.getenv("OPENROUTER_API_KEY")
if not API_KEY:
    raise RuntimeError("没有读取到 OPENROUTER_API_KEY，请先在环境变量中设置。")

client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=API_KEY,
)

LLM_MODEL = "tngtech/deepseek-r1t2-chimera:free"

# ========= 2. 路径配置：媒体写到 WebStorm 前端项目 =========

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ⛏⛏⛏ 只需要确认这一行：必须是 index.html 所在的目录 ⛏⛏⛏
FRONTEND_WEB_DIR = r"E:\PycharmDemo\digital-human-web\web"

AUDIO_DIR = os.path.join(FRONTEND_WEB_DIR, "media", "audio")
VIDEO_DIR = os.path.join(FRONTEND_WEB_DIR, "media", "video")
os.makedirs(AUDIO_DIR, exist_ok=True)
os.makedirs(VIDEO_DIR, exist_ok=True)

print("[路径调试] BASE_DIR         =", BASE_DIR)
print("[路径调试] FRONTEND_WEB_DIR =", FRONTEND_WEB_DIR)
print("[路径调试] AUDIO_DIR        =", AUDIO_DIR)
print("[路径调试] VIDEO_DIR        =", VIDEO_DIR)

# ========= 3. Wav2Lip 配置 =========

WAV2LIP_DIR = os.path.join(BASE_DIR, "Wav2Lip")
W2L_SCRIPT = os.path.join(WAV2LIP_DIR, "inference.py")
W2L_CHECKPOINT = os.path.join(WAV2LIP_DIR, "checkpoints", "wav2lip_gan.pth")
FACE_IMG = os.path.join(WAV2LIP_DIR, "inputs", "face.png")

# ========= 4. TTS 配置 =========

VOICE = "zh-CN-XiaoxiaoNeural"


def clean_text_for_tts(text: str) -> str:
    text = re.sub(r"（.*?）", "", text)
    text = re.sub(r"\(.*?\)", "", text)

    def _strip_symbol_other(s: str) -> str:
        return "".join(ch for ch in s if unicodedata.category(ch) != "So")

    text = _strip_symbol_other(text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


async def tts_edge_to_file(text: str, out_path: str):
    cleaned = clean_text_for_tts(text)
    if not cleaned:
        raise RuntimeError("TTS 文本为空，跳过生成。")

    tts = edge_tts.Communicate(cleaned, VOICE)
    await tts.save(out_path)
    print(f"[TTS] ✅ 语音已生成：{out_path}")
    print("[TTS] exists(audio)?", os.path.exists(out_path))


async def run_wav2lip(audio_path: str, out_video_path: str):
    cmd = [
        sys.executable,
        W2L_SCRIPT,
        "--checkpoint_path", W2L_CHECKPOINT,
        "--face", FACE_IMG,
        "--audio", audio_path,
        "--outfile", out_video_path,
    ]
    print(f"[Wav2Lip] 开始生成嘴型视频：{out_video_path}")
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=WAV2LIP_DIR,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    stdout, _ = await proc.communicate()
    log_text = stdout.decode("utf-8", errors="ignore")
    print(log_text)
    if proc.returncode != 0:
        raise RuntimeError(f"Wav2Lip 推理失败，returncode={proc.returncode}")
    print(f"[Wav2Lip] ✅ 视频已生成：{out_video_path}")
    print("[Wav2Lip] exists(video)?", os.path.exists(out_video_path))


# ========= 5. LLM 对话上下文 =========

messages = [
    {
        "role": "system",
        "content": (
            "你是一个温柔、有共情力的中文情感数字人，正在用语音和用户聊天。\n"
            "说话风格要求：\n"
            "1. 用口语化的中文，像跟好朋友语音聊天，多用“我”和“你”。\n"
            "2. 简单打招呼时可以只用 1～2 句轻松问候，例如“你好呀，今天发生什么有趣的事了吗？”。\n"
            "3. 当用户聊到情绪、烦恼、需要分析时，可以稍微多说一些，最多 4～6 句，注意先共情再给建议。\n"
            "4. 不要用“第一、第二、第三”这种列表，也不要写太多严肃步骤。\n"
            "5. 语气自然，有人味，可以带一点幽默，但不要否定或忽视用户感受。"
        ),
    }
]


async def handle_user_text(websocket, text: str):
    global messages

    user_text = text.strip()
    if not user_text:
        return

    print("🧑 前端发来的文本：", user_text)
    messages.append({"role": "user", "content": user_text})

    # ===== 1) 调 LLM =====
    completion = client.chat.completions.create(
        extra_headers={
            "HTTP-Referer": "https://your-site-url.com",
            "X-Title": "Your Site Name",
        },
        extra_body={},
        model=LLM_MODEL,
        messages=messages,
    )

    model_reply = completion.choices[0].message.content
    print("🤖 模型回复：\n", model_reply)
    messages.append({"role": "assistant", "content": model_reply})

    # ===== 2) 生成文件名 & 路径 =====
    ts = int(time.time() * 1000)
    audio_filename = f"reply_{ts}.mp3"
    video_filename = f"reply_{ts}.mp4"

    audio_abs = os.path.join(AUDIO_DIR, audio_filename)
    video_abs = os.path.join(VIDEO_DIR, video_filename)

    # ⛏ 注意：这里给前端的是“相对 index.html 的路径”
    audio_rel = f"media/audio/{audio_filename}"
    video_rel = f"media/video/{video_filename}"

    print("[调试] 将要写入音频文件：", audio_abs)
    print("[调试] 将要写入视频文件：", video_abs)

    # ===== 3) TTS =====
    await tts_edge_to_file(model_reply, audio_abs)

    # ===== 4) Wav2Lip =====
    try:
        await run_wav2lip(audio_abs, video_abs)
    except Exception as e:
        print("[Wav2Lip] ❌ 生成视频失败：", e)
        video_rel = None

    # 再确认一次文件确实在磁盘上
    print("[调试] exists(audio_abs)?", os.path.exists(audio_abs))
    print("[调试] exists(video_abs)?", os.path.exists(video_abs))

    # ===== 5) 通知前端 =====
    reply_msg = {
        "type": "bot_reply",
        "text": model_reply,
        "audio": audio_rel,
        "video": video_rel,
    }
    await websocket.send(json.dumps(reply_msg, ensure_ascii=False))


# ========= 6. WebSocket =========

async def signaling_handler(websocket):
    print("🔥 有前端连上来了")
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
                await websocket.send(json.dumps({
                    "type": "server_reply",
                    "text": "信令服务器已收到 join，DeepSeek / TTS / Wav2Lip 已接管。",
                }, ensure_ascii=False))

            elif msg_type == "user_text":
                text = data.get("text", "")
                try:
                    await handle_user_text(websocket, text)
                except Exception as e:
                    print("处理 user_text 出错：", e)
                    err_msg = {
                        "type": "bot_error",
                        "error": str(e),
                    }
                    await websocket.send(json.dumps(err_msg, ensure_ascii=False))
            else:
                print("收到未知类型消息：", data)

    except websockets.ConnectionClosed:
        print("前端连接关闭了")


async def main():
    print("WebSocket 服务正在启动，监听 ws://localhost:9999 ...")
    async with websockets.serve(signaling_handler, "localhost", 9999):
        print("WebSocket 服务已启动，等待前端连接...")
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())
