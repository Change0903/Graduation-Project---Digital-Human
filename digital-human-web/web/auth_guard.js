(function () {
    const SESSION_KEY = "digital_human_session_v1";
    const DEV_BYPASS_PARAM = "dev_bypass";

    function saveSession(session) {
        localStorage.setItem(SESSION_KEY, JSON.stringify(session));
    }

    function readSession() {
        try {
            const raw = localStorage.getItem(SESSION_KEY);
            return raw ? JSON.parse(raw) : null;
        } catch (_error) {
            return null;
        }
    }

    function isSessionValid(session) {
        if (!session || !session.username || !session.role || !session.loginAt) return false;
        const maxAgeMs = 24 * 60 * 60 * 1000;
        return Date.now() - Number(session.loginAt) < maxAgeMs;
    }

    function logout() {
        localStorage.removeItem(SESSION_KEY);
        window.location.href = "login.html";
    }

    const params = new URLSearchParams(window.location.search);
    if (params.get(DEV_BYPASS_PARAM) === "1") {
        saveSession({
            username: "developer",
            role: "developer",
            loginAt: Date.now(),
        });
        window.history.replaceState(null, "", window.location.pathname);
    }

    const session = readSession();
    if (!isSessionValid(session)) {
        const next = encodeURIComponent(window.location.pathname.split("/").pop() || "index-2.html");
        window.location.replace(`login.html?next=${next}`);
        return;
    }

    window.DigitalHumanAuth = {
        session,
        logout,
    };
})();
