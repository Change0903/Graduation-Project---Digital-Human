# AutoDL Launcher

This project includes a terminal launcher for headless AutoDL environments:

```bash
cd /root/autodl-tmp/Deepseek
python autodl_launcher.py check
python autodl_launcher.py run
```

Common commands:

```bash
python autodl_launcher.py run
python autodl_launcher.py start
python autodl_launcher.py status
python autodl_launcher.py logs --follow
python autodl_launcher.py stop
```

Notes:

- `run` starts backend and frontend in the current terminal and prints both logs with timestamps.
- `start` runs both services in the background and writes logs under `.autodl_launcher/logs/`.
- `check` verifies paths, Python, ports, and `OPENROUTER_API_KEY`.
- If `OPENROUTER_API_KEY` is not already exported in the shell, you can place it in `.env.autodl`:

```bash
OPENROUTER_API_KEY=your_real_key
```
