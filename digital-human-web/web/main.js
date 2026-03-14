// main.js - 单轮对话模式

// ===== 1. DOM 引用 =====
const chatWindow = document.getElementById("chatWindow");
const userTextInput = document.getElementById("userText");
const sendBtn = document.getElementById("sendBtn");
const voiceBtn = document.getElementById("voiceBtn");
const avatarVideo = document.getElementById("avatarVideo");
const replyAudio = document.getElementById("replyAudio");
const statusText = document.getElementById("statusText");

// ===== 2. 对话状态控制 =====
let conversationState = "idle"; // idle | listening | ai_thinking | ai_speaking
let recognition = null;
let recognizing = false;
let callActive = false; // 是否在通话中（隐藏按钮逻辑）
let buttonLocked = false; // 按钮防抖锁
let socket = null;

// ===== 2.1. 前端控制台日志上报 =====
const MAX_PENDING_FRONTEND_LOGS = 200;
const pendingFrontendLogs = [];
const rawConsole = {
    log: console.log.bind(console),
    warn: console.warn.bind(console),
    error: console.error.bind(console),
};

function stringifyLogArg(arg) {
    if (arg === null) return "null";
    if (arg === undefined) return "undefined";
    if (typeof arg === "string") return arg;
    if (arg instanceof Error) return arg.stack || arg.message || String(arg);
    try {
        return JSON.stringify(arg);
    } catch (_e) {
        return String(arg);
    }
}

function buildFrontendLogPayload(level, args) {
    return {
        type: "frontend_log",
        level: level,
        text: args.map(stringifyLogArg).join(" "),
    };
}

function queueFrontendLog(level, args) {
    const payload = buildFrontendLogPayload(level, args);
    if (!payload.text.trim()) return;

    if (socket && socket.readyState === WebSocket.OPEN) {
        try {
            socket.send(JSON.stringify(payload));
            return;
        } catch (_e) {
            // 发送失败时进入队列，等待下次连接恢复后再发
        }
    }

    if (pendingFrontendLogs.length >= MAX_PENDING_FRONTEND_LOGS) {
        pendingFrontendLogs.shift();
    }
    pendingFrontendLogs.push(payload);
}

function flushFrontendLogs() {
    if (!socket || socket.readyState !== WebSocket.OPEN) return;
    while (pendingFrontendLogs.length > 0) {
        const payload = pendingFrontendLogs.shift();
        socket.send(JSON.stringify(payload));
    }
}

console.log = (...args) => {
    rawConsole.log(...args);
    queueFrontendLog("log", args);
};
console.warn = (...args) => {
    rawConsole.warn(...args);
    queueFrontendLog("warn", args);
};
console.error = (...args) => {
    rawConsole.error(...args);
    queueFrontendLog("error", args);
};

window.addEventListener("error", (event) => {
    const detail = event.error?.stack || event.message || "unknown error";
    queueFrontendLog("error", ["[window.onerror]", detail]);
});

window.addEventListener("unhandledrejection", (event) => {
    queueFrontendLog("error", ["[unhandledrejection]", stringifyLogArg(event.reason)]);
});

// 页面加载
window.addEventListener("load", () => {
    console.log("[前端] 页面加载完成");
    avatarVideo.poster = "avatar.png";
    initSpeechRecognition();
    updateStatus("可打字发送，或点击🎤开始语音通话");
});

// ===== 3. WebSocket 连接 =====
const wsUrl = "ws://localhost:9998";
socket = new WebSocket(wsUrl);

// 连接建立
socket.onopen = () => {
    console.log("[前端] ✅ WebSocket 已连接");
    socket.send(JSON.stringify({
        type: "join",
        role: "web_client"
    }));
    flushFrontendLogs();
};

// 收到消息
socket.onmessage = (event) => {
    let data;
    try {
        data = JSON.parse(event.data);
    } catch (e) {
        console.log("[前端] 收到非 JSON 消息：", event.data);
        return;
    }

    if (data.type === "server_reply") {
        console.log("[前端] 服务器回复：", data.text);
    } else if (data.type === "bot_reply") {
        const text = data.text || "";
        const audioPath = data.audio;

        console.log("[前端] 收到AI回复：", text);

        // 添加消息到聊天窗口
        appendMessage("bot", text);

        // 开始播放AI音频
        if (audioPath) {
            playAiAudio(audioPath);
        } else {
            console.log("[前端] 本次回复没有音频，跳过播放");
            conversationState = "idle";
            updateStatus("收到文本回复（本次无音频）");
            if (callActive) {
                setTimeout(() => {
                    startNextListeningCycle();
                }, 500);
            }
        }
    }
};

// 连接关闭
socket.onclose = () => {
    console.log("[前端] 🔌 WebSocket 已关闭");
    updateStatus("连接已断开");
};

// 连接错误
socket.onerror = (err) => {
    console.log("[前端] ❌ WebSocket 出错：", err);
    updateStatus("连接出错");
};

// ===== 4. 状态更新函数 =====
function updateStatus(message) {
    statusText.textContent = message;
}

// ===== 5. 播放AI音频 =====
function playAiAudio(audioPath) {
    console.log("[前端] 🎵 开始播放AI音频：", audioPath);
    updateStatus("AI正在说话...");

    conversationState = "ai_speaking";
    recognizing = false;

    replyAudio.src = audioPath;
    replyAudio.play().catch(err => {
        console.log("[前端] 音频播放失败：", err);
        if (callActive) {
            conversationState = "idle";
            startNextListeningCycle();
        } else {
            conversationState = "idle";
            updateStatus("播放失败，请重试发送");
        }
    });

    // 音频播放结束事件
    replyAudio.onended = () => {
        console.log("[前端] ✅ AI音频播放完毕");
        if (callActive) {
            console.log("[前端] 🔄 通话中，自动开启下一轮对话");
            conversationState = "idle";
            // 延迟1秒后自动开始下一轮对话（隐藏逻辑）
            setTimeout(() => {
                startNextListeningCycle();
            }, 1000);
        } else {
            conversationState = "idle";
            updateStatus("回复结束，可继续打字或点击🎤语音");
        }
    };
}

// ===== 5.1. 自动开始下一轮对话循环 =====
function startNextListeningCycle() {
    if (!callActive) {
        console.log("[前端] ⏹️ 通话已挂断，停止自动循环");
        return;
    }

    // 确保不在AI说话或其他状态时启动
    if (conversationState !== "idle") {
        console.log("[前端] ⏳ 当前状态不适合启动ASR：", conversationState);
        return;
    }

    // 确保ASR没有在运行
    if (recognizing) {
        console.log("[前端] ⏳ ASR正在运行中，等待结束后重试");
        // 延迟重试
        setTimeout(() => {
            if (callActive && conversationState === "idle") {
                startNextListeningCycle();
            }
        }, 500);
        return;
    }

    console.log("[前端] 🎤 自动启动语音识别（通话中）");
    conversationState = "listening";
    updateStatus("通话中... 请开始说话");
    recognizing = true;

    try {
        recognition.start();
    } catch (err) {
        console.log("[前端] ❌ 自动启动ASR失败：", err);
        recognizing = false;
        conversationState = "idle";
        // 失败后延迟重试
        setTimeout(() => {
            if (callActive && conversationState === "idle" && !recognizing) {
                startNextListeningCycle();
            }
        }, 1000);
    }
}

// ===== 5.2. 挂断通话 =====
function hangUpCall() {
    console.log("[前端] 📞 挂断通话");
    callActive = false;

    // 立即停止当前ASR
    if (recognizing && recognition) {
        try {
            recognition.stop();
            recognizing = false;
        } catch (e) {
            console.log("[前端] 停止ASR时出现异常：", e);
        }
    }

    conversationState = "idle";
    voiceBtn.textContent = "🎤 语音";
    updateStatus("通话已挂断");
}

// ===== 6. 初始化语音识别 =====
function initSpeechRecognition() {
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;

    if (!SpeechRecognition) {
        console.log("[前端] 当前浏览器不支持 Web Speech API 语音识别");
        voiceBtn.disabled = true;
        voiceBtn.textContent = "语音不支持";
        return;
    }

    recognition = new SpeechRecognition();
    recognition.lang = "zh-CN";
    recognition.continuous = false;
    recognition.interimResults = false;

    recognition.onstart = () => {
        recognizing = true;
        conversationState = "listening";
        console.log("[前端] 🎤 开始语音识别");

        if (callActive) {
            voiceBtn.textContent = "📞 挂断";
            updateStatus("通话中... 请开始说话");
        } else {
            voiceBtn.textContent = "🎤 监听中...";
            updateStatus("正在听你说话...");
        }
    };

    recognition.onresult = (event) => {
        if (!event.results || !event.results[0] || !event.results[0][0]) return;

        const transcript = event.results[0][0].transcript;
        console.log("[前端] 🎤 识别结果：", transcript);

        userTextInput.value = transcript;

        // 识别完成后立即发送
        sendUserText();
    };

    recognition.onerror = (event) => {
        console.log("[前端] ❌ 语音识别错误：", event.error);
        recognizing = false;

        if (callActive) {
            // 通话中出错，延迟重试
            conversationState = "idle";
            setTimeout(() => {
                if (callActive && !recognizing) {
                    console.log("[前端] 🔄 通话中，自动重试ASR");
                    startNextListeningCycle();
                }
            }, 1000);
        } else {
            // 未通话，显示错误状态
            conversationState = "idle";
            voiceBtn.textContent = "🎤 语音";
            updateStatus("语音识别出错，请重试");
        }
    };

    recognition.onend = () => {
        console.log("[前端] 🎤 语音识别结束");
        recognizing = false;

        // 如果在通话中且不是AI说话状态，自动重试
        if (callActive && conversationState === "listening") {
            conversationState = "idle";
            setTimeout(() => {
                if (callActive && !recognizing && conversationState === "idle") {
                    console.log("[前端] 🔄 通话中，自动重启ASR");
                    startNextListeningCycle();
                }
            }, 500);
        } else if (!callActive) {
            // 未通话，恢复按钮状态
            voiceBtn.textContent = "🎤 语音";
            conversationState = "idle";
        }
    };
}

// ===== 7. 发送用户文本 =====
function sendUserText() {
    const text = userTextInput.value.trim();
    if (!text) return;

    console.log("[前端] 📤 发送用户消息：", text);

    // 添加消息到聊天窗口
    appendMessage("user", text);
    userTextInput.value = "";

    if (socket && socket.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify({
            type: "user_text",
            text: text
        }));
        updateStatus("AI正在思考...");
        conversationState = "ai_thinking";
    } else {
        appendMessage("bot", "WebSocket 未连接，无法发送消息。");
        conversationState = "idle";
    }
}

// ===== 8. 语音按钮点击事件 =====
voiceBtn.addEventListener("click", () => {
    // 防抖：防止快速点击
    if (buttonLocked) {
        console.log("[前端] ⏸️ 按钮已锁定，请稍后再试");
        return;
    }

    if (!recognition) {
        console.log("[前端] ❌ 语音识别未初始化");
        return;
    }

    if (!callActive) {
        // ===== 开始通话 =====
        buttonLocked = true; // 锁定按钮
        console.log("[前端] 📞 开始通话");

        // 先彻底停止任何残留的ASR
        if (recognizing && recognition) {
            try {
                recognition.stop();
            } catch (e) {
                // 忽略错误
            }
        }
        recognizing = false;
        conversationState = "idle";

        callActive = true;
        voiceBtn.textContent = "📞 挂断";
        updateStatus("正在接通...");

        // 自动启动第一轮对话
        setTimeout(() => {
            if (callActive) {
                startNextListeningCycle();
            }
            buttonLocked = false; // 释放按钮锁
        }, 500);
    } else {
        // ===== 挂断通话 =====
        buttonLocked = true; // 锁定按钮
        console.log("[前端] 📞 用户点击挂断");
        hangUpCall();

        // 延迟释放按钮锁
        setTimeout(() => {
            buttonLocked = false;
        }, 1000);
    }
});

// ===== 9. 发送按钮点击事件（保留备用） =====
sendBtn.addEventListener("click", () => {
    sendUserText();
});

// ===== 10. 回车发送（保留备用） =====
userTextInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
        e.preventDefault();
        sendUserText();
    }
});

// ===== 11. 添加聊天消息 =====
function appendMessage(role, text) {
    const div = document.createElement("div");
    div.className = `msg ${role}`;
    div.textContent = (role === "user" ? "你：" : "数字人：") + text;
    chatWindow.appendChild(div);
    chatWindow.scrollTop = chatWindow.scrollHeight;
}
