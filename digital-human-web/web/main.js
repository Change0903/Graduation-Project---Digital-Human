const chatWindow = document.getElementById("chatWindow");
const userTextInput = document.getElementById("userText");
const sendBtn = document.getElementById("sendBtn");
const voiceBtn = document.getElementById("voiceBtn");
const avatarVideo = document.getElementById("avatarVideo");
const replyAudio = document.getElementById("replyAudio");
const statusText = document.getElementById("statusText");
const emotionVideo = document.getElementById("emotionVideo");
const emotionStatus = document.getElementById("emotionStatus");
const emotionBadge = document.getElementById("emotionBadge");
const emotionSummary = document.getElementById("emotionSummary");
const emotionBars = document.getElementById("emotionBars");

const EMOTION_LABELS = {
    happy: "开心",
    neutral: "平静",
    surprise: "惊讶",
    sad: "悲伤",
    angry: "愤怒",
    fear: "害怕",
    disgust: "厌恶",
};
const EMOTION_ORDER = ["happy", "neutral", "surprise", "sad", "angry", "fear", "disgust"];
const EMOTION_CAPTURE_INTERVAL_MS = 450;
const EMOTION_REQUEST_TIMEOUT_MS = 6000;

let conversationState = "idle";
let recognition = null;
let recognizing = false;
let callActive = false;
let buttonLocked = false;
let socket = null;

let emotionStream = null;
let emotionCaptureTimer = null;
let emotionRequestInFlight = false;
let emotionLastRequestAt = 0;
const emotionCanvas = document.createElement("canvas");
const emotionContext = emotionCanvas.getContext("2d");
const emotionBarRefs = new Map();

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
    } catch (_error) {
        return String(arg);
    }
}

function queueFrontendLog(level, args) {
    const payload = {
        type: "frontend_log",
        level,
        text: args.map(stringifyLogArg).join(" "),
    };
    if (!payload.text.trim()) return;

    if (socket && socket.readyState === WebSocket.OPEN) {
        try {
            socket.send(JSON.stringify(payload));
            return;
        } catch (_error) {
            // 发送失败时进入队列，等待连接恢复
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

function updateStatus(message) {
    statusText.textContent = message;
}

function appendMessage(role, text) {
    const div = document.createElement("div");
    div.className = `msg ${role}`;
    div.textContent = `${role === "user" ? "你" : "数字人"}：${text}`;
    chatWindow.appendChild(div);
    chatWindow.scrollTop = chatWindow.scrollHeight;
}

function createEmotionRows() {
    emotionBars.innerHTML = "";
    EMOTION_ORDER.forEach((emotionKey) => {
        const row = document.createElement("div");
        row.className = "emotion-row";

        const label = document.createElement("div");
        label.className = "emotion-label";
        label.textContent = EMOTION_LABELS[emotionKey];

        const track = document.createElement("div");
        track.className = "emotion-track";

        const fill = document.createElement("div");
        fill.className = "emotion-fill";
        track.appendChild(fill);

        const value = document.createElement("div");
        value.className = "emotion-value";
        value.textContent = "0.0%";

        row.appendChild(label);
        row.appendChild(track);
        row.appendChild(value);
        emotionBars.appendChild(row);

        emotionBarRefs.set(emotionKey, {fill, value});
    });
}

function getQualityText(reason) {
    const qualityTextMap = {
        ok: "画面稳定",
        too_dark: "画面偏暗",
        too_bright: "画面偏亮",
        too_blurry: "画面偏模糊",
        no_face: "未检测到人脸",
        waiting: "等待识别",
    };
    return qualityTextMap[reason] || "识别中";
}

function updateEmotionStatus(message) {
    emotionStatus.textContent = message;
}

function resetEmotionPanel(message = "等待识别") {
    emotionBadge.textContent = message;
    emotionSummary.textContent = `当前主情绪：${message}`;
    EMOTION_ORDER.forEach((emotionKey) => {
        const refs = emotionBarRefs.get(emotionKey);
        if (!refs) return;
        refs.fill.style.width = "0%";
        refs.value.textContent = "0.0%";
    });
}

function renderEmotionResult(data) {
    emotionRequestInFlight = false;
    const scores = data.scores || {};
    const hasFace = Boolean(data.has_face);

    if (!hasFace) {
        updateEmotionStatus("未检测到稳定人脸，请保持正对摄像头");
        resetEmotionPanel("未检测到人脸");
        return;
    }

    const dominant = data.dominant_emotion || "neutral";
    const dominantLabel = EMOTION_LABELS[dominant] || dominant;
    emotionBadge.textContent = `主情绪：${dominantLabel}`;
    emotionSummary.textContent = `当前主情绪：${dominantLabel}`;

    const qualityText = getQualityText(data.quality_reason);
    const brightness = Number(data.brightness || 0).toFixed(0);
    const sharpness = Number(data.sharpness || 0).toFixed(0);
    updateEmotionStatus(`${qualityText} | 亮度 ${brightness} | 清晰度 ${sharpness}`);

    EMOTION_ORDER.forEach((emotionKey) => {
        const refs = emotionBarRefs.get(emotionKey);
        if (!refs) return;
        const rawValue = Number(scores[emotionKey] || 0);
        const width = Math.max(0, Math.min(rawValue, 100));
        refs.fill.style.width = `${width}%`;
        refs.value.textContent = `${rawValue.toFixed(1)}%`;
    });
}

async function startEmotionCamera() {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
        updateEmotionStatus("当前浏览器不支持摄像头调用");
        resetEmotionPanel("浏览器不支持");
        return;
    }

    try {
        emotionStream = await navigator.mediaDevices.getUserMedia({
            video: {
                facingMode: "user",
                width: {ideal: 640},
                height: {ideal: 480},
            },
            audio: false,
        });
        emotionVideo.srcObject = emotionStream;
        await emotionVideo.play();
        updateEmotionStatus("摄像头已开启，正在分析中...");
        console.log("[前端] 情绪识别摄像头已开启");

        if (emotionCaptureTimer) {
            clearInterval(emotionCaptureTimer);
        }
        emotionCaptureTimer = window.setInterval(captureEmotionFrame, EMOTION_CAPTURE_INTERVAL_MS);
    } catch (error) {
        console.error("[前端] 摄像头启动失败：", error);
        updateEmotionStatus("摄像头启动失败，请检查浏览器权限");
        resetEmotionPanel("摄像头未授权");
    }
}

function stopEmotionCamera() {
    if (emotionCaptureTimer) {
        clearInterval(emotionCaptureTimer);
        emotionCaptureTimer = null;
    }
    if (emotionStream) {
        emotionStream.getTracks().forEach((track) => track.stop());
        emotionStream = null;
    }
}

function captureEmotionFrame() {
    if (!socket || socket.readyState !== WebSocket.OPEN) return;
    if (!emotionStream || document.hidden) return;
    if (!emotionVideo.videoWidth || !emotionVideo.videoHeight) return;

    if (emotionRequestInFlight) {
        if (Date.now() - emotionLastRequestAt > EMOTION_REQUEST_TIMEOUT_MS) {
            emotionRequestInFlight = false;
            updateEmotionStatus("情绪识别请求超时，已自动重试");
        } else {
            return;
        }
    }

    const targetWidth = 320;
    const targetHeight = Math.round((emotionVideo.videoHeight / emotionVideo.videoWidth) * targetWidth);
    emotionCanvas.width = targetWidth;
    emotionCanvas.height = targetHeight;
    emotionContext.drawImage(emotionVideo, 0, 0, targetWidth, targetHeight);

    const dataUrl = emotionCanvas.toDataURL("image/jpeg", 0.72);
    const imageBase64 = dataUrl.split(",")[1];
    if (!imageBase64) return;

    emotionRequestInFlight = true;
    emotionLastRequestAt = Date.now();
    socket.send(JSON.stringify({
        type: "emotion_frame",
        image: imageBase64,
    }));
}

function connectWebSocket() {
    const wsUrl = "ws://localhost:9998";
    socket = new WebSocket(wsUrl);

    socket.onopen = () => {
        console.log("[前端] WebSocket 已连接");
        socket.send(JSON.stringify({
            type: "join",
            role: "web_client",
        }));
        flushFrontendLogs();
    };

    socket.onmessage = (event) => {
        let data;
        try {
            data = JSON.parse(event.data);
        } catch (_error) {
            console.log("[前端] 收到非 JSON 消息：", event.data);
            return;
        }

        if (data.type === "server_reply") {
            console.log("[前端] 服务器回复：", data.text);
            return;
        }

        if (data.type === "bot_reply") {
            const text = data.text || "";
            const audioPath = data.audio;
            console.log("[前端] 收到AI回复：", text);
            appendMessage("bot", text);

            if (audioPath) {
                playAiAudio(audioPath);
            } else {
                console.log("[前端] 本次回复没有音频，跳过播放");
                conversationState = "idle";
                updateStatus("收到文本回复（本次无音频）");
                if (callActive) {
                    setTimeout(startNextListeningCycle, 500);
                }
            }
            return;
        }

        if (data.type === "emotion_result") {
            renderEmotionResult(data);
            return;
        }

        if (data.type === "emotion_error") {
            emotionRequestInFlight = false;
            console.error("[前端] 情绪识别失败：", data.error);
            updateEmotionStatus(`情绪识别失败：${data.error}`);
            return;
        }
    };

    socket.onclose = () => {
        console.log("[前端] WebSocket 已关闭");
        emotionRequestInFlight = false;
        updateStatus("连接已断开");
        updateEmotionStatus("后端连接已断开，情绪识别暂停");
    };

    socket.onerror = (error) => {
        console.error("[前端] WebSocket 出错：", error);
        updateStatus("连接出错");
    };
}

function playAiAudio(audioPath) {
    console.log("[前端] 开始播放AI音频：", audioPath);
    updateStatus("AI正在说话...");
    conversationState = "ai_speaking";
    recognizing = false;

    replyAudio.src = audioPath;
    replyAudio.play().catch((error) => {
        console.error("[前端] 音频播放失败：", error);
        conversationState = "idle";
        if (callActive) {
            startNextListeningCycle();
        } else {
            updateStatus("播放失败，请重试发送");
        }
    });

    replyAudio.onended = () => {
        console.log("[前端] AI音频播放完毕");
        if (callActive) {
            console.log("[前端] 通话中，自动开启下一轮对话");
            conversationState = "idle";
            setTimeout(startNextListeningCycle, 1000);
        } else {
            conversationState = "idle";
            updateStatus("回复结束，可继续打字或点击语音");
        }
    };
}

function startNextListeningCycle() {
    if (!callActive) {
        console.log("[前端] 通话已挂断，停止自动循环");
        return;
    }
    if (conversationState !== "idle") {
        console.log("[前端] 当前状态不适合启动ASR：", conversationState);
        return;
    }
    if (recognizing) {
        console.log("[前端] ASR 正在运行中，稍后重试");
        setTimeout(() => {
            if (callActive && conversationState === "idle") {
                startNextListeningCycle();
            }
        }, 500);
        return;
    }

    console.log("[前端] 自动启动语音识别（通话中）");
    conversationState = "listening";
    updateStatus("通话中，请开始说话...");
    recognizing = true;

    try {
        recognition.start();
    } catch (error) {
        console.error("[前端] 自动启动 ASR 失败：", error);
        recognizing = false;
        conversationState = "idle";
        setTimeout(() => {
            if (callActive && conversationState === "idle" && !recognizing) {
                startNextListeningCycle();
            }
        }, 1000);
    }
}

function hangUpCall() {
    console.log("[前端] 挂断通话");
    callActive = false;

    if (recognizing && recognition) {
        try {
            recognition.stop();
        } catch (error) {
            console.log("[前端] 停止 ASR 时出现异常：", error);
        }
        recognizing = false;
    }

    conversationState = "idle";
    voiceBtn.textContent = "说话";
    updateStatus("通话已挂断");
}

function initSpeechRecognition() {
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SpeechRecognition) {
        console.log("[前端] 当前浏览器不支持 Web Speech API 语音识别");
        voiceBtn.disabled = true;
        voiceBtn.textContent = "语音不可用";
        return;
    }

    recognition = new SpeechRecognition();
    recognition.lang = "zh-CN";
    recognition.continuous = false;
    recognition.interimResults = false;

    recognition.onstart = () => {
        recognizing = true;
        conversationState = "listening";
        console.log("[前端] 开始语音识别");
        voiceBtn.textContent = callActive ? "挂断" : "监听中";
        updateStatus(callActive ? "通话中，请开始说话..." : "正在听你说话...");
    };

    recognition.onresult = (event) => {
        const transcript = event.results?.[0]?.[0]?.transcript;
        if (!transcript) return;
        console.log("[前端] 识别结果：", transcript);
        userTextInput.value = transcript;
        sendUserText();
    };

    recognition.onerror = (event) => {
        console.log("[前端] 语音识别错误：", event.error);
        recognizing = false;

        if (callActive) {
            conversationState = "idle";
            setTimeout(() => {
                if (callActive && !recognizing) {
                    console.log("[前端] 通话中，自动重试 ASR");
                    startNextListeningCycle();
                }
            }, 1000);
            return;
        }

        conversationState = "idle";
        voiceBtn.textContent = "说话";
        updateStatus("语音识别出错，请重试");
    };

    recognition.onend = () => {
        console.log("[前端] 语音识别结束");
        recognizing = false;

        if (callActive && conversationState === "listening") {
            conversationState = "idle";
            setTimeout(() => {
                if (callActive && !recognizing && conversationState === "idle") {
                    console.log("[前端] 通话中，自动重启 ASR");
                    startNextListeningCycle();
                }
            }, 500);
            return;
        }

        if (!callActive) {
            voiceBtn.textContent = "说话";
            conversationState = "idle";
        }
    };
}

function sendUserText() {
    const text = userTextInput.value.trim();
    if (!text) return;

    console.log("[前端] 发送用户消息：", text);
    appendMessage("user", text);
    userTextInput.value = "";

    if (socket && socket.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify({
            type: "user_text",
            text,
        }));
        updateStatus("AI 正在思考...");
        conversationState = "ai_thinking";
        return;
    }

    appendMessage("bot", "WebSocket 未连接，无法发送消息。");
    conversationState = "idle";
}

voiceBtn.addEventListener("click", () => {
    if (buttonLocked) {
        console.log("[前端] 按钮已锁定，请稍后再试");
        return;
    }
    if (!recognition) {
        console.log("[前端] 语音识别未初始化");
        return;
    }

    if (!callActive) {
        buttonLocked = true;
        console.log("[前端] 开始通话");

        if (recognizing && recognition) {
            try {
                recognition.stop();
            } catch (_error) {
                // 忽略残留状态
            }
        }

        recognizing = false;
        conversationState = "idle";
        callActive = true;
        voiceBtn.textContent = "挂断";
        updateStatus("正在接通...");

        setTimeout(() => {
            if (callActive) {
                startNextListeningCycle();
            }
            buttonLocked = false;
        }, 500);
        return;
    }

    buttonLocked = true;
    console.log("[前端] 用户点击挂断");
    hangUpCall();
    setTimeout(() => {
        buttonLocked = false;
    }, 1000);
});

sendBtn.addEventListener("click", sendUserText);

userTextInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
        event.preventDefault();
        sendUserText();
    }
});

window.addEventListener("load", async () => {
    console.log("[前端] 页面加载完成");
    avatarVideo.poster = "avatar.png";
    createEmotionRows();
    resetEmotionPanel();
    initSpeechRecognition();
    updateStatus("可打字发送，或点击语音开始通话");
    connectWebSocket();
    await startEmotionCamera();
});

window.addEventListener("beforeunload", () => {
    stopEmotionCamera();
    if (socket && socket.readyState === WebSocket.OPEN) {
        socket.close();
    }
});
