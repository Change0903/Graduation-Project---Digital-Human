// main-2.js - 语音通话模式

// ===== 1. DOM 引用 =====
const chatWindow = document.getElementById("chatWindow");
const userTextInput = document.getElementById("userText");
const sendBtn = document.getElementById("sendBtn");
const voiceBtn = document.getElementById("voiceBtn");
const avatarVideo = document.getElementById("avatarVideo");
const replyAudio = document.getElementById("replyAudio");
const statusText = document.getElementById("statusText");

// ===== 通话状态管理 =====
let callActive = false;
let callTimer = null;
let callStartTime = null;
let recognition = null;
let recognizing = false;

// 页面加载后，先用一张静态图片做"数字人"初始画面
window.addEventListener("load", () => {
    console.log("[前端] ✅ 页面加载完成");
    console.log("[前端] 检查DOM元素:");
    console.log("  - chatWindow:", chatWindow ? "✅" : "❌");
    console.log("  - userTextInput:", userTextInput ? "✅" : "❌");
    console.log("  - sendBtn:", sendBtn ? "✅" : "❌");
    console.log("  - voiceBtn:", voiceBtn ? "✅" : "❌");
    console.log("  - avatarVideo:", avatarVideo ? "✅" : "❌");
    console.log("  - replyAudio:", replyAudio ? "✅" : "❌");
    console.log("  - statusText:", statusText ? "✅" : "❌");

    if (!voiceBtn) {
        console.error("[前端] ❌ voiceBtn 元素未找到！");
        return;
    }

    avatarVideo.poster = "avatar.png";
    updateCallUI(false);
    console.log("[前端] ✅ 页面初始化完成");
});

// ===== 2. WebSocket 连接 =====
const wsUrl = "ws://localhost:9998";
let socket = new WebSocket(wsUrl);

// 连接建立
socket.onopen = () => {
    console.log("[前端] ✅ WebSocket 已连接");
    socket.send(JSON.stringify({
        type: "join",
        role: "web_client"
    }));
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
        const mode = data.mode || "unknown";
        if (mode === "audio_only" && callActive) {
            statusText.textContent = "正在通话中...";
        }
    } else if (data.type === "bot_reply") {
        const text = data.text || "";
        const audioPath = data.audio;

        appendMessage("bot", text);
        statusText.textContent = "正在播放语音回应...";

        // 播放音频（语音模式）- 立即关闭麦克风避免录制自己声音
        if (audioPath) {
            replyAudio.src = audioPath;

            // 立即停止语音识别（不等音频开始播放）
            if (recognition && recognizing) {
                try {
                    console.log("[音频] 立即暂停语音识别");
                    recognition.stop();
                    recognizing = false;
                    console.log("[音频] ✅ 语音识别已暂停");
                } catch (err) {
                    console.log("[音频] 停止识别失败:", err);
                }
            }

            // 音频播放结束时：重新开启语音识别
            replyAudio.onended = () => {
                console.log("[音频] 播放结束，重新开启语音识别");
                setTimeout(() => {
                    if (callActive && recognition) {
                        try {
                            recognition.start();
                            recognizing = true;
                            console.log("[音频] ✅ 语音识别已重启");
                            statusText.textContent = "请说话...";
                        } catch (err) {
                            console.log("[音频] 重启识别失败:", err);
                            statusText.textContent = "语音识别重启失败";
                        }
                    }
                }, 1000);
            };

            replyAudio.play().catch((err) => {
                console.log("音频播放被阻止/失败：", err);
                // 播放失败时也要重启识别
                if (callActive && recognition && !recognizing) {
                    try {
                        recognition.start();
                        recognizing = true;
                    } catch (err) {
                        console.log("[音频] 播放失败后重启识别失败:", err);
                    }
                }
            });
        }
    }
};

// 连接关闭
socket.onclose = () => {
    console.log("[前端] 🔌 WebSocket 已关闭");
    statusText.textContent = "连接已断开，请检查 server.py 是否在运行。";
    callActive = false;
    updateCallUI(false);
};

// 连接错误
socket.onerror = (err) => {
    console.log("[前端] ❌ WebSocket 出错：", err);
    statusText.textContent = "WebSocket 出错，请检查终端。";
};

// ===== 3. 发送用户文本 =====
function sendUserText() {
    const text = userTextInput.value.trim();
    if (!text) return;

    appendMessage("user", text);

    if (socket && socket.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify({
            type: "user_text",
            text: text
        }));
        statusText.textContent = "数字人在思考中...";
        userTextInput.value = "";
    } else {
        appendMessage("bot", "WebSocket 未连接，无法发送消息。");
        userTextInput.value = "";
    }
}

// 点击发送按钮
sendBtn.addEventListener("click", () => {
    sendUserText();
});

// 回车发送
userTextInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
        e.preventDefault();
        sendUserText();
    }
});

// ===== 4. 通话控制 =====
function toggleCall() {
    if (callActive) {
        // 挂断通话
        endCall();
    } else {
        // 开始通话
        startCall();
    }
}

function startCall() {
    console.log("[通话] 开始通话");
    callActive = true;
    callStartTime = Date.now();
    updateCallUI(true);
    startTimer();
    statusText.textContent = "正在通话中...";

    // 初始化语音识别
    console.log("[通话] 初始化语音识别");
    initSpeechRecognition();

    // 启动语音识别
    console.log("[通话] 启动语音识别");
    if (recognition) {
        try {
            recognition.start();
            console.log("[通话] ✅ 语音识别启动成功");
        } catch (err) {
            console.error("[通话] ❌ 语音识别启动失败:", err);
        }
    } else {
        console.error("[通话] ❌ recognition 为空");
    }
}

function endCall() {
    callActive = false;
    updateCallUI(false);
    stopTimer();
    statusText.textContent = "通话已结束";

    // 关闭语音识别
    if (recognition && recognizing) {
        recognition.stop();
        recognizing = false;
    }
}

function updateCallUI(active) {
    if (active) {
        voiceBtn.textContent = "挂断";
        voiceBtn.style.background = "linear-gradient(135deg, #ef4444, #dc2626)";
        chatWindow.style.display = "block";
        userTextInput.style.display = "none";
        sendBtn.style.display = "none";
    } else {
        voiceBtn.textContent = "🎤 语音";
        voiceBtn.style.background = "linear-gradient(135deg, #38bdf8, #6366f1)";
        chatWindow.style.display = "block";
        userTextInput.style.display = "block";
        sendBtn.style.display = "inline-block";
    }
}

function startTimer() {
    callTimer = setInterval(() => {
        if (callStartTime) {
            const duration = Math.floor((Date.now() - callStartTime) / 1000);
            const minutes = Math.floor(duration / 60);
            const seconds = duration % 60;
            const timerText = `${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`;
            document.querySelector('.avatar-header .label').textContent = `数字人 通话中 ${timerText}`;
        }
    }, 1000);
}

function stopTimer() {
    if (callTimer) {
        clearInterval(callTimer);
        callTimer = null;
    }
    document.querySelector('.avatar-header .label').textContent = "数字人";
}

// ===== 5. 语音识别功能 =====
function initSpeechRecognition() {
    if (recognition) {
        console.log("[识别] 语音识别已初始化");
        return; // 避免重复初始化
    }

    console.log("[识别] 检查浏览器支持");
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SpeechRecognition) {
        console.log("[前端] ❌ 当前浏览器不支持 Web Speech API 语音识别");
        statusText.textContent = "浏览器不支持语音识别";
        alert("你的浏览器不支持语音识别功能，请使用Chrome或Edge浏览器");
        return;
    }

    console.log("[识别] 创建语音识别实例");
    recognition = new SpeechRecognition();
    recognition.lang = "zh-CN";
    recognition.continuous = true;  // 改为持续识别
    recognition.interimResults = false;
    console.log("[识别] 实例创建完成，等待启动");

    recognition.onstart = () => {
        recognizing = true;
        console.log("[识别] ✅ 语音识别已启动");
        statusText.textContent = "正在听你说话...";
    };

    recognition.onaudiostart = () => {
        console.log("[识别] ✅ 正在监听麦克风");
    };

    recognition.onsoundstart = () => {
        console.log("[识别] 🔊 检测到声音");
    };

    recognition.onresult = (event) => {
        const transcript = event.results[0][0].transcript;
        console.log("[前端] 🎤 识别结果：", transcript);
        if (transcript.trim()) {
            sendUserTextFromVoice(transcript);
        }
    };

    recognition.onerror = (event) => {
        console.log("[识别] ❌ 语音识别错误：", event.error);
        recognizing = false;
        if (callActive) {
            if (event.error === 'not-allowed') {
                console.log("[识别] ❌ 麦克风权限被拒绝");
                statusText.textContent = "请允许麦克风权限并刷新页面";
                alert("请允许麦克风权限，然后刷新页面重试");
                endCall();
                return;
            }
            if (event.error === 'no-speech') {
                console.log("[识别] ⚠️ 没有检测到语音，继续监听");
                // no-speech 错误不需要重试，继续等待
                return;
            }
            statusText.textContent = "语音识别出错，正在重试...";
            setTimeout(() => {
                if (callActive && recognition) {
                    try {
                        console.log("[识别] 重试语音识别");
                        recognition.start();
                    } catch (err) {
                        console.log("重试失败：", err);
                    }
                }
            }, 1000);
        }
    };

    recognition.onend = () => {
        recognizing = false;
        console.log("[识别] 语音识别结束");
        if (callActive) {
            // 通话中自动重新开始识别 - 但要检查是否正在播放音频
            setTimeout(() => {
                if (callActive && recognition && !replyAudio.paused) {
                    // 只有在不播放音频时才重启
                    console.log("[识别] 自动重启语音识别");
                    try {
                        recognition.start();
                    } catch (err) {
                        console.log("自动重启识别失败：", err);
                    }
                } else if (callActive && recognition && replyAudio.paused) {
                    // 音频在播放，不重启
                    console.log("[识别] 音频正在播放，跳过重启");
                }
            }, 500);
        }
    };
}

function sendUserTextFromVoice(transcript) {
    if (!transcript.trim()) return;

    appendMessage("user", transcript);

    if (socket && socket.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify({
            type: "user_text",
            text: transcript
        }));
        statusText.textContent = "数字人在思考中...";
    }
}

// 点击发送按钮
sendBtn.addEventListener("click", () => {
    sendUserText();
});

// 回车发送
userTextInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
        e.preventDefault();
        sendUserText();
    }
});

// 通话按钮 - 添加调试
console.log("[绑定] 检查 voiceBtn 绑定:", voiceBtn ? "✅" : "❌");
if (voiceBtn) {
    voiceBtn.addEventListener("click", () => {
        console.log("[绑定] ✅ 语音按钮被点击了！");
        toggleCall();
    });
    console.log("[绑定] ✅ 语音按钮事件绑定成功");
} else {
    console.error("[绑定] ❌ 无法绑定语音按钮 - voiceBtn 为空");
}

// ===== 6. 辅助函数：添加聊天消息 =====
function appendMessage(role, text) {
    const div = document.createElement("div");
    div.className = `msg ${role}`;
    div.textContent = (role === "user" ? "你：" : "数字人：") + text;
    chatWindow.appendChild(div);
    chatWindow.scrollTop = chatWindow.scrollHeight;
}
