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
from storage import DigitalHumanStorage
from storage import normalize_conversation_id
from storage import normalize_user_id
from storage import split_memory_key


def resolve_llm_api_key() -> tuple[str, str]:
    """优先读取 API_KEY_ALI，兼容旧环境变量。"""
    for env_name in ("API_KEY_ALI", "ANTHROPIC_AUTH_TOKEN", "DASHSCOPE_API_KEY", "OPENROUTER_API_KEY"):
        api_key = os.getenv(env_name)
        if api_key:
            return api_key, env_name
    raise RuntimeError(
        "没有读取到 LLM API Key，请先设置 API_KEY_ALI。"
    )


API_KEY, API_KEY_SOURCE = resolve_llm_api_key()

BASE_DIR = Path(__file__).resolve().parent
CONFIGURED_FRONTEND_WEB_DIR = Path(r"E:\PycharmDemo\Deepseek\digital-human-web\web")
FALLBACK_FRONTEND_WEB_DIR = BASE_DIR / "digital-human-web" / "web"
VOICE = "zh-CN-XiaoxiaoNeural"
LLM_MODEL = os.getenv("DASHSCOPE_MODEL", "qwen3.6-flash")
LLM_BASE_URL = os.getenv(
    "DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
)
WEB_SEARCH_STRATEGY = os.getenv("DASHSCOPE_SEARCH_STRATEGY", "turbo")


def resolve_frontend_web_dir() -> Path:
    if CONFIGURED_FRONTEND_WEB_DIR.exists():
        return CONFIGURED_FRONTEND_WEB_DIR
    return FALLBACK_FRONTEND_WEB_DIR


FRONTEND_WEB_DIR = resolve_frontend_web_dir()
AUDIO_DIR = FRONTEND_WEB_DIR / "media" / "audio"
VIDEO_DIR = FRONTEND_WEB_DIR / "media" / "video"
MEMORY_DIR = BASE_DIR / "runtime"
MEMORY_FILE = MEMORY_DIR / "user_memory.json"
USER_REGISTRY_FILE = MEMORY_DIR / "user_registry.json"
ADMIN_DELETE_STATE_FILE = MEMORY_DIR / "admin_delete_state.json"
SQLITE_FILE = MEMORY_DIR / "digital_human.db"
MAX_MEMORY_MESSAGES = 30
AUDIO_DIR.mkdir(parents=True, exist_ok=True)
VIDEO_DIR.mkdir(parents=True, exist_ok=True)
MEMORY_DIR.mkdir(parents=True, exist_ok=True)

client = OpenAI(
    base_url=LLM_BASE_URL,
    api_key=API_KEY,
)

print(f"[LLM] 当前 API Key 来源: {API_KEY_SOURCE}")

emotion_worker = EmotionWorkerClient(BASE_DIR)
storage = DigitalHumanStorage(SQLITE_FILE)

SYSTEM_MESSAGE = {
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
def clean_text_for_tts(text: str) -> str:
    text = re.sub(r"（.*?）", "", text)
    text = re.sub(r"\(.*?\)", "", text)
    text = re.sub(r"[^0-9a-zA-Z\u4e00-\u9fa5，。！？；：、“”‘’《》…\-\s]", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def build_memory_key(user_id: str, conversation_id: str) -> str:
    return f"{normalize_user_id(user_id)}__conv__{normalize_conversation_id(conversation_id)}"


def load_user_memories() -> dict[str, list[dict[str, str]]]:
    if not MEMORY_FILE.exists():
        return {}
    try:
        raw = json.loads(MEMORY_FILE.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"[记忆] 读取记忆文件失败，已忽略：{exc}")
        return {}

    if not isinstance(raw, dict):
        return {}

    cleaned: dict[str, list[dict[str, str]]] = {}
    for user_id, history in raw.items():
        if not isinstance(history, list):
            continue
        safe_history = []
        for item in history:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role", ""))
            content = str(item.get("content", "")).strip()
            if role in {"user", "assistant"} and content:
                safe_history.append({"role": role, "content": content})
        cleaned[str(user_id)] = safe_history[-MAX_MEMORY_MESSAGES:]
    return cleaned


def save_user_memories() -> None:
    try:
        MEMORY_FILE.write_text(
            json.dumps(user_memories, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as exc:
        print(f"[记忆] 保存记忆文件失败：{exc}")


def load_user_registry() -> dict[str, dict]:
    if not USER_REGISTRY_FILE.exists():
        return {}
    try:
        raw = json.loads(USER_REGISTRY_FILE.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"[用户] 读取用户登记文件失败，已忽略：{exc}")
        return {}
    return raw if isinstance(raw, dict) else {}


def save_user_registry() -> None:
    try:
        USER_REGISTRY_FILE.write_text(
            json.dumps(user_registry, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as exc:
        print(f"[用户] 保存用户登记文件失败：{exc}")


def load_admin_delete_state() -> dict:
    if not ADMIN_DELETE_STATE_FILE.exists():
        return {}
    try:
        raw = json.loads(ADMIN_DELETE_STATE_FILE.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"[后台] 读取删除状态失败，已忽略：{exc}")
        return {}
    return raw if isinstance(raw, dict) else {}


def sync_user_registry(username: str, role: str = "user") -> str:
    return storage.sync_user(username, role)


def sync_conversation_title(username: str, conversation_id: str, title: str) -> None:
    storage.upsert_conversation(username, conversation_id, title)


def list_existing_conversation_ids(username: str) -> list[str]:
    return storage.list_existing_conversation_ids(username)


def build_llm_messages(memory_key: str, user_text: str) -> list[dict[str, str]]:
    user_id, conversation_id = split_memory_key(memory_key)
    history = storage.get_llm_history(user_id, conversation_id, MAX_MEMORY_MESSAGES)
    return [SYSTEM_MESSAGE, *history, {"role": "user", "content": user_text}]


def remember_turn(memory_key: str, user_text: str, assistant_text: str) -> None:
    user_id, conversation_id = split_memory_key(memory_key)
    storage.append_turn(user_id, conversation_id, user_text, assistant_text)


storage.migrate_legacy_json(USER_REGISTRY_FILE, MEMORY_FILE)
storage_stats = storage.stats()
print(
    f"[SQLite] 已加载 用户={storage_stats['users']} "
    f"对话={storage_stats['conversations']} 消息={storage_stats['messages']}"
)


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


async def handle_user_text(
    websocket,
    text: str,
    user_id: str,
    conversation_id: str,
    web_search: bool = False,
) -> None:
    user_text = text.strip()
    if not user_text:
        return

    memory_key = build_memory_key(user_id, conversation_id)
    print(f"[用户:{user_id}][对话:{conversation_id}] 前端发来的文本：", user_text)
    print(f"[LLM] 联网模式: {'开启' if web_search else '关闭'}")
    model_reply = await generate_llm_reply(user_text, memory_key, web_search=web_search)

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
        "conversation_id": conversation_id,
    }
    if not tts_success:
        reply_msg["audio_error"] = "tts_failed"

    await websocket.send(json.dumps(reply_msg, ensure_ascii=False))


async def generate_llm_reply(user_text: str, memory_key: str, web_search: bool = False) -> str:
    try:
        completion = request_llm_completion(memory_key, user_text, web_search=web_search)
        reply = completion.choices[0].message.content or ""
        reply = reply.strip()
        if not reply:
            raise RuntimeError("LLM 返回了空内容")
        print("[AI] 模型回复:\n", reply)
    except Exception as exc:
        if web_search:
            print(f"[LLM] 联网请求失败，尝试降级普通模式：{exc}")
            try:
                completion = request_llm_completion(memory_key, user_text, web_search=False)
                plain_reply = (completion.choices[0].message.content or "").strip()
                if not plain_reply:
                    raise RuntimeError("降级普通模式返回了空内容")
                reply = f"我刚才联网搜索失败了，先按普通模式回答：{plain_reply}"
                print("[AI] 降级普通模式回复:\n", reply)
            except Exception as fallback_exc:
                print(f"[LLM] 普通模式也失败：{fallback_exc}")
                reply = "抱歉，联网搜索刚才失败了，你可以稍后再试，或者先关闭联网模式继续聊天。"
        else:
            print(f"[LLM] 请求超时或失败：{exc}")
            reply = "抱歉，我刚才没听清，你能再说一遍吗？"

    remember_turn(memory_key, user_text, reply)
    return reply


def request_llm_completion(memory_key: str, user_text: str, web_search: bool = False):
    extra_body = {"enable_thinking": False}
    timeout = 15
    if web_search:
        # 百炼 OpenAI 兼容接口通过 enable_search 开启联网搜索。
        # forced_search=True 表示用户勾选联网模式时尽量强制搜索。
        extra_body.update(
            {
                "enable_search": True,
                "search_options": {
                    "forced_search": True,
                    "search_strategy": WEB_SEARCH_STRATEGY,
                },
            }
        )
        timeout = 30

    return client.chat.completions.create(
        model=LLM_MODEL,
        messages=build_llm_messages(memory_key, user_text),
        extra_body=extra_body,
        timeout=timeout,
    )


async def handle_emotion_frame(websocket, session_id: str, image_base64: str) -> None:
    if not image_base64:
        return

    try:
        result = await emotion_worker.analyze_frame(image_base64, session_id=session_id)
    except Exception as exc:
        error_text = str(exc).strip() or repr(exc)
        print(f"[情绪] 实时分析失败：{type(exc).__name__}: {error_text}")
        await websocket.send(
            json.dumps(
                {
                    "type": "emotion_error",
                    "error": error_text,
                },
                ensure_ascii=False,
            )
        )
        return

    if result.get("error"):
        print(f"[情绪] worker 返回错误：{result['error']}")
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
    session_user_id = "guest"
    session_conversation_id = "default"
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
                user_role = str(data.get("user_role", "user"))
                session_user_id = normalize_user_id(str(data.get("username", "guest")))
                session_conversation_id = normalize_conversation_id(data.get("conversation_id"))
                sync_user_registry(session_user_id, user_role)
                print(
                    f"前端加入，role={role}, user={session_user_id}, "
                    f"user_role={user_role}, conversation={session_conversation_id}"
                )
                await websocket.send(
                    json.dumps(
                        {
                            "type": "server_reply",
                            "text": (
                                f"信令服务器已收到 join，当前用户 {session_user_id} "
                                f"的对话 {session_conversation_id} 已接管。"
                            ),
                            "mode": "audio_only",
                            "username": session_user_id,
                            "conversation_id": session_conversation_id,
                        },
                        ensure_ascii=False,
                    )
                )
            elif msg_type == "user_text":
                request_user_id = normalize_user_id(data.get("username") or session_user_id)
                current_role = storage.get_user_role(request_user_id)
                sync_user_registry(request_user_id, str(data.get("user_role") or current_role))
                request_conversation_id = normalize_conversation_id(
                    data.get("conversation_id") or session_conversation_id
                )
                await handle_user_text(
                    websocket,
                    str(data.get("text", "")),
                    request_user_id,
                    request_conversation_id,
                    web_search=bool(data.get("web_search")),
                )
            elif msg_type == "auth_user_sync":
                username = sync_user_registry(
                    str(data.get("username", "guest")),
                    str(data.get("user_role", "user")),
                )
                await websocket.send(
                    json.dumps(
                        {
                            "type": "auth_user_synced",
                            "username": username,
                        },
                        ensure_ascii=False,
                    )
                )
            elif msg_type == "auth_register":
                username = normalize_user_id(str(data.get("username", "guest")))
                ok, reason = storage.register_user(
                    username,
                    str(data.get("password_hash", "")),
                    str(data.get("user_role", "user")),
                )
                await websocket.send(
                    json.dumps(
                        {
                            "type": "auth_result",
                            "action": "register",
                            "ok": ok,
                            "reason": reason,
                            "username": username,
                        },
                        ensure_ascii=False,
                    )
                )
            elif msg_type == "auth_login":
                username = normalize_user_id(str(data.get("username", "guest")))
                ok, reason = storage.verify_user(username, str(data.get("password_hash", "")))
                await websocket.send(
                    json.dumps(
                        {
                            "type": "auth_result",
                            "action": "login",
                            "ok": ok,
                            "reason": reason,
                            "username": username,
                        },
                        ensure_ascii=False,
                    )
                )
            elif msg_type == "conversation_title_sync":
                username = normalize_user_id(data.get("username") or session_user_id)
                conversation_id = normalize_conversation_id(data.get("conversation_id"))
                title = str(data.get("title", "未命名对话"))
                sync_conversation_title(username, conversation_id, title)
                await websocket.send(
                    json.dumps(
                        {
                            "type": "conversation_title_synced",
                            "username": username,
                            "conversation_id": conversation_id,
                        },
                        ensure_ascii=False,
                    )
                )
            elif msg_type == "client_conversations_sync":
                username = normalize_user_id(data.get("username") or session_user_id)
                conversations = data.get("conversations", [])
                # 安全策略：默认不接收浏览器 localStorage 的历史对话导入。
                # 否则浏览器残留缓存会污染 SQLite，造成后台出现多余对话。
                imported = 0
                if bool(data.get("allow_import")):
                    imported = storage.import_client_conversations(
                        username,
                        conversations if isinstance(conversations, list) else [],
                    )
                await websocket.send(
                    json.dumps(
                        {
                            "type": "client_conversations_synced",
                            "username": username,
                            "imported": imported,
                        },
                        ensure_ascii=False,
                    )
                )
            elif msg_type == "conversation_list_request":
                username = normalize_user_id(data.get("username") or session_user_id)
                await websocket.send(
                    json.dumps(
                        {
                            "type": "conversation_list",
                            "username": username,
                            "conversations": storage.list_conversations(username),
                        },
                        ensure_ascii=False,
                    )
                )
            elif msg_type == "conversation_history_request":
                username = normalize_user_id(data.get("username") or session_user_id)
                conversation_id = normalize_conversation_id(data.get("conversation_id"))
                await websocket.send(
                    json.dumps(
                        {
                            "type": "conversation_history",
                            "username": username,
                            "conversation_id": conversation_id,
                            "messages": storage.get_conversation_messages(username, conversation_id),
                        },
                        ensure_ascii=False,
                    )
                )
            elif msg_type == "admin_delete_state_request":
                username = normalize_user_id(data.get("username") or session_user_id)
                delete_state = storage.get_delete_state(username)
                await websocket.send(
                    json.dumps(
                        {
                            "type": "admin_delete_state",
                            "username": username,
                            "deleted_conversation_ids": delete_state["deleted_conversation_ids"],
                            "existing_conversation_ids": delete_state["existing_conversation_ids"],
                            "user_deleted": delete_state["user_deleted"],
                            "user_deleted_at": delete_state["user_deleted_at"],
                            "delete_all_at": delete_state.get("delete_all_at"),
                        },
                        ensure_ascii=False,
                    )
                )
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
