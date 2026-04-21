const chatWindow = document.getElementById("chatWindow");
const conversationSelect = document.getElementById("conversationSelect");
const newConversationBtn = document.getElementById("newConversationBtn");
const userTextInput = document.getElementById("userText");
const sendBtn = document.getElementById("sendBtn");
const voiceBtn = document.getElementById("voiceBtn");
const webSearchToggle = document.getElementById("webSearchToggle");
const webSearchLabel = document.getElementById("webSearchLabel");
const avatarVideo = document.getElementById("avatarVideo");
const replyAudio = document.getElementById("replyAudio");
const statusText = document.getElementById("statusText");
const logoutBtn = document.getElementById("logoutBtn");
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
// 情绪面板需要实时感，主程序按约 5 帧/秒发送摄像头画面给后端分析。
// 这里不能无限调低，否则会占用过多 CPU，并影响语音/聊天链路。
const EMOTION_CAPTURE_INTERVAL_MS = 180;
const EMOTION_REQUEST_TIMEOUT_MS = 6000;
const CHAT_HISTORY_PREFIX = "digital_human_chat_history_v1";
const CHAT_CONVERSATION_PREFIX = "digital_human_conversations_v1";
const ACTIVE_CONVERSATION_PREFIX = "digital_human_active_conversation_v1";
const ADMIN_DELETE_ALL_PREFIX = "digital_human_admin_delete_all_v1";
const ADMIN_DELETE_USER_PREFIX = "digital_human_admin_delete_user_v1";
const LEGACY_CONVERSATION_ID = "legacy";
const MAX_CHAT_HISTORY_ITEMS = 80;
const DEFAULT_CONVERSATION_TITLE = "默认对话";

let conversationState = "idle";
let recognition = null;
let recognizing = false;
let callActive = false;
let buttonLocked = false;
let socket = null;
let activeConversationId = "";
let activeConversationIsDraft = false;
let adminDeletePollTimer = null;
let adminDeletedNoticeTimer = null;

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

function updateWebSearchLabel() {
    if (!webSearchToggle || !webSearchLabel) return;
    webSearchLabel.textContent = webSearchToggle.checked ? "联网：开" : "联网：关";
}

function getCurrentSession() {
    return window.DigitalHumanAuth?.session || {
        username: "guest",
        role: "guest",
    };
}

function getCurrentUsername() {
    return String(getCurrentSession().username || "guest");
}

function getUserStorageScope() {
    const session = getCurrentSession();
    const username = String(session.username || "guest");
    const role = String(session.role || "user");
    return `${role}_${username}`;
}

function getConversationMetaKey() {
    return `${CHAT_CONVERSATION_PREFIX}_${getUserStorageScope()}`;
}

function getActiveConversationKey() {
    return `${ACTIVE_CONVERSATION_PREFIX}_${getUserStorageScope()}`;
}

function getLegacyChatHistoryKey() {
    return `${CHAT_HISTORY_PREFIX}_${getUserStorageScope()}`;
}

function getChatHistoryKey(conversationId = activeConversationId) {
    const safeConversationId = conversationId || "default";
    return `${CHAT_HISTORY_PREFIX}_${getUserStorageScope()}_${safeConversationId}`;
}

function generateConversationId() {
    return `conv_${Date.now()}_${Math.random().toString(16).slice(2, 8)}`;
}

function readConversations() {
    try {
        const raw = localStorage.getItem(getConversationMetaKey());
        const parsed = raw ? JSON.parse(raw) : [];
        return Array.isArray(parsed) ? parsed : [];
    } catch (_error) {
        return [];
    }
}

function saveConversations(conversations) {
    localStorage.setItem(getConversationMetaKey(), JSON.stringify(conversations));
}

function readLegacyChatHistory() {
    try {
        const raw = localStorage.getItem(getLegacyChatHistoryKey());
        const parsed = raw ? JSON.parse(raw) : [];
        return Array.isArray(parsed) ? parsed : [];
    } catch (_error) {
        return [];
    }
}

function ensureConversationState() {
    let conversations = readConversations();

    if (conversations.length === 0) {
        const legacyHistory = readLegacyChatHistory();
        if (legacyHistory.length > 0) {
            const firstId = LEGACY_CONVERSATION_ID;
            conversations = [{
                id: firstId,
                title: DEFAULT_CONVERSATION_TITLE,
                createdAt: Date.now(),
                updatedAt: Date.now(),
            }];
            saveConversations(conversations);
            saveChatHistory(legacyHistory, firstId);
        }
    }

    const savedActiveId = localStorage.getItem(getActiveConversationKey());
    const matched = conversations.find((item) => item.id === savedActiveId);
    if (conversations.length === 0) {
        activeConversationId = "";
        activeConversationIsDraft = true;
    } else {
        activeConversationId = matched ? matched.id : conversations[0].id;
        activeConversationIsDraft = false;
        localStorage.setItem(getActiveConversationKey(), activeConversationId);
    }
    repairConversationTitles();
    renderConversationSelect();
}

function renderConversationSelect() {
    if (!conversationSelect) return;
    const conversations = readConversations();
    conversationSelect.innerHTML = "";
    if (activeConversationIsDraft) {
        const draftOption = document.createElement("option");
        draftOption.value = "__draft__";
        draftOption.textContent = "新对话（未保存）";
        draftOption.selected = true;
        conversationSelect.appendChild(draftOption);
    }
    conversations.forEach((conversation) => {
        const option = document.createElement("option");
        option.value = conversation.id;
        option.textContent = conversation.title || DEFAULT_CONVERSATION_TITLE;
        option.selected = conversation.id === activeConversationId;
        conversationSelect.appendChild(option);
    });
}

function setActiveConversation(conversationId) {
    if (conversationId === "__draft__") return;
    const conversations = readConversations();
    const matched = conversations.find((item) => item.id === conversationId);
    if (!matched) return;
    activeConversationId = matched.id;
    activeConversationIsDraft = false;
    localStorage.setItem(getActiveConversationKey(), activeConversationId);
    renderConversationSelect();
    loadChatHistory();
    requestConversationHistory(activeConversationId);
    updateStatus(`已切换到：${matched.title || DEFAULT_CONVERSATION_TITLE}`);
}

function createNewConversation() {
    activeConversationId = generateConversationId();
    activeConversationIsDraft = true;
    renderConversationSelect();
    chatWindow.innerHTML = "";
    updateStatus("已进入新对话，发送第一句话后才会保存");
}

function ensureActiveConversationSaved(firstUserText) {
    if (!activeConversationIsDraft && activeConversationId) return;

    activeConversationId = activeConversationId || generateConversationId();
    const conversations = readConversations();
    const conversation = {
        id: activeConversationId,
        title: createConversationTitle(firstUserText),
        createdAt: Date.now(),
        updatedAt: Date.now(),
    };
    conversations.unshift(conversation);
    saveConversations(conversations);
    localStorage.setItem(getActiveConversationKey(), activeConversationId);
    activeConversationIsDraft = false;
    renderConversationSelect();
    syncConversationTitleToBackend(conversation);
}

function createConversationTitle(userText) {
    const normalized = userText
        .replace(/[？?！!。.,，、；;：:]/g, " ")
        .replace(/\s+/g, " ")
        .trim();
    const cleaned = normalized
        .replace(/^(请问|请你|帮我|帮忙|我想|我需要|你能不能|能不能|可以不可以|可以帮我|请帮我|麻烦你)/, "")
        .trim();
    const titleSource = cleaned || normalized || "新对话";
    return titleSource.slice(0, 18);
}

function isPlaceholderConversationTitle(title) {
    const value = String(title || "").trim();
    return (
        !value ||
        value === "新对话" ||
        value === DEFAULT_CONVERSATION_TITLE ||
        value === "未命名对话" ||
        value.startsWith("conv_")
    );
}

function inferTitleFromHistory(conversationId) {
    const history = readChatHistory(conversationId);
    const firstUserMessage = history.find((item) => item && item.role === "user" && item.text);
    return firstUserMessage ? createConversationTitle(firstUserMessage.text) : "";
}

function repairConversationTitles() {
    const conversations = readConversations();
    let changed = false;
    conversations.forEach((conversation) => {
        if (!isPlaceholderConversationTitle(conversation.title)) return;
        const inferredTitle = inferTitleFromHistory(conversation.id);
        if (!inferredTitle) return;
        conversation.title = inferredTitle;
        conversation.updatedAt = Date.now();
        syncConversationTitleToBackend(conversation);
        changed = true;
    });
    if (changed) {
        saveConversations(conversations);
    }
}

function updateActiveConversationTitle(userText) {
    const conversations = readConversations();
    const index = conversations.findIndex((item) => item.id === activeConversationId);
    if (index < 0) return;

    const currentTitle = conversations[index].title || "";
    if (!isPlaceholderConversationTitle(currentTitle)) {
        conversations[index].updatedAt = Date.now();
        saveConversations(conversations);
        renderConversationSelect();
        return;
    }

    const title = createConversationTitle(userText);
    conversations[index].title = title;
    conversations[index].updatedAt = Date.now();
    saveConversations(conversations);
    renderConversationSelect();
    syncConversationTitleToBackend(conversations[index]);
}

function showAdminDeletedNotice(message) {
    let notice = document.getElementById("adminDeletedNotice");
    if (!notice) {
        notice = document.createElement("div");
        notice.id = "adminDeletedNotice";
        notice.style.position = "fixed";
        notice.style.inset = "0";
        notice.style.zIndex = "99999";
        notice.style.display = "flex";
        notice.style.alignItems = "center";
        notice.style.justifyContent = "center";
        notice.style.padding = "28px";
        notice.style.background = "rgba(8, 12, 20, 0.72)";
        notice.style.backdropFilter = "blur(6px)";
        notice.style.textAlign = "center";
        notice.style.pointerEvents = "auto";

        const card = document.createElement("div");
        card.style.width = "min(620px, 92vw)";
        card.style.padding = "34px 38px";
        card.style.borderRadius = "24px";
        card.style.background = "linear-gradient(145deg, rgba(18, 28, 44, 0.96), rgba(38, 52, 76, 0.92))";
        card.style.border = "1px solid rgba(255,255,255,0.18)";
        card.style.boxShadow = "0 28px 80px rgba(0,0,0,0.45)";

        const title = document.createElement("div");
        title.id = "adminDeletedNoticeTitle";
        title.style.color = "#ffffff";
        title.style.fontSize = "42px";
        title.style.fontWeight = "800";
        title.style.letterSpacing = "1px";
        title.style.textShadow = "0 8px 30px rgba(0,0,0,0.45)";

        const desc = document.createElement("div");
        desc.textContent = "当前对话已不可继续，请开启一段新的对话。";
        desc.style.marginTop = "14px";
        desc.style.color = "rgba(255,255,255,0.76)";
        desc.style.fontSize = "18px";
        desc.style.fontWeight = "500";

        const button = document.createElement("button");
        button.type = "button";
        button.textContent = "开启新对话";
        button.style.marginTop = "28px";
        button.style.padding = "14px 30px";
        button.style.border = "0";
        button.style.borderRadius = "999px";
        button.style.background = "linear-gradient(135deg, #2ee6a6, #39bdf8)";
        button.style.color = "#06101a";
        button.style.fontSize = "18px";
        button.style.fontWeight = "800";
        button.style.cursor = "pointer";
        button.style.boxShadow = "0 16px 36px rgba(45, 213, 188, 0.3)";
        button.addEventListener("click", () => {
            hideAdminDeletedNotice();
            createNewConversation();
            userTextInput?.focus();
        });

        card.appendChild(title);
        card.appendChild(desc);
        card.appendChild(button);
        notice.appendChild(card);
        document.body.appendChild(notice);
    }
    const title = document.getElementById("adminDeletedNoticeTitle");
    if (title) {
        title.textContent = message;
    }
    notice.style.display = "flex";
    notice.style.opacity = "1";

    if (adminDeletedNoticeTimer) {
        clearTimeout(adminDeletedNoticeTimer);
        adminDeletedNoticeTimer = null;
    }
}

function hideAdminDeletedNotice() {
    const notice = document.getElementById("adminDeletedNotice");
    if (!notice) return;
    notice.style.opacity = "0";
    notice.style.display = "none";
}

function syncConversationTitleToBackend(conversation) {
    if (!socket || socket.readyState !== WebSocket.OPEN || !conversation) return;
    try {
        socket.send(JSON.stringify({
            type: "conversation_title_sync",
            username: getCurrentUsername(),
            conversation_id: conversation.id,
            title: conversation.title || "未命名对话",
        }));
    } catch (_error) {
        // 标题同步失败不影响正常聊天。
    }
}

function collectLocalConversationsForBackend() {
    return readConversations().map((conversation) => ({
        id: conversation.id,
        title: conversation.title || DEFAULT_CONVERSATION_TITLE,
        createdAt: conversation.createdAt || Date.now(),
        updatedAt: conversation.updatedAt || Date.now(),
        messages: readChatHistory(conversation.id).map((item) => ({
            role: item.role,
            text: item.text,
            time: item.time || Date.now(),
        })),
    }));
}

function syncLocalConversationsToBackend() {
    if (!socket || socket.readyState !== WebSocket.OPEN) return;
    const conversations = collectLocalConversationsForBackend();
    if (conversations.length === 0) return;
    socket.send(JSON.stringify({
        type: "client_conversations_sync",
        username: getCurrentUsername(),
        conversations,
    }));
}

function requestConversationList() {
    if (!socket || socket.readyState !== WebSocket.OPEN) return;
    socket.send(JSON.stringify({
        type: "conversation_list_request",
        username: getCurrentUsername(),
    }));
}

function requestConversationHistory(conversationId = activeConversationId) {
    if (!socket || socket.readyState !== WebSocket.OPEN) return;
    if (!conversationId || activeConversationIsDraft) return;
    socket.send(JSON.stringify({
        type: "conversation_history_request",
        username: getCurrentUsername(),
        conversation_id: conversationId,
    }));
}

function applyBackendConversationList(conversations) {
    if (!Array.isArray(conversations)) return;
    const backendConversations = conversations.map((conversation) => ({
        id: String(conversation.id || conversation.conversation_id || ""),
        title: conversation.title || DEFAULT_CONVERSATION_TITLE,
        createdAt: conversation.createdAt || Date.now(),
        updatedAt: conversation.updatedAt || Date.now(),
    })).filter((conversation) => conversation.id);

    // SQLite 是主数据源。只有后端返回非空列表时才覆盖本地缓存；
    // 如果后端临时返回空，则保留本地，避免页面闪空。
    if (backendConversations.length === 0) {
        renderConversationSelect();
        loadChatHistory();
        return;
    }

    const merged = backendConversations
        .sort((a, b) => (b.updatedAt || 0) - (a.updatedAt || 0));

    saveConversations(merged);

    if (!activeConversationIsDraft) {
        const stillExists = merged.some((conversation) => conversation.id === activeConversationId);
        if (!stillExists) {
            if (merged.length > 0) {
                activeConversationId = merged[0].id;
                activeConversationIsDraft = false;
                localStorage.setItem(getActiveConversationKey(), activeConversationId);
            } else {
                activeConversationId = "";
                activeConversationIsDraft = true;
                localStorage.removeItem(getActiveConversationKey());
            }
        }
    }

    renderConversationSelect();
    loadChatHistory();
    requestConversationHistory(activeConversationId);
}

function applyBackendConversationHistory(data) {
    const conversationId = data.conversation_id || activeConversationId;
    if (!conversationId || !Array.isArray(data.messages)) return;
    const backendMessages = data.messages.map((item) => ({
        role: item.role === "assistant" ? "bot" : item.role,
        text: item.text || item.content || "",
        time: item.time || Date.now(),
    })).filter((item) => item.role && item.text);

    // 同理：后端空历史不能覆盖本地非空历史，避免刚迁移/刚启动时记录被清空。
    const localMessages = readChatHistory(conversationId);
    if (backendMessages.length === 0 && localMessages.length > 0) {
        return;
    }

    // 本地刚发出的第一句话，可能比后端历史响应更早出现。
    // 因此这里做“合并”，不能用后端历史直接覆盖本地历史。
    const mergedMessages = localMessages.length > 0
        ? mergeChatHistories(localMessages, backendMessages)
        : backendMessages;

    saveChatHistory(mergedMessages, conversationId);
    if (conversationId === activeConversationId) {
        loadChatHistory();
    }
}

function mergeChatHistories(localMessages, backendMessages) {
    const merged = [...localMessages];
    const seen = new Set(localMessages.map((item) => `${item.role}\u0000${item.text}`));
    backendMessages.forEach((item) => {
        const key = `${item.role}\u0000${item.text}`;
        if (seen.has(key)) return;
        seen.add(key);
        merged.push(item);
    });
    return merged.slice(-MAX_CHAT_HISTORY_ITEMS);
}

function getAdminDeleteAllKey() {
    return `${ADMIN_DELETE_ALL_PREFIX}:${getCurrentUsername()}`;
}

function getAdminDeleteUserKey() {
    return `${ADMIN_DELETE_USER_PREFIX}:${getCurrentUsername()}`;
}

function readChatHistory(conversationId = activeConversationId) {
    if (!conversationId) return [];
    try {
        const raw = localStorage.getItem(getChatHistoryKey(conversationId));
        const parsed = raw ? JSON.parse(raw) : [];
        return Array.isArray(parsed) ? parsed : [];
    } catch (_error) {
        return [];
    }
}

function saveChatHistory(history, conversationId = activeConversationId) {
    if (!conversationId) return;
    const trimmed = history.slice(-MAX_CHAT_HISTORY_ITEMS);
    localStorage.setItem(getChatHistoryKey(conversationId), JSON.stringify(trimmed));
}

function deleteLocalConversation(conversationId) {
    const existingConversations = readConversations();
    if (conversationId === LEGACY_CONVERSATION_ID) {
        localStorage.removeItem(getLegacyChatHistoryKey());
        const hasStableLegacyConversation = existingConversations.some((item) => item.id === LEGACY_CONVERSATION_ID);
        if (!hasStableLegacyConversation && existingConversations.length === 1) {
            clearLocalConversationsForCurrentUser();
            return;
        }
    }

    const conversations = existingConversations.filter((item) => item.id !== conversationId);
    saveConversations(conversations);
    localStorage.removeItem(getChatHistoryKey(conversationId));

    if (activeConversationId === conversationId) {
        if (conversations.length > 0) {
            activeConversationId = conversations[0].id;
            activeConversationIsDraft = false;
            localStorage.setItem(getActiveConversationKey(), activeConversationId);
        } else {
            activeConversationId = "";
            activeConversationIsDraft = true;
            localStorage.removeItem(getActiveConversationKey());
        }
    }
}

function clearLocalConversationsForCurrentUser() {
    const conversations = readConversations();
    conversations.forEach((conversation) => {
        localStorage.removeItem(getChatHistoryKey(conversation.id));
    });
    localStorage.removeItem(getConversationMetaKey());
    localStorage.removeItem(getActiveConversationKey());
    activeConversationId = "";
    activeConversationIsDraft = true;
}

function pruneLocalConversationsByBackend(existingIds) {
    if (!Array.isArray(existingIds)) return false;
    if (existingIds.length === 0) return false;

    const validIds = new Set(existingIds.map((item) => String(item)));
    const conversations = readConversations();
    if (conversations.length === 0) return false;

    const removedConversations = conversations.filter((item) => !validIds.has(item.id));
    if (removedConversations.length === 0) return false;

    const keptConversations = conversations.filter((item) => validIds.has(item.id));
    removedConversations.forEach((conversation) => {
        localStorage.removeItem(getChatHistoryKey(conversation.id));
        if (conversation.id === LEGACY_CONVERSATION_ID) {
            localStorage.removeItem(getLegacyChatHistoryKey());
        }
    });

    saveConversations(keptConversations);

    if (!keptConversations.some((item) => item.id === activeConversationId)) {
        if (keptConversations.length > 0) {
            activeConversationId = keptConversations[0].id;
            activeConversationIsDraft = false;
            localStorage.setItem(getActiveConversationKey(), activeConversationId);
        } else {
            activeConversationId = "";
            activeConversationIsDraft = true;
            localStorage.removeItem(getActiveConversationKey());
        }
    }

    return true;
}

function applyAdminDeleteState(data) {
    const deleteAllAt = data.delete_all_at ? String(data.delete_all_at) : "";
    if (deleteAllAt && localStorage.getItem(getAdminDeleteAllKey()) !== deleteAllAt) {
        clearLocalConversationsForCurrentUser();
        localStorage.setItem(getAdminDeleteAllKey(), deleteAllAt);
        renderConversationSelect();
        loadChatHistory();
        updateStatus("后台已一键删除全部数据，当前进入未保存的新对话");
        return;
    }

    const userDeletedMarker = data.user_deleted_at
        ? String(data.user_deleted_at)
        : (data.user_deleted ? "legacy" : "");
    if (userDeletedMarker && localStorage.getItem(getAdminDeleteUserKey()) !== userDeletedMarker) {
        clearLocalConversationsForCurrentUser();
        localStorage.setItem(getAdminDeleteUserKey(), userDeletedMarker);
        renderConversationSelect();
        loadChatHistory();
        updateStatus("后台已删除该用户数据，当前进入未保存的新对话");
        return;
    }

    const pruned = pruneLocalConversationsByBackend(data.existing_conversation_ids);
    if (pruned) {
        renderConversationSelect();
        loadChatHistory();
        updateStatus("已清理本地残留的旧对话");
    }

    const deletedIds = Array.isArray(data.deleted_conversation_ids) ? data.deleted_conversation_ids : [];
    if (deletedIds.length === 0) return;
    const activeConversationWasDeleted = deletedIds.includes(activeConversationId);
    if (activeConversationWasDeleted) {
        showAdminDeletedNotice("该对话已被管理删除");
    }
    deletedIds.forEach(deleteLocalConversation);
    renderConversationSelect();
    loadChatHistory();
    updateStatus("已同步后台删除的对话");
}

function requestAdminDeleteState() {
    if (!socket || socket.readyState !== WebSocket.OPEN) return;
    const session = getCurrentSession();
    socket.send(JSON.stringify({
        type: "admin_delete_state_request",
        username: session.username || "guest",
    }));
}

function persistMessage(role, text) {
    persistMessageToConversation(role, text, activeConversationId);
}

function persistMessageToConversation(role, text, conversationId) {
    const history = readChatHistory(conversationId);
    history.push({
        role,
        text,
        time: Date.now(),
    });
    saveChatHistory(history, conversationId);
}

function loadChatHistory() {
    chatWindow.innerHTML = "";
    const history = readChatHistory();
    history.forEach((item) => {
        if (!item || !item.role || !item.text) return;
        appendMessage(item.role, item.text, {persist: false});
    });
}

function appendMessage(role, text, options = {}) {
    const shouldPersist = options.persist !== false;
    const div = document.createElement("div");
    div.className = `msg ${role}`;
    div.textContent = `${role === "user" ? "你" : "数字人"}：${text}`;
    chatWindow.appendChild(div);
    chatWindow.scrollTop = chatWindow.scrollHeight;
    if (shouldPersist && (role === "user" || role === "bot")) {
        persistMessage(role, text);
    }
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
        const session = getCurrentSession();
        socket.send(JSON.stringify({
            type: "join",
            role: "web_client",
            username: session.username || "guest",
            user_role: session.role || "user",
            conversation_id: activeConversationId,
        }));
        // SQLite 现在是唯一主数据源，不再自动把浏览器旧缓存导入后端。
        // 否则旧 localStorage 里的历史对话会被误认为是真实新对话。
        requestConversationList();
        requestConversationHistory(activeConversationId);
        requestAdminDeleteState();
        if (adminDeletePollTimer) {
            clearInterval(adminDeletePollTimer);
        }
        // 启动器的后台删除是写本地文件；前端定时询问后端，才能在不刷新页面时同步删除结果。
        adminDeletePollTimer = setInterval(requestAdminDeleteState, 1500);
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

        if (data.type === "client_conversations_synced") {
            console.log("[前端] 本地对话已同步到后端，导入数：", data.imported);
            return;
        }

        if (data.type === "conversation_list") {
            applyBackendConversationList(data.conversations);
            return;
        }

        if (data.type === "conversation_history") {
            applyBackendConversationHistory(data);
            return;
        }

        if (data.type === "bot_reply") {
            const text = data.text || "";
            const audioPath = data.audio;
            const replyConversationId = data.conversation_id || activeConversationId;
            console.log("[前端] 收到AI回复：", text);
            if (replyConversationId === activeConversationId) {
                appendMessage("bot", text);
            } else {
                persistMessageToConversation("bot", text, replyConversationId);
            }
            requestConversationList();

            if (audioPath && replyConversationId === activeConversationId) {
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

        if (data.type === "admin_delete_state") {
            applyAdminDeleteState(data);
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
        if (adminDeletePollTimer) {
            clearInterval(adminDeletePollTimer);
            adminDeletePollTimer = null;
        }
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
    ensureActiveConversationSaved(text);
    appendMessage("user", text);
    updateActiveConversationTitle(text);
    userTextInput.value = "";

    if (socket && socket.readyState === WebSocket.OPEN) {
        const webSearchEnabled = Boolean(webSearchToggle?.checked);
        socket.send(JSON.stringify({
            type: "user_text",
            text,
            username: getCurrentUsername(),
            user_role: getCurrentSession().role || "user",
            conversation_id: activeConversationId,
            web_search: webSearchEnabled,
        }));
        updateStatus(webSearchEnabled ? "AI 正在联网搜索..." : "AI 正在思考...");
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

if (conversationSelect) {
    conversationSelect.addEventListener("change", () => {
        setActiveConversation(conversationSelect.value);
    });
}

if (newConversationBtn) {
    newConversationBtn.addEventListener("click", createNewConversation);
}

if (webSearchToggle) {
    webSearchToggle.addEventListener("change", updateWebSearchLabel);
}

if (logoutBtn) {
    logoutBtn.addEventListener("click", (event) => {
        event.preventDefault();
        if (window.DigitalHumanAuth) {
            window.DigitalHumanAuth.logout();
        } else {
            window.location.href = "login.html";
        }
    });
}

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
    updateWebSearchLabel();
    ensureConversationState();
    loadChatHistory();
    initSpeechRecognition();
    updateStatus(`${getCurrentUsername()}，可打字发送，或点击语音开始通话`);
    connectWebSocket();
    await startEmotionCamera();
});

window.addEventListener("beforeunload", () => {
    stopEmotionCamera();
    if (adminDeletePollTimer) {
        clearInterval(adminDeletePollTimer);
        adminDeletePollTimer = null;
    }
    if (socket && socket.readyState === WebSocket.OPEN) {
        socket.close();
    }
});
