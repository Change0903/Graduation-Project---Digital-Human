const USER_STORE_KEY = "digital_human_users_v1";
const SESSION_KEY = "digital_human_session_v1";
const DEVELOPER_PASSWORD = "111111";

const tabButtons = document.querySelectorAll(".tab-btn");
const panels = document.querySelectorAll(".auth-panel");
const messageBox = document.getElementById("messageBox");

const loginForm = document.getElementById("loginForm");
const registerForm = document.getElementById("registerForm");
const developerForm = document.getElementById("developerForm");

let captchaAnswer = 0;

function getNextPage() {
    const params = new URLSearchParams(window.location.search);
    const next = params.get("next") || "index-2.html";
    return next.includes("://") ? "index-2.html" : next;
}

function showMessage(text, type = "info") {
    messageBox.textContent = text;
    messageBox.className = `message-box ${type}`;
}

function switchPanel(target) {
    tabButtons.forEach((button) => {
        button.classList.toggle("active", button.dataset.target === target);
    });
    panels.forEach((panel) => {
        panel.classList.toggle("active", panel.id === target);
    });
    refreshCaptcha();
    showMessage("请输入信息后继续。");
}

function refreshCaptcha() {
    const left = Math.floor(Math.random() * 8) + 1;
    const right = Math.floor(Math.random() * 8) + 1;
    captchaAnswer = left + right;
    const question = getActivePanel()?.querySelector(".captcha-question");
    const input = getActiveCaptchaInput();
    if (question) {
        question.textContent = `${left} + ${right} = ?`;
    }
    if (input) {
        input.value = "";
    }
}

function checkCaptcha(value) {
    return Number(value) === captchaAnswer;
}

function getActivePanel() {
    return document.querySelector(".auth-panel.active");
}

function getActiveCaptchaInput() {
    return getActivePanel()?.querySelector(".captcha-input");
}

function getCaptchaValue() {
    return getActiveCaptchaInput()?.value.trim() || "";
}

function readUsers() {
    try {
        return JSON.parse(localStorage.getItem(USER_STORE_KEY) || "{}");
    } catch (_error) {
        return {};
    }
}

function saveUsers(users) {
    localStorage.setItem(USER_STORE_KEY, JSON.stringify(users));
}

function simpleHash(text) {
    // 本地演示用轻量哈希，避免明文密码直接存储；正式产品应放到后端加密保存。
    let hash = 2166136261;
    for (let i = 0; i < text.length; i += 1) {
        hash ^= text.charCodeAt(i);
        hash += (hash << 1) + (hash << 4) + (hash << 7) + (hash << 8) + (hash << 24);
    }
    return (hash >>> 0).toString(16);
}

function saveSession(username, role = "user") {
    localStorage.setItem(
        SESSION_KEY,
        JSON.stringify({
            username,
            role,
            loginAt: Date.now(),
        }),
    );
}

async function syncUserToBackend(username, role = "user") {
    return new Promise((resolve) => {
        let settled = false;
        let ws = null;

        function finish() {
            if (settled) return;
            settled = true;
            try {
                if (ws && ws.readyState === WebSocket.OPEN) ws.close();
            } catch (_error) {
                // 同步失败不影响本地登录
            }
            resolve();
        }

        try {
            ws = new WebSocket("ws://localhost:9998");
            const timer = window.setTimeout(finish, 1200);

            ws.onopen = () => {
                ws.send(JSON.stringify({
                    type: "auth_user_sync",
                    username,
                    user_role: role,
                }));
            };
            ws.onmessage = () => {
                window.clearTimeout(timer);
                finish();
            };
            ws.onerror = finish;
            ws.onclose = finish;
        } catch (_error) {
            finish();
        }
    });
}

async function sendAuthRequest(action, username, passwordHash) {
    return new Promise((resolve) => {
        let settled = false;
        let ws = null;

        function finish(result) {
            if (settled) return;
            settled = true;
            try {
                if (ws && ws.readyState === WebSocket.OPEN) ws.close();
            } catch (_error) {
                // 关闭失败不影响登录流程。
            }
            resolve(result);
        }

        try {
            ws = new WebSocket("ws://localhost:9998");
            const timer = window.setTimeout(() => {
                finish({ok: false, reason: "backend_timeout", offline: true});
            }, 1800);

            ws.onopen = () => {
                ws.send(JSON.stringify({
                    type: action === "register" ? "auth_register" : "auth_login",
                    username,
                    password_hash: passwordHash,
                    user_role: "user",
                }));
            };
            ws.onmessage = (event) => {
                window.clearTimeout(timer);
                try {
                    const data = JSON.parse(event.data);
                    if (data.type === "auth_result") {
                        finish(data);
                        return;
                    }
                } catch (_error) {
                    // 非 JSON 回复按失败处理。
                }
                finish({ok: false, reason: "bad_response"});
            };
            ws.onerror = () => finish({ok: false, reason: "backend_error", offline: true});
            ws.onclose = () => finish({ok: false, reason: "backend_closed", offline: true});
        } catch (_error) {
            finish({ok: false, reason: "backend_error", offline: true});
        }
    });
}

function enterApp() {
    window.location.href = getNextPage();
}

tabButtons.forEach((button) => {
    button.addEventListener("click", () => switchPanel(button.dataset.target));
});

document.querySelectorAll(".refresh-btn").forEach((button) => {
    button.addEventListener("click", refreshCaptcha);
});

registerForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const formData = new FormData(registerForm);
    const username = String(formData.get("username") || "").trim();
    const password = String(formData.get("password") || "");
    const confirmPassword = String(formData.get("confirmPassword") || "");
    const captcha = getCaptchaValue();

    if (username.length < 3) {
        showMessage("账号至少需要 3 个字符。", "error");
        return;
    }
    if (password.length < 6) {
        showMessage("密码至少需要 6 位。", "error");
        return;
    }
    if (password !== confirmPassword) {
        showMessage("两次输入的密码不一致。", "error");
        return;
    }
    if (!checkCaptcha(captcha)) {
        showMessage("验证码错误，请重新计算。", "error");
        refreshCaptcha();
        return;
    }

    const passwordHash = simpleHash(`${username}:${password}`);
    const result = await sendAuthRequest("register", username, passwordHash);
    if (result.reason === "user_exists") {
        showMessage("该账号已存在，请直接登录。", "error");
        return;
    }
    if (!result.ok) {
        showMessage("注册失败，请确认后端已经启动。", "error");
        return;
    }

    saveSession(username);
    showMessage("注册成功，正在进入系统。", "success");
    enterApp();
});

loginForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const formData = new FormData(loginForm);
    const username = String(formData.get("username") || "").trim();
    const password = String(formData.get("password") || "");
    const captcha = getCaptchaValue();

    if (!checkCaptcha(captcha)) {
        showMessage("验证码错误，请重新计算。", "error");
        refreshCaptcha();
        return;
    }

    const passwordHash = simpleHash(`${username}:${password}`);
    const result = await sendAuthRequest("login", username, passwordHash);
    if (!result.ok) {
        const users = readUsers();
        const user = users[username];
        const legacyLoginOk = user && user.passwordHash === passwordHash;
        if (legacyLoginOk) {
            // 兼容旧版本：旧账号存在浏览器本地时，登录成功后补注册到后端 SQLite。
            await sendAuthRequest("register", username, passwordHash);
        } else {
            showMessage("账号或密码错误。", "error");
            return;
        }
    }

    if (!result.ok && result.offline) {
        showMessage("后端未连接，无法完成登录。", "error");
        return;
    }

    const users = readUsers();
    const user = users[username];
    if (result.reason === "user_not_found" && (!user || user.passwordHash !== passwordHash)) {
        showMessage("账号或密码错误。", "error");
        return;
    }

    saveSession(username);
    showMessage("登录成功，正在进入系统。", "success");
    enterApp();
});

developerForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const formData = new FormData(developerForm);
    const password = String(formData.get("developerPassword") || "");
    const captcha = getCaptchaValue();

    if (!checkCaptcha(captcha)) {
        showMessage("验证码错误，请重新计算。", "error");
        refreshCaptcha();
        return;
    }
    if (password !== DEVELOPER_PASSWORD) {
        showMessage("开发者密码错误。", "error");
        return;
    }

    saveSession("developer", "developer");
    await syncUserToBackend("developer", "developer");
    showMessage("开发者模式已进入。", "success");
    enterApp();
});

refreshCaptcha();
