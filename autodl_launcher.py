from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path
from typing import TextIO


BACKEND_PORT = 9998
FRONTEND_PORT = 8080
STATE_DIRNAME = ".autodl_launcher"
STATE_FILENAME = "state.json"
LOG_DIRNAME = "logs"
DEFAULT_TAIL_LINES = 60


def now_text() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def format_log(prefix: str, text: str) -> str:
    return f"[{now_text()}] [{prefix}] {text}"


def is_port_open(port: int, host: str = "127.0.0.1", timeout: float = 0.5) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        return sock.connect_ex((host, port)) == 0
    finally:
        sock.close()


def wait_for_port(port: int, timeout: float = 12.0) -> bool:
    started = time.time()
    while time.time() - started < timeout:
        if is_port_open(port):
            return True
        time.sleep(0.2)
    return False


def process_alive(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def terminate_pid(pid: int, timeout: float = 5.0) -> bool:
    if not process_alive(pid):
        return True

    os.kill(pid, signal.SIGTERM)
    started = time.time()
    while time.time() - started < timeout:
        if not process_alive(pid):
            return True
        time.sleep(0.2)

    if process_alive(pid):
        os.kill(pid, signal.SIGKILL)
        time.sleep(0.5)
    return not process_alive(pid)


class ProjectLayout:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.backend_script = root / "server-2.py"
        self.frontend_dir = root / "digital-human-web" / "web"
        self.python = self._resolve_python()
        self.state_dir = root / STATE_DIRNAME
        self.log_dir = self.state_dir / LOG_DIRNAME
        self.state_file = self.state_dir / STATE_FILENAME

    def _resolve_python(self) -> Path:
        candidates = [
            self.root / ".venv" / "bin" / "python",
            self.root / ".venv" / "Scripts" / "python.exe",
        ]
        for candidate in candidates:
            if candidate.exists():
                return candidate
        raise FileNotFoundError("No project Python found under .venv.")

    def ensure_state_dirs(self) -> None:
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def backend_log(self) -> Path:
        return self.log_dir / "backend.log"

    def frontend_log(self) -> Path:
        return self.log_dir / "frontend.log"


def find_project_root(start: Path) -> Path:
    current = start.resolve()
    candidates = [current] + list(current.parents)
    for candidate in candidates:
        if (candidate / "server-2.py").exists() and (candidate / "digital-human-web").exists():
            return candidate
    raise FileNotFoundError("Unable to find project root from current path.")


def load_env_file(env_path: Path, env: dict[str, str]) -> None:
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in env:
            env[key] = value


def build_runtime_env(layout: ProjectLayout) -> dict[str, str]:
    env = os.environ.copy()
    load_env_file(layout.root / ".env.autodl", env)
    load_env_file(layout.root / ".env", env)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return env


def require_backend_env(env: dict[str, str]) -> None:
    if (
        env.get("API_KEY_ALI")
        or env.get("ANTHROPIC_AUTH_TOKEN")
        or env.get("DASHSCOPE_API_KEY")
        or env.get("OPENROUTER_API_KEY")
    ):
        return
    raise RuntimeError(
        "LLM API Key is missing. Set API_KEY_ALI in the shell, .env.autodl, or .env before start."
    )


def load_state(layout: ProjectLayout) -> dict:
    if not layout.state_file.exists():
        return {}
    try:
        return json.loads(layout.state_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def save_state(layout: ProjectLayout, state: dict) -> None:
    layout.ensure_state_dirs()
    layout.state_file.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def clear_state(layout: ProjectLayout) -> None:
    if layout.state_file.exists():
        layout.state_file.unlink()


def start_detached(layout: ProjectLayout) -> int:
    env = build_runtime_env(layout)
    require_backend_env(env)
    layout.ensure_state_dirs()

    if is_port_open(BACKEND_PORT):
        raise RuntimeError(f"Backend port {BACKEND_PORT} is already in use.")
    if is_port_open(FRONTEND_PORT):
        raise RuntimeError(f"Frontend port {FRONTEND_PORT} is already in use.")

    backend_log = layout.backend_log().open("a", encoding="utf-8")
    frontend_log = layout.frontend_log().open("a", encoding="utf-8")

    backend_cmd = [str(layout.python), "-u", str(layout.backend_script)]
    frontend_cmd = [str(layout.python), "-u", "-m", "http.server", str(FRONTEND_PORT)]

    backend_proc = subprocess.Popen(
        backend_cmd,
        cwd=str(layout.root),
        stdout=backend_log,
        stderr=subprocess.STDOUT,
        env=env,
        start_new_session=True,
    )
    frontend_proc = subprocess.Popen(
        frontend_cmd,
        cwd=str(layout.frontend_dir),
        stdout=frontend_log,
        stderr=subprocess.STDOUT,
        env=env,
        start_new_session=True,
    )

    backend_ready = wait_for_port(BACKEND_PORT)
    frontend_ready = wait_for_port(FRONTEND_PORT)

    save_state(
        layout,
        {
            "started_at": now_text(),
            "backend": {
                "pid": backend_proc.pid,
                "log": str(layout.backend_log()),
                "cmd": backend_cmd,
                "ready": backend_ready,
            },
            "frontend": {
                "pid": frontend_proc.pid,
                "log": str(layout.frontend_log()),
                "cmd": frontend_cmd,
                "ready": frontend_ready,
            },
        },
    )

    print(format_log("SYSTEM", f"Backend PID={backend_proc.pid}, ready={backend_ready}"))
    print(format_log("SYSTEM", f"Frontend PID={frontend_proc.pid}, ready={frontend_ready}"))
    print(format_log("SYSTEM", f"Logs: {layout.log_dir}"))
    return 0


def stop_detached(layout: ProjectLayout) -> int:
    state = load_state(layout)
    if not state:
        print(format_log("SYSTEM", "No launcher state found."))
        return 0

    backend_pid = state.get("backend", {}).get("pid")
    frontend_pid = state.get("frontend", {}).get("pid")

    backend_ok = terminate_pid(backend_pid) if backend_pid else True
    frontend_ok = terminate_pid(frontend_pid) if frontend_pid else True
    clear_state(layout)

    print(format_log("SYSTEM", f"Backend stopped={backend_ok}"))
    print(format_log("SYSTEM", f"Frontend stopped={frontend_ok}"))
    return 0 if backend_ok and frontend_ok else 1


def show_status(layout: ProjectLayout) -> int:
    state = load_state(layout)
    backend_pid = state.get("backend", {}).get("pid")
    frontend_pid = state.get("frontend", {}).get("pid")

    backend_alive = process_alive(backend_pid)
    frontend_alive = process_alive(frontend_pid)
    backend_port = is_port_open(BACKEND_PORT)
    frontend_port = is_port_open(FRONTEND_PORT)

    print(format_log("STATUS", f"Project root: {layout.root}"))
    print(format_log("STATUS", f"Python: {layout.python}"))
    print(format_log("STATUS", f"Backend pid={backend_pid} alive={backend_alive} port={BACKEND_PORT} open={backend_port}"))
    print(format_log("STATUS", f"Frontend pid={frontend_pid} alive={frontend_alive} port={FRONTEND_PORT} open={frontend_port}"))
    print(format_log("STATUS", f"Logs dir: {layout.log_dir}"))
    return 0


def tail_lines(path: Path, lines: int) -> list[str]:
    if not path.exists():
        return [f"<missing log file: {path}>"]
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        return list(deque(handle, maxlen=lines))


def follow_file(path: Path, prefix: str) -> None:
    if not path.exists():
        print(format_log(prefix, f"Missing log file: {path}"))
        return

    with path.open("r", encoding="utf-8", errors="replace") as handle:
        handle.seek(0, os.SEEK_END)
        while True:
            line = handle.readline()
            if line:
                print(format_log(prefix, line.rstrip()))
                continue
            time.sleep(0.3)


def show_logs(layout: ProjectLayout, lines: int, follow: bool) -> int:
    for prefix, path in (("BACKEND", layout.backend_log()), ("FRONTEND", layout.frontend_log())):
        print(format_log("SYSTEM", f"===== {prefix} ====="))
        for line in tail_lines(path, lines):
            print(format_log(prefix, line.rstrip()))

    if not follow:
        return 0

    threads = [
        threading.Thread(target=follow_file, args=(layout.backend_log(), "BACKEND"), daemon=True),
        threading.Thread(target=follow_file, args=(layout.frontend_log(), "FRONTEND"), daemon=True),
    ]
    for thread in threads:
        thread.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print(format_log("SYSTEM", "Stopped following logs."))
        return 0


def stream_process_output(stream: TextIO | None, prefix: str) -> None:
    if stream is None:
        return
    for raw_line in iter(stream.readline, ""):
        line = raw_line.rstrip()
        if line:
            print(format_log(prefix, line))


def run_foreground(layout: ProjectLayout) -> int:
    env = build_runtime_env(layout)
    require_backend_env(env)

    if is_port_open(BACKEND_PORT):
        raise RuntimeError(f"Backend port {BACKEND_PORT} is already in use.")
    if is_port_open(FRONTEND_PORT):
        raise RuntimeError(f"Frontend port {FRONTEND_PORT} is already in use.")

    backend_cmd = [str(layout.python), "-u", str(layout.backend_script)]
    frontend_cmd = [str(layout.python), "-u", "-m", "http.server", str(FRONTEND_PORT)]

    backend_proc = subprocess.Popen(
        backend_cmd,
        cwd=str(layout.root),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    frontend_proc = subprocess.Popen(
        frontend_cmd,
        cwd=str(layout.frontend_dir),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )

    print(format_log("SYSTEM", f"Backend PID={backend_proc.pid}"))
    print(format_log("SYSTEM", f"Frontend PID={frontend_proc.pid}"))

    threads = [
        threading.Thread(target=stream_process_output, args=(backend_proc.stdout, "BACKEND"), daemon=True),
        threading.Thread(target=stream_process_output, args=(frontend_proc.stdout, "FRONTEND"), daemon=True),
    ]
    for thread in threads:
        thread.start()

    backend_ready = wait_for_port(BACKEND_PORT)
    frontend_ready = wait_for_port(FRONTEND_PORT)
    print(format_log("SYSTEM", f"Backend ready={backend_ready}, frontend ready={frontend_ready}"))
    print(format_log("SYSTEM", "Press Ctrl+C to stop both services."))

    try:
        while True:
            backend_code = backend_proc.poll()
            frontend_code = frontend_proc.poll()
            if backend_code is not None:
                print(format_log("SYSTEM", f"Backend exited with code {backend_code}"))
                if frontend_proc.poll() is None:
                    frontend_proc.terminate()
                return backend_code
            if frontend_code is not None:
                print(format_log("SYSTEM", f"Frontend exited with code {frontend_code}"))
                if backend_proc.poll() is None:
                    backend_proc.terminate()
                return frontend_code
            time.sleep(0.5)
    except KeyboardInterrupt:
        print(format_log("SYSTEM", "Stopping services..."))
        for proc in (backend_proc, frontend_proc):
            if proc.poll() is None:
                proc.terminate()
        for proc in (backend_proc, frontend_proc):
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        return 0


def check_environment(layout: ProjectLayout) -> int:
    env = build_runtime_env(layout)
    print(format_log("CHECK", f"Project root: {layout.root}"))
    print(format_log("CHECK", f"Python: {layout.python}"))
    print(format_log("CHECK", f"Backend script exists: {layout.backend_script.exists()}"))
    print(format_log("CHECK", f"Frontend dir exists: {layout.frontend_dir.exists()}"))
    has_llm_key = bool(
        env.get("API_KEY_ALI")
        or env.get("ANTHROPIC_AUTH_TOKEN")
        or env.get("DASHSCOPE_API_KEY")
        or env.get("OPENROUTER_API_KEY")
    )
    print(format_log("CHECK", f"LLM API key set: {has_llm_key}"))
    print(format_log("CHECK", f"Backend port open: {is_port_open(BACKEND_PORT)}"))
    print(format_log("CHECK", f"Frontend port open: {is_port_open(FRONTEND_PORT)}"))
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Terminal launcher for running the digital human project on AutoDL."
    )
    parser.add_argument(
        "command",
        choices=["run", "start", "stop", "status", "logs", "check"],
        help="run: foreground logs | start: detached | stop: stop detached | status: show status | logs: show log files | check: env check",
    )
    parser.add_argument("--lines", type=int, default=DEFAULT_TAIL_LINES, help="Tail line count for logs command.")
    parser.add_argument("--follow", action="store_true", help="Follow logs after printing the tail.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    layout = ProjectLayout(find_project_root(Path.cwd()))

    if args.command == "run":
        return run_foreground(layout)
    if args.command == "start":
        return start_detached(layout)
    if args.command == "stop":
        return stop_detached(layout)
    if args.command == "status":
        return show_status(layout)
    if args.command == "logs":
        return show_logs(layout, lines=max(1, args.lines), follow=args.follow)
    if args.command == "check":
        return check_environment(layout)
    return 1


if __name__ == "__main__":
    sys.exit(main())
