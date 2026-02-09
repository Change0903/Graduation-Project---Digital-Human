import os
import sys
import asyncio
import json
import time
import re
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

# ========= 3. TTS 配置 =========

VOICE = "zh-CN-XiaoxiaoNeural"


def clean_text_for_tts(text: str) -> str:
    # 移除括号内容
    text = re.sub(r"（.*?）", "", text)
    text = re.sub(r"\(.*?\)", "", text)

    # 极简化处理：只保留中文汉字、数字、字母和基本标点
    # 移除非ASCII字符，包括波浪号、特殊符号等
    text = re.sub(r'[^0-9a-zA-Z\u4e00-\u9fa5，。！？：；\s]', '', text)

    # 规范化空白字符
    text = re.sub(r"\s+", " ", text)
    return text.strip()


async def tts_edge_to_file(text: str, out_path: str):
    cleaned = clean_text_for_tts(text)
    print(f"[TTS] 清理后文本: '{cleaned}' (长度: {len(cleaned)})")

    if not cleaned:
        raise RuntimeError("TTS 文本为空")

    if len(cleaned) > 200:
        print(f"[TTS] ⚠️ 文本过长 ({len(cleaned)} 字符)")

    # 使用线程池执行同步的 TTS 调用 (1.1倍语速)
    await asyncio.to_thread(
        edge_tts.Communicate(cleaned, VOICE, rate="+10%").save_sync, out_path
    )

    # 验证文件
    if os.path.getsize(out_path) > 0:
        print(f"[TTS] ✅ {out_path}")
    else:
        os.remove(out_path)
        raise RuntimeError("TTS 生成失败：文件为空")


# ========= 4. LLM 对话上下文 =========

messages = [
    {
        "role": "system",
        "content": (
            "你是一个温柔、有趣的中文语音助手，正在和用户进行电话聊天。\n"
            "说话风格要求：\n"
            "1. 像微信语音聊天一样自然，用口语化的中文，多用'我'和'你'。\n"
            "2. 每次回复控制在 1～2 句话，要短小精悍，别啰嗦！\n"
            "3. 语气轻松活泼，像朋友聊天一样。\n"
            "4. 不要长篇大论，不要讲大道理，就日常聊天即可。\n"
            "5. 如果知识简单的问候，或者用户问你能不能听到这类的，你只需要一句话简单回复就好。\n"
            "6. 当用户问问题时，简单直接回答就好。"
        ),
    }
]


async def handle_user_text(websocket, text: str):
    global messages

    user_text = text.strip()
    if not user_text:
        return

    print("[用户] 前端发来的文本：", user_text)
    messages.append({"role": "user", "content": user_text})

    # ===== 1) 调 LLM =====
    model_reply = None
    try:
        completion = client.chat.completions.create(
            extra_headers={
                "HTTP-Referer": "https://your-site-url.com",
                "X-Title": "Your Site Name",
            },
            extra_body={},
            model=LLM_MODEL,
            messages=messages,
            timeout=15,  # 15秒超时
        )
        model_reply = completion.choices[0].message.content
        print("[AI] 模型回复：\n", model_reply)
        messages.append({"role": "assistant", "content": model_reply})
    except Exception as e:
        print(f"[LLM] 请求超时或失败：{e}")
        model_reply = "抱歉，我刚才没听清，你能再说一遍吗？"
        messages.append({"role": "assistant", "content": model_reply})

    # ===== 2) 生成文件名 & 路径 =====
    ts = int(time.time() * 1000)
    audio_filename = f"reply_{ts}.mp3"

    audio_abs = os.path.join(AUDIO_DIR, audio_filename)

    # ⛏ 注意：这里给前端的是"相对 index.html 的路径"
    audio_rel = f"media/audio/{audio_filename}"

    print("[调试] 将要写入音频文件：", audio_abs)
    print("[调试] 语音模式：仅生成音频，不生成视频")

    # ===== 3) TTS =====
    tts_success = False
    try:
        await asyncio.wait_for(tts_edge_to_file(model_reply, audio_abs), timeout=20)  # 20秒超时
        print("[TTS] ✅ 语音生成成功")
        tts_success = True
    except asyncio.TimeoutError:
        print("[TTS] ❌ 语音生成超时")
        # 超时后也要检查文件是否已生成
        if os.path.exists(audio_abs):
            file_size = os.path.getsize(audio_abs)
            if file_size > 0:
                print(f"[TTS] ✅ 音频文件已生成 (大小: {file_size} bytes)，继续使用")
                tts_success = True
            else:
                print("[TTS] 音频文件为空，跳过TTS")
                audio_rel = None
        else:
            print("[TTS] 跳过TTS，继续使用文本模式")
            audio_rel = None
    except Exception as e:
        print(f"[TTS] 语音生成过程出错：{e}")
        # Edge TTS的bug：即使报错，文件也可能已生成
        if os.path.exists(audio_abs):
            file_size = os.path.getsize(audio_abs)
            if file_size > 0:
                print(f"[TTS] ✅ 检测到音频文件已生成 (大小: {file_size} bytes)，继续使用")
                tts_success = True
            else:
                print("[TTS] 音频文件大小为0，跳过TTS")
                audio_rel = None
                # 删除空文件
                try:
                    os.remove(audio_abs)
                except:
                    pass
        else:
            print("[TTS] 跳过TTS，继续使用文本模式")
            audio_rel = None

    # 再确认一次文件确实在磁盘上
    print("[调试] exists(audio_abs)?", os.path.exists(audio_abs))
    print(f"[调试] TTS状态: {'成功' if tts_success else '跳过'}")

    # ===== 4) 通知前端 =====
    reply_msg = {
        "type": "bot_reply",
        "text": model_reply,
        "mode": "audio_only",
    }

    # 即使TTS失败，也返回音频路径（前端可以处理404）
    # 如果TTS成功，使用真实文件路径
    # 如果TTS失败，使用默认音频路径
    if tts_success and audio_rel:
        reply_msg["audio"] = audio_rel
    else:
        # TTS失败时，返回一个占位符音频路径
        # 这样前端不会因为缺少audio字段而出错
        reply_msg["audio"] = "media/audio/silent.mp3"

    await websocket.send(json.dumps(reply_msg, ensure_ascii=False))


# ========= 5. WebSocket =========

async def signaling_handler(websocket):
    print("[前端] 有前端连上来了")
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
                    "text": "信令服务器已收到 join，DeepSeek / TTS 语音模式已接管。",
                    "mode": "audio_only",
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
    print("==================================================")
    print("WebSocket 服务正在启动，监听 ws://localhost:9998")
    print("语音模式：只生成音频，不生成视频，速度更快！")
    print("==================================================")
    async with websockets.serve(signaling_handler, "localhost", 9998):
        print("WebSocket 服务已启动，等待前端连接...")
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())
