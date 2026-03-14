# AGENTS.md
> 说明：此文件为项目当前唯一的“说明与规范来源”。如有历史文档或旧说明，以本文件为准。

## 项目背景与目标
这是一个数字人项目，该项目的技术路线：该项目的前端交互基于网页实现，首先用户通过麦克风输入语音，通过ASR技术转换成文本，将该文本信息传给大模型API，大模型API回复的文本通过TTS技术转换成语音然后播放，最后配合数字人形象完成展示
该项目分为前端和后端部分其中
- 前端路径：E:\PycharmDemo\Deepseek\digital-human-web\web
- 后端路径：E:\PycharmDemo\Deepseek

## 任务要求
- 开发工具：VS code
- 沟通语言：中文
- 代码要求：
  - 我是一个代码小白，我希望在沟通时你能尽可能的使用通俗易懂的语言跟我沟通
  - 在注释方面，我希望能够有清晰的注释，没必要每行都注释，但是在实现一个模块作用下请务必重视
  - 为了程序的可维护性以及更新迭代，我希望你能把这个项目根据不同的功能分成多个好维护的模块或者说多个文件
  - 我希望你能够写好相应的测试，方便进行代码的调试。
  
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
  `E:\PycharmDemo\Deepseek\digital-human-web\web`
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

