from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path


class EmotionWorkerClient:
    def __init__(self, project_root: str | Path) -> None:
        self.project_root = Path(project_root)
        self.python_exe = self.project_root / ".venv-deepface" / "Scripts" / "python.exe"
        self.worker_script = self.project_root / "emotion" / "emotion_worker.py"
        self.process: asyncio.subprocess.Process | None = None
        self.pending: dict[str, asyncio.Future] = {}
        self.stdout_task: asyncio.Task | None = None
        self.stderr_task: asyncio.Task | None = None
        self.write_lock = asyncio.Lock()
        self.start_lock = asyncio.Lock()
        self.response_timeout_seconds = 45

    async def ensure_started(self) -> None:
        if self.process is not None and self.process.returncode is None:
            return

        async with self.start_lock:
            if self.process is not None and self.process.returncode is None:
                return

            if not self.python_exe.exists():
                raise RuntimeError(f"未找到 DeepFace 独立环境: {self.python_exe}")
            if not self.worker_script.exists():
                raise RuntimeError(f"未找到情绪分析 worker 脚本: {self.worker_script}")

            self.process = await asyncio.create_subprocess_exec(
                str(self.python_exe),
                str(self.worker_script),
                cwd=str(self.project_root),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            self.stdout_task = asyncio.create_task(self._read_stdout())
            self.stderr_task = asyncio.create_task(self._read_stderr())
            print(f"[情绪] DeepFace worker 已启动，PID={self.process.pid}")

    async def stop(self) -> None:
        process = self.process
        self.process = None
        if process is None:
            return

        if process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=5)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()

        for task in (self.stdout_task, self.stderr_task):
            if task is not None:
                task.cancel()

        pending = list(self.pending.values())
        self.pending.clear()
        for future in pending:
            if not future.done():
                future.set_exception(RuntimeError("情绪分析 worker 已关闭"))

    async def analyze_frame(self, image_base64: str, session_id: str) -> dict:
        await self.ensure_started()
        if self.process is None or self.process.stdin is None:
            raise RuntimeError("情绪分析 worker 未就绪")

        request_id = uuid.uuid4().hex
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        self.pending[request_id] = future

        payload = {
            "type": "analyze",
            "request_id": request_id,
            "session_id": session_id,
            "image": image_base64,
        }

        async with self.write_lock:
            self.process.stdin.write((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
            await self.process.stdin.drain()

        try:
            result = await asyncio.wait_for(future, timeout=self.response_timeout_seconds)
            return result
        finally:
            self.pending.pop(request_id, None)

    async def _read_stdout(self) -> None:
        assert self.process is not None and self.process.stdout is not None
        try:
            while True:
                line = await self.process.stdout.readline()
                if not line:
                    break

                text = line.decode("utf-8", errors="replace").strip()
                if not text:
                    continue

                try:
                    payload = json.loads(text)
                except json.JSONDecodeError:
                    print(f"[情绪] 无法解析 worker 输出: {text}")
                    continue

                message_type = payload.get("type")
                if message_type == "ready":
                    print("[情绪] DeepFace worker 已就绪")
                    continue

                if message_type != "result":
                    print(f"[情绪] 收到未知 worker 消息: {payload}")
                    continue

                request_id = str(payload.get("request_id", ""))
                future = self.pending.get(request_id)
                if future is None or future.done():
                    continue
                future.set_result(payload)
        finally:
            self._fail_all_pending(RuntimeError("情绪分析 worker 输出流已关闭"))

    async def _read_stderr(self) -> None:
        assert self.process is not None and self.process.stderr is not None
        while True:
            line = await self.process.stderr.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="replace").rstrip()
            if text:
                print(f"[情绪Worker] {text}")

    def _fail_all_pending(self, error: Exception) -> None:
        pending = list(self.pending.values())
        self.pending.clear()
        for future in pending:
            if not future.done():
                future.set_exception(error)


__all__ = ["EmotionWorkerClient"]
