import os
import queue
import shutil
import socket
import subprocess
import sys
import threading
import webbrowser
import json
from pathlib import Path

import tkinter as tk
from tkinter import ttk
from tkinter import messagebox
from tkinter.scrolledtext import ScrolledText


BACKEND_PORT = 9998
FRONTEND_PORT = 8080
FRONTEND_URL = f"http://localhost:{FRONTEND_PORT}/index-2.html"
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
        ttk.Button(button_row, text="清空日志", style="Secondary.TButton", command=self.clear_log).pack(side="left")

        notebook = ttk.Notebook(container, style="App.TNotebook")
        notebook.pack(fill="both", expand=True)

        frame_main = tk.Frame(notebook, bg=COLOR_CARD_BG)
        frame_frontend = tk.Frame(notebook, bg=COLOR_CARD_BG)
        notebook.add(frame_main, text="系统 + 后端")
        notebook.add(frame_frontend, text="前端控制台")

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

    def start_backend(self) -> None:
        if self.backend_proc and self.backend_proc.poll() is None:
            self.log_queue.put("[系统] 后端已在运行")
            return
        if is_port_open(BACKEND_PORT):
            self.log_queue.put(f"[系统] 端口 {BACKEND_PORT} 已被占用，跳过后端启动")
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
            self.log_queue.put(f"[系统] 端口 {FRONTEND_PORT} 已被占用，跳过前端启动")
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
        self.log_queue.put("[系统] 全部停止完成")

    def clear_log(self) -> None:
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")
        self.frontend_log_text.configure(state="normal")
        self.frontend_log_text.delete("1.0", "end")
        self.frontend_log_text.configure(state="disabled")

    def open_frontend(self) -> None:
        webbrowser.open(FRONTEND_URL)
        self.log_queue.put(f"[系统] 已打开: {FRONTEND_URL}")

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
