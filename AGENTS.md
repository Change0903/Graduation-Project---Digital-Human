# AGENTS.md

## Project Summary
This repo is a local "digital human" demo that combines:
- LLM (OpenRouter DeepSeek via `openai` client)
- TTS (Edge TTS)
- Wav2Lip video lip-sync

Backend scripts run a WebSocket server and write generated media to a separate frontend project.

## Top-Level Layout
- `server.py`: WebSocket server on `ws://localhost:9999` that runs LLM -> TTS -> Wav2Lip video.
- `server-2.py`: Audio-only WebSocket server on `ws://localhost:9998` (faster, no video).
- `demo.py`: Offline Wav2Lip sync demo that combines `Video/HeyGen.mp4` and `Wav2Lip/inputs/audio.mp3` into `output.mp4`.
- `tts_comprehensive_test.py`: TTS/network diagnostics.
- `Wav2Lip/`: Wav2Lip source, weights, inputs, outputs, and `ffmpeg.exe`.
- `Video/`: sample input video.
- `.venv/`, `.idea/`, `.vscode/`, `.claude/`, `__pycache__/`: local environment/IDE caches; avoid editing.

## External Paths & Env
- Required env var: `OPENROUTER_API_KEY`.
- Frontend path hardcoded in `server.py` and `server-2.py`:
  `E:\PycharmDemo\digital-human-web\web`
  Generated media is written to `media/audio` and `media/video` under that path.

## Run Commands
```bash
# Full pipeline (audio + video)
python server.py

# Audio-only mode (faster)
python server-2.py

# Offline lip-sync demo
python demo.py
```

## Wav2Lip Notes
- Weights present: `Wav2Lip/checkpoints/wav2lip_gan.pth`.
- Face detection weights `face_detection/detection/sfd/s3fd.pth` are not in repo; Wav2Lip may require downloading them.
- `Wav2Lip/inputs/face.png` is used by `server.py` for inference.

## Docs
- `快速启动指南.md` and `性能优化方案.md` describe a "fast" backend, but `server_fast.py` / `server_optimized_fast.py` are not present in this repo.

## Generated Artifacts / Large Files
- Large binaries: `Wav2Lip/ffmpeg.exe`, `Wav2Lip/checkpoints/wav2lip_gan.pth`, `Video/HeyGen.mp4`, `output.mp4`.
- Generated outputs: `output.mp4`, `Wav2Lip/outputs/result.mp4`, files under `Wav2Lip/inputs/`.

## Tests
- No automated test suite found.

