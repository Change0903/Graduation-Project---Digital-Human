import os
import queue
import shutil
import socket
import subprocess
import sys
import threading
import time
import webbrowser
import json
from datetime import datetime
from pathlib import Path

import tkinter as tk
from tkinter import ttk
from tkinter import messagebox
from tkinter.scrolledtext import ScrolledText

from storage import DigitalHumanStorage


BACKEND_PORT = 9998
FRONTEND_PORT = 8080
FRONTEND_URL = f"http://localhost:{FRONTEND_PORT}/index-2.html"
LOGIN_URL = f"http://localhost:{FRONTEND_PORT}/login.html"
DEV_FRONTEND_URL = f"{FRONTEND_URL}?dev_bypass=1"
FRONTEND_CONSOLE_PREFIX = "[前端控制台]"

COLOR_APP_BG = "#eef3f9"
COLOR_CARD_BG = "#ffffff"
COLOR_BORDER = "#d6dfec"
COLOR_TEXT = "#1f2a37"
COLOR_MUTED = "#637083"
COLOR_ACCENT = "#1f6feb"
COLOR_SUCCESS = "#1a7f37"
COLOR_WARNING = "#b7791f"
COLOR_IDLE = "#94a3b8"
COLOR_LOG_BG = "#f8fafc"
COLOR_LOG_TEXT = "#1e293b"


def is_port_open(port: int, host: str = "127.0.0.1", timeout: float = 0.3) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        return sock.connect_ex((host, port)) == 0
    finally:
        sock.close()


def find_listening_pids(port: int) -> set[int]:
    """返回占用指定端口的 PID。用于清理旧启动器残留进程。"""
    if os.name != "nt":
        return set()
    try:
        result = subprocess.run(
            ["netstat", "-ano", "-p", "tcp"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
        )
    except Exception:
        return set()

    pids: set[int] = set()
    port_suffix = f":{port}"
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) < 5:
            continue
        local_addr, state, pid_text = parts[1], parts[3], parts[4]
        if state.upper() != "LISTENING":
            continue
        if not local_addr.endswith(port_suffix):
            continue
        try:
            pids.add(int(pid_text))
        except ValueError:
            continue
    return pids


class LauncherApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("数字人启动器 · 语音模式")
        self.root.geometry("980x700")
        self.root.minsize(920, 640)
        self.root.configure(bg=COLOR_APP_BG)

        self.base_dir = self._resolve_base_dir()
        self.frontend_dir = self.base_dir / "digital-human-web" / "web"
        self.backend_script = self.base_dir / "server-2.py"
        self.runtime_dir = self.base_dir / "runtime"
        self.user_registry_file = self.runtime_dir / "user_registry.json"
        self.user_memory_file = self.runtime_dir / "user_memory.json"
        self.admin_delete_state_file = self.runtime_dir / "admin_delete_state.json"
        self.sqlite_file = self.runtime_dir / "digital_human.db"
        self.storage = DigitalHumanStorage(self.sqlite_file)
        self.storage.migrate_legacy_json(self.user_registry_file, self.user_memory_file)
        self.log_queue: queue.Queue[str] = queue.Queue()
        self.python_exe = self._resolve_python_exe()

        self.backend_proc: subprocess.Popen | None = None
        self.frontend_proc: subprocess.Popen | None = None
        self._ws_noise_remaining = 0

        self._setup_styles()
        self._build_ui()
        self._append_log(f"[系统] 项目目录: {self.base_dir}")
        self._append_log(f"[系统] Python: {self.python_exe}")
        self._append_log(f"[系统] 前端目录: {self.frontend_dir}")
        self._append_log(f"[系统] 后端脚本: {self.backend_script}")
        self._append_log(f"[系统] SQLite数据: {self.sqlite_file}")

        self._refresh_status()
        self._drain_log_queue()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _resolve_base_dir(self) -> Path:
        if getattr(sys, "frozen", False):
            exe_dir = Path(sys.executable).resolve().parent
            candidates = [exe_dir, exe_dir.parent]
            for candidate in candidates:
                if (candidate / "server-2.py").exists():
                    return candidate
            return exe_dir
        return Path(__file__).resolve().parent

    def _resolve_python_exe(self) -> Path:
        candidates: list[Path] = []
        venv_python = self.base_dir / ".venv" / "Scripts" / "python.exe"
        if venv_python.exists():
            candidates.append(venv_python.resolve())

        found = shutil.which("python")
        if found:
            candidates.append(Path(found).resolve())

        if not getattr(sys, "frozen", False):
            candidates.append(Path(sys.executable).resolve())

        unique_candidates: list[Path] = []
        seen: set[str] = set()
        for c in candidates:
            key = str(c).lower()
            if key in seen:
                continue
            seen.add(key)
            unique_candidates.append(c)

        if not unique_candidates:
            raise RuntimeError("找不到可用的 python.exe。请先安装 Python，或在项目目录放置 .venv。")

        best_path: Path | None = None
        best_probe: dict | None = None
        all_probes: list[tuple[Path, dict]] = []
        for candidate in unique_candidates:
            probe = self._probe_python(candidate)
            all_probes.append((candidate, probe))
            if not probe.get("required_ok"):
                continue
            if best_probe is None:
                best_path = candidate
                best_probe = probe
                continue
            if self._version_tuple(probe.get("edge_tts_version", "")) > self._version_tuple(
                best_probe.get("edge_tts_version", "")
            ):
                best_path = candidate
                best_probe = probe

        for candidate, probe in all_probes:
            self.log_queue.put(
                f"[系统] Python候选: {candidate} | required_ok={probe.get('required_ok')} | edge_tts={probe.get('edge_tts_version','unknown')}"
            )

        if best_path is not None:
            return best_path

        # 没有满足依赖的解释器时，回退到第一个可用解释器，并在日志提示风险
        fallback = unique_candidates[0]
        self.log_queue.put(f"[系统] 警告：未找到依赖完整的 Python，回退使用 {fallback}")
        return fallback

    def _probe_python(self, python_path: Path) -> dict:
        script = (
            "import json, importlib.util\n"
            "def has(m):\n"
            "  return importlib.util.find_spec(m) is not None\n"
            "res={'required_ok': False, 'edge_tts_version': ''}\n"
            "if has('openai') and has('websockets') and has('edge_tts'):\n"
            "  import edge_tts\n"
            "  res['required_ok']=True\n"
            "  res['edge_tts_version']=getattr(edge_tts,'__version__','')\n"
            "print(json.dumps(res, ensure_ascii=True))\n"
        )
        try:
            out = subprocess.check_output(
                [str(python_path), "-c", script],
                text=True,
                timeout=6,
                encoding="utf-8",
                errors="replace",
            ).strip()
            return json.loads(out.splitlines()[-1]) if out else {"required_ok": False, "edge_tts_version": ""}
        except Exception:
            return {"required_ok": False, "edge_tts_version": ""}

    def _version_tuple(self, version: str) -> tuple[int, ...]:
        parts: list[int] = []
        for token in str(version).split("."):
            num = ""
            for ch in token:
                if ch.isdigit():
                    num += ch
                else:
                    break
            if num:
                parts.append(int(num))
            else:
                parts.append(0)
        return tuple(parts)

    def _setup_styles(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure("App.TFrame", background=COLOR_APP_BG)
        style.configure("App.TNotebook", background=COLOR_APP_BG, borderwidth=0)
        style.configure(
            "App.TNotebook.Tab",
            padding=(14, 8),
            font=("Microsoft YaHei UI", 10, "bold"),
        )
        style.map(
            "App.TNotebook.Tab",
            background=[("selected", COLOR_CARD_BG), ("!selected", "#dfe7f4")],
            foreground=[("selected", COLOR_ACCENT), ("!selected", COLOR_MUTED)],
        )
        style.configure(
            "Primary.TButton",
            font=("Microsoft YaHei UI", 10, "bold"),
            padding=(14, 8),
        )
        style.configure(
            "Secondary.TButton",
            font=("Microsoft YaHei UI", 10),
            padding=(12, 8),
        )

    def _status_dot_color(self, proc_alive: bool, port_alive: bool) -> str:
        if proc_alive and port_alive:
            return COLOR_SUCCESS
        if proc_alive or port_alive:
            return COLOR_WARNING
        return COLOR_IDLE

    def _configure_log_widget(self, widget: ScrolledText) -> None:
        widget.configure(state="disabled")
        widget.tag_configure("system", foreground="#0f766e")
        widget.tag_configure("warn", foreground="#b45309")
        widget.tag_configure("error", foreground="#b91c1c")
        widget.tag_configure("normal", foreground=COLOR_LOG_TEXT)

    def _build_ui(self) -> None:
        container = ttk.Frame(self.root, style="App.TFrame", padding=(14, 12, 14, 12))
        container.pack(fill="both", expand=True)

        header = ttk.Frame(container, style="App.TFrame")
        header.pack(fill="x")
        tk.Label(
            header,
            text="数字人启动器",
            font=("Microsoft YaHei UI", 17, "bold"),
            fg=COLOR_TEXT,
            bg=COLOR_APP_BG,
        ).pack(anchor="w")
        tk.Label(
            header,
            text="语音模式 · 一键启动前后端 · 日志已分区显示",
            font=("Microsoft YaHei UI", 10),
            fg=COLOR_MUTED,
            bg=COLOR_APP_BG,
        ).pack(anchor="w", pady=(2, 0))
        ttk.Separator(container, orient="horizontal").pack(fill="x", pady=(10, 10))

        status_row = tk.Frame(container, bg=COLOR_APP_BG)
        status_row.pack(fill="x")
        status_row.grid_columnconfigure(0, weight=1)
        status_row.grid_columnconfigure(1, weight=1)

        self.backend_status_var = tk.StringVar(value="进程：未知 | 端口：未知")
        self.frontend_status_var = tk.StringVar(value="进程：未知 | 端口：未知")

        backend_card = tk.Frame(
            status_row,
            bg=COLOR_CARD_BG,
            highlightthickness=1,
            highlightbackground=COLOR_BORDER,
            highlightcolor=COLOR_BORDER,
        )
        backend_card.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        backend_head = tk.Frame(backend_card, bg=COLOR_CARD_BG)
        backend_head.pack(fill="x", padx=12, pady=(10, 4))
        self.backend_dot_label = tk.Label(
            backend_head, text="●", fg=COLOR_IDLE, bg=COLOR_CARD_BG, font=("Segoe UI", 12, "bold")
        )
        self.backend_dot_label.pack(side="left")
        tk.Label(
            backend_head, text="后端状态", fg=COLOR_TEXT, bg=COLOR_CARD_BG, font=("Microsoft YaHei UI", 11, "bold")
        ).pack(side="left", padx=(6, 0))
        tk.Label(
            backend_card,
            textvariable=self.backend_status_var,
            anchor="w",
            justify="left",
            fg=COLOR_MUTED,
            bg=COLOR_CARD_BG,
            font=("Microsoft YaHei UI", 10),
        ).pack(fill="x", padx=12, pady=(0, 10))

        frontend_card = tk.Frame(
            status_row,
            bg=COLOR_CARD_BG,
            highlightthickness=1,
            highlightbackground=COLOR_BORDER,
            highlightcolor=COLOR_BORDER,
        )
        frontend_card.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        frontend_head = tk.Frame(frontend_card, bg=COLOR_CARD_BG)
        frontend_head.pack(fill="x", padx=12, pady=(10, 4))
        self.frontend_dot_label = tk.Label(
            frontend_head, text="●", fg=COLOR_IDLE, bg=COLOR_CARD_BG, font=("Segoe UI", 12, "bold")
        )
        self.frontend_dot_label.pack(side="left")
        tk.Label(
            frontend_head, text="前端状态", fg=COLOR_TEXT, bg=COLOR_CARD_BG, font=("Microsoft YaHei UI", 11, "bold")
        ).pack(side="left", padx=(6, 0))
        tk.Label(
            frontend_card,
            textvariable=self.frontend_status_var,
            anchor="w",
            justify="left",
            fg=COLOR_MUTED,
            bg=COLOR_CARD_BG,
            font=("Microsoft YaHei UI", 10),
        ).pack(fill="x", padx=12, pady=(0, 10))

        button_row = ttk.Frame(container, style="App.TFrame")
        button_row.pack(fill="x", pady=(10, 8))
        ttk.Button(button_row, text="一键启动", style="Primary.TButton", command=self.start_all).pack(
            side="left", padx=(0, 8)
        )
        ttk.Button(button_row, text="全部停止", style="Secondary.TButton", command=self.stop_all).pack(
            side="left", padx=(0, 8)
        )
        ttk.Button(button_row, text="打开前端页面", style="Secondary.TButton", command=self.open_frontend).pack(
            side="left", padx=(0, 8)
        )
        ttk.Button(button_row, text="开发者直达", style="Secondary.TButton", command=self.open_frontend_dev).pack(
            side="left", padx=(0, 8)
        )
        ttk.Button(button_row, text="清空日志", style="Secondary.TButton", command=self.clear_log).pack(side="left")

        notebook = ttk.Notebook(container, style="App.TNotebook")
        notebook.pack(fill="both", expand=True)

        frame_main = tk.Frame(notebook, bg=COLOR_CARD_BG)
        frame_frontend = tk.Frame(notebook, bg=COLOR_CARD_BG)
        frame_admin = tk.Frame(notebook, bg=COLOR_CARD_BG)
        notebook.add(frame_main, text="系统 + 后端")
        notebook.add(frame_frontend, text="前端控制台")
        notebook.add(frame_admin, text="后台管理")

        self.log_text = ScrolledText(
            frame_main,
            height=30,
            wrap="word",
            font=("Consolas", 10),
            bg=COLOR_LOG_BG,
            fg=COLOR_LOG_TEXT,
            insertbackground=COLOR_LOG_TEXT,
            relief="flat",
            padx=8,
            pady=8,
        )
        self.log_text.pack(fill="both", expand=True, padx=8, pady=8)
        self._configure_log_widget(self.log_text)

        self.frontend_log_text = ScrolledText(
            frame_frontend,
            height=30,
            wrap="word",
            font=("Consolas", 10),
            bg=COLOR_LOG_BG,
            fg=COLOR_LOG_TEXT,
            insertbackground=COLOR_LOG_TEXT,
            relief="flat",
            padx=8,
            pady=8,
        )
        self.frontend_log_text.pack(fill="both", expand=True, padx=8, pady=8)
        self._configure_log_widget(self.frontend_log_text)

        self._build_admin_tab(frame_admin)

    def _build_admin_tab(self, parent: tk.Frame) -> None:
        toolbar = tk.Frame(parent, bg=COLOR_CARD_BG)
        toolbar.pack(fill="x", padx=10, pady=(10, 6))

        self.admin_summary_var = tk.StringVar(value="用户数：0    对话数：0")
        tk.Label(
            toolbar,
            textvariable=self.admin_summary_var,
            fg=COLOR_TEXT,
            bg=COLOR_CARD_BG,
            font=("Microsoft YaHei UI", 10, "bold"),
        ).pack(side="left")

        ttk.Button(toolbar, text="刷新", style="Secondary.TButton", command=self.refresh_admin_data).pack(
            side="right", padx=(8, 0)
        )
        ttk.Button(
            toolbar,
            text="一键删除全部",
            style="Secondary.TButton",
            command=self.delete_all_admin_data,
        ).pack(side="right", padx=(8, 0))
        ttk.Button(
            toolbar,
            text="删除选中用户",
            style="Secondary.TButton",
            command=self.delete_selected_user,
        ).pack(side="right", padx=(8, 0))
        ttk.Button(
            toolbar,
            text="删除选中对话",
            style="Secondary.TButton",
            command=self.delete_selected_conversation,
        ).pack(side="right", padx=(8, 0))

        columns = ("role", "conversation_count", "message_count", "last_login")
        self.admin_tree = ttk.Treeview(parent, columns=columns, show="tree headings", height=22)
        self.admin_tree.heading("#0", text="账号 / 对话")
        self.admin_tree.heading("role", text="角色")
        self.admin_tree.heading("conversation_count", text="对话数")
        self.admin_tree.heading("message_count", text="消息数")
        self.admin_tree.heading("last_login", text="最近登录")
        self.admin_tree.column("#0", width=260, minwidth=180)
        self.admin_tree.column("role", width=90, anchor="center")
        self.admin_tree.column("conversation_count", width=90, anchor="center")
        self.admin_tree.column("message_count", width=90, anchor="center")
        self.admin_tree.column("last_login", width=160, anchor="center")
        self.admin_tree.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        self.refresh_admin_data()

    def _append_log(self, text: str) -> None:
        self._append_log_to_widget(self.log_text, text)

    def _append_frontend_log(self, text: str) -> None:
        self._append_log_to_widget(self.frontend_log_text, text)

    def _append_log_to_widget(self, widget: ScrolledText, text: str) -> None:
        tag = "normal"
        lower = text.lower()
        if text.startswith("[系统]"):
            tag = "system"
        elif "traceback" in lower or " error" in lower or "❌" in text:
            tag = "error"
        elif "warning" in lower or "⚠" in text or "[warn]" in lower:
            tag = "warn"

        widget.configure(state="normal")
        widget.insert("end", text + "\n", tag)
        widget.see("end")
        widget.configure(state="disabled")

    def _drain_log_queue(self) -> None:
        try:
            while True:
                line = self.log_queue.get_nowait()
                if line.startswith(FRONTEND_CONSOLE_PREFIX):
                    frontend_line = line[len(FRONTEND_CONSOLE_PREFIX):].lstrip()
                    self._append_frontend_log(frontend_line)
                else:
                    self._append_log(line)
        except queue.Empty:
            pass
        self.root.after(150, self._drain_log_queue)

    def _should_filter_raw_line(self, name: str, line: str) -> bool:
        if name != "后端":
            return False
        text = line.strip()

        # Filter noisy websocket probe traceback blocks:
        # "opening handshake failed" ... "InvalidMessage: did not receive a valid HTTP request"
        if self._ws_noise_remaining > 0:
            self._ws_noise_remaining -= 1
            if "websockets.exceptions.InvalidMessage: did not receive a valid HTTP request" in text:
                self._ws_noise_remaining = 0
            return True

        if text == "opening handshake failed":
            self._ws_noise_remaining = 80
            return True

        return False

    def _read_stream(self, name: str, proc: subprocess.Popen, echo_raw: bool) -> None:
        if proc.stdout is None:
            return
        for line in iter(proc.stdout.readline, ""):
            if not line:
                break
            if echo_raw:
                text = line.rstrip("\r\n")
                if self._should_filter_raw_line(name, text):
                    continue
                self.log_queue.put(text)
        exit_code = proc.wait()
        self.log_queue.put(f"[系统] {name} 进程退出（exit={exit_code}）")

    def _watch_port_ready(self, name: str, port: int, proc: subprocess.Popen, timeout: float = 12.0) -> None:
        deadline = threading.Event()
        elapsed = 0.0
        while elapsed < timeout:
            if proc.poll() is not None:
                self.log_queue.put(f"[系统] {name} 进程提前退出，端口 {port} 未就绪")
                return
            if is_port_open(port):
                self.log_queue.put(f"[系统] {name} 端口 {port} 已就绪")
                return
            deadline.wait(0.2)
            elapsed += 0.2
        self.log_queue.put(f"[系统] {name} 等待端口 {port} 超时，请检查日志")

    def _spawn(self, name: str, cmd: list[str], cwd: Path, echo_raw: bool) -> subprocess.Popen:
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        cmd_str = " ".join(cmd)
        self.log_queue.put(f"[系统] ===== {name} 终端 =====")
        self.log_queue.put(f"[系统] 启动{name}: {cmd_str}")
        self.log_queue.put(f"[系统] {name}工作目录: {cwd}")
        proc = subprocess.Popen(
            cmd,
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=creationflags,
            env=env,
        )
        self.log_queue.put(f"[系统] {name} PID={proc.pid}")
        threading.Thread(target=self._read_stream, args=(name, proc, echo_raw), daemon=True).start()
        return proc

    def _stop_proc(self, name: str, proc: subprocess.Popen | None) -> None:
        if proc is None or proc.poll() is not None:
            return
        self.log_queue.put(f"[系统] 正在停止{name}...")
        proc.terminate()
        try:
            proc.wait(timeout=4)
        except subprocess.TimeoutExpired:
            self.log_queue.put(f"[系统] {name}未及时退出，强制结束")
            proc.kill()
            proc.wait(timeout=2)

    def _stop_port_processes(self, name: str, port: int) -> None:
        """清理占用项目端口的旧进程，解决旧启动器残留导致无法接管日志的问题。"""
        pids = find_listening_pids(port)
        current_pid = os.getpid()
        pids.discard(current_pid)
        if not pids:
            return

        for pid in sorted(pids):
            self.log_queue.put(f"[系统] 发现{name}端口 {port} 被旧进程 PID={pid} 占用，正在结束")
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/F", "/T"],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=8,
                )
            except Exception as exc:
                self.log_queue.put(f"[系统] 结束 PID={pid} 失败：{exc}")

        time.sleep(0.4)

    def _load_json_file(self, path: Path, default):
        if not path.exists():
            return default
        try:
            with path.open("r", encoding="utf-8") as file:
                data = json.load(file)
            return data if data is not None else default
        except Exception as exc:
            messagebox.showerror("读取失败", f"读取 {path} 失败：\n{exc}")
            return default

    def _save_json_file(self, path: Path, data) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False, indent=2)

    def _mark_conversation_deleted(self, user: str, conversation: str) -> None:
        state = self._load_json_file(self.admin_delete_state_file, {})
        if not isinstance(state, dict):
            state = {}
        deleted_conversations = state.setdefault("deleted_conversations", {})
        if not isinstance(deleted_conversations, dict):
            deleted_conversations = {}
            state["deleted_conversations"] = deleted_conversations
        user_deleted = deleted_conversations.setdefault(user, [])
        if conversation not in user_deleted:
            user_deleted.append(conversation)
        self._save_json_file(self.admin_delete_state_file, state)

    def _mark_user_deleted(self, user: str) -> None:
        state = self._load_json_file(self.admin_delete_state_file, {})
        if not isinstance(state, dict):
            state = {}
        deleted_users = state.setdefault("deleted_users", {})
        if isinstance(deleted_users, list):
            deleted_users = {item: 1 for item in deleted_users}
            state["deleted_users"] = deleted_users
        if not isinstance(deleted_users, dict):
            deleted_users = {}
            state["deleted_users"] = deleted_users
        deleted_users[user] = int(time.time())
        self._save_json_file(self.admin_delete_state_file, state)

    def _mark_all_deleted(self) -> None:
        state = self._load_json_file(self.admin_delete_state_file, {})
        if not isinstance(state, dict):
            state = {}
        state["delete_all_at"] = int(time.time())
        self._save_json_file(self.admin_delete_state_file, state)

    def _split_memory_key(self, key: str) -> tuple[str, str]:
        marker = "__conv__"
        if marker in key:
            user, conversation = key.split(marker, 1)
            return user or "guest", conversation or "default"
        return key or "guest", "legacy"

    def _resolve_memory_key(self, memory: dict, user: str, conversation: str) -> str:
        """兼容旧版记忆：旧数据直接用用户名当 key，没有 __conv__ 对话后缀。"""
        new_key = f"{user}__conv__{conversation}"
        if new_key in memory:
            return new_key
        if conversation == "legacy" and user in memory:
            return user
        return new_key

    def _format_timestamp(self, value) -> str:
        try:
            ts = float(value)
            if ts > 10_000_000_000:
                ts = ts / 1000
            return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
        except Exception:
            return "-"

    def _collect_admin_data(self) -> dict:
        return self.storage.collect_admin_data()

    def refresh_admin_data(self) -> None:
        if not hasattr(self, "admin_tree"):
            return
        data = self._collect_admin_data()
        users = data["users"]

        self.admin_tree.delete(*self.admin_tree.get_children())
        total_conversations = 0
        for username in sorted(users):
            info = users[username]
            conversations = info.get("conversations", {})
            conversation_count = len(conversations)
            message_count = sum(c.get("message_count", 0) for c in conversations.values())
            total_conversations += conversation_count
            user_iid = f"user::{username}"
            self.admin_tree.insert(
                "",
                "end",
                iid=user_iid,
                text=username,
                values=(
                    info.get("role", "user"),
                    conversation_count,
                    message_count,
                    self._format_timestamp(info.get("last_login")),
                ),
                open=True,
            )

            for conversation_id in sorted(conversations):
                conv = conversations[conversation_id]
                self.admin_tree.insert(
                    user_iid,
                    "end",
                    iid=f"conv::{username}::{conversation_id}",
                    text=f"对话：{conv.get('title') or '未命名对话'}",
                    values=("", "", conv.get("message_count", 0), ""),
                )

        self.admin_summary_var.set(f"用户数：{len(users)}    对话数：{total_conversations}")

    def _selected_admin_item(self) -> tuple[str, str | None, str | None]:
        selected = self.admin_tree.selection() if hasattr(self, "admin_tree") else ()
        if not selected:
            return "", None, None
        item_id = selected[0]
        if item_id.startswith("conv::"):
            _, user, conversation = item_id.split("::", 2)
            return "conversation", user, conversation
        if item_id.startswith("user::"):
            return "user", item_id.split("::", 1)[1], None
        return "", None, None

    def delete_selected_conversation(self) -> None:
        item_type, user, conversation = self._selected_admin_item()
        if item_type != "conversation" or not user or not conversation:
            messagebox.showinfo("提示", "请先选中一条具体对话。")
            return
        if not messagebox.askyesno("确认删除", f"确定删除用户 {user} 的对话 {conversation} 吗？"):
            return

        self.storage.delete_conversation(user, conversation)
        self.log_queue.put(f"[系统] 已删除对话记忆: {user} / {conversation}")
        self.refresh_admin_data()

    def delete_selected_user(self) -> None:
        item_type, user, _conversation = self._selected_admin_item()
        if item_type == "conversation":
            parent = self.admin_tree.parent(self.admin_tree.selection()[0])
            user = parent.split("::", 1)[1] if parent.startswith("user::") else user
        if not user:
            messagebox.showinfo("提示", "请先选中一个用户。")
            return
        if not messagebox.askyesno("确认删除", f"确定删除用户 {user} 及其全部对话记忆吗？"):
            return

        self.storage.delete_user(user)
        self.log_queue.put(f"[系统] 已删除用户及记忆: {user}")
        self.refresh_admin_data()

    def delete_all_admin_data(self) -> None:
        if not messagebox.askyesno("确认一键删除", "确定删除全部用户登记和全部对话记忆吗？此操作不可恢复。"):
            return
        self.storage.delete_all()
        self.log_queue.put("[系统] 已一键删除全部用户登记和对话记忆")
        self.refresh_admin_data()

    def start_backend(self) -> None:
        if self.backend_proc and self.backend_proc.poll() is None:
            self.log_queue.put("[系统] 后端已在运行")
            return
        if is_port_open(BACKEND_PORT):
            self._stop_port_processes("后端", BACKEND_PORT)
            if is_port_open(BACKEND_PORT):
                self.log_queue.put(f"[系统] 端口 {BACKEND_PORT} 仍被占用，后端启动失败")
                return
        if not self.backend_script.exists():
            messagebox.showerror("启动失败", f"找不到后端脚本: {self.backend_script}")
            return
        cmd = [str(self.python_exe), "-u", str(self.backend_script.name)]
        self.backend_proc = self._spawn("后端", cmd, self.base_dir, echo_raw=True)
        threading.Thread(
            target=self._watch_port_ready,
            args=("后端", BACKEND_PORT, self.backend_proc),
            daemon=True,
        ).start()

    def start_frontend(self) -> None:
        if self.frontend_proc and self.frontend_proc.poll() is None:
            self.log_queue.put("[系统] 前端已在运行")
            return
        if is_port_open(FRONTEND_PORT):
            self._stop_port_processes("前端", FRONTEND_PORT)
            if is_port_open(FRONTEND_PORT):
                self.log_queue.put(f"[系统] 端口 {FRONTEND_PORT} 仍被占用，前端启动失败")
                return
        if not self.frontend_dir.exists():
            messagebox.showerror("启动失败", f"找不到前端目录: {self.frontend_dir}")
            return
        cmd = [str(self.python_exe), "-u", "-m", "http.server", str(FRONTEND_PORT)]
        self.frontend_proc = self._spawn("前端", cmd, self.frontend_dir, echo_raw=False)
        threading.Thread(
            target=self._watch_port_ready,
            args=("前端", FRONTEND_PORT, self.frontend_proc),
            daemon=True,
        ).start()

    def start_all(self) -> None:
        self.start_backend()
        self.start_frontend()
        self.root.after(1200, self.open_frontend)

    def stop_all(self) -> None:
        self._stop_proc("后端", self.backend_proc)
        self._stop_proc("前端", self.frontend_proc)
        self.backend_proc = None
        self.frontend_proc = None
        self._stop_port_processes("后端", BACKEND_PORT)
        self._stop_port_processes("前端", FRONTEND_PORT)
        self.log_queue.put("[系统] 全部停止完成")

    def clear_log(self) -> None:
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")
        self.frontend_log_text.configure(state="normal")
        self.frontend_log_text.delete("1.0", "end")
        self.frontend_log_text.configure(state="disabled")

    def open_frontend(self) -> None:
        webbrowser.open(LOGIN_URL)
        self.log_queue.put(f"[系统] 已打开登录页: {LOGIN_URL}")

    def open_frontend_dev(self) -> None:
        webbrowser.open(DEV_FRONTEND_URL)
        self.log_queue.put(f"[系统] 已开发者直达: {DEV_FRONTEND_URL}")

    def _refresh_status(self) -> None:
        backend_proc_alive = self.backend_proc is not None and self.backend_proc.poll() is None
        frontend_proc_alive = self.frontend_proc is not None and self.frontend_proc.poll() is None
        backend_port_alive = is_port_open(BACKEND_PORT)
        frontend_port_alive = is_port_open(FRONTEND_PORT)

        self.backend_status_var.set(
            f"进程：{'运行中' if backend_proc_alive else '已停止'}    端口 {BACKEND_PORT}：{'已监听' if backend_port_alive else '未监听'}"
        )
        self.frontend_status_var.set(
            f"进程：{'运行中' if frontend_proc_alive else '已停止'}    端口 {FRONTEND_PORT}：{'已监听' if frontend_port_alive else '未监听'}"
        )
        self.backend_dot_label.configure(fg=self._status_dot_color(backend_proc_alive, backend_port_alive))
        self.frontend_dot_label.configure(fg=self._status_dot_color(frontend_proc_alive, frontend_port_alive))

        self.root.after(1000, self._refresh_status)

    def _on_close(self) -> None:
        self.stop_all()
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    LauncherApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
