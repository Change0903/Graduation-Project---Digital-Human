# DeepFace 独立模块

这个目录只用于 `DeepFace` 情绪识别实验，不影响现有语音主流程。

## 当前目录结构

- `deepface_runtime.py`
  - 统一设置 `DEEPFACE_HOME`，把模型缓存限制在项目目录内。
- `emotion_smoke_test.py`
  - 最小自检脚本。先验证导入，再验证单张图片情绪识别。
- `emotion_camera_test.py`
  - 摄像头测试脚本。实时显示当前主脸的 7 类情绪占比和柱状条。

## 当前环境

- 独立 Python: `tools/python310/python.exe`
- 独立虚拟环境: `.venv-deepface`

## 运行命令

先做导入自检：

```powershell
.\.venv-deepface\Scripts\python.exe .\emotion\emotion_smoke_test.py --self-check
```

再做单张图片测试：

```powershell
.\.venv-deepface\Scripts\python.exe .\emotion\emotion_smoke_test.py --image .\emotion\test.jpg
```

最后做摄像头测试：

```powershell
.\.venv-deepface\Scripts\python.exe .\emotion\emotion_camera_test.py
```

## 当前实时摄像头脚本特点

- 会先检测当前画面中的主脸，再只分析这张脸的情绪。
- 会把 `happy / neutral / surprise / sad / angry / fear / disgust` 的占比实时画到画面上。
- 已经加入平滑处理，所以结果不会像单帧判断那样剧烈跳变。
- 已经加入滑动平均、人脸质量筛选、二次精裁对齐，主要目标是提升实时面板的稳定性和准确率。

## VS Code 运行方式

已经新增专用调试配置：

- `Emotion Camera Test`
- `Emotion Smoke Test`

它们会固定使用 `.venv-deepface`，不会误用主项目的 Python。

## 注意

- 第一次分析时，DeepFace 可能会下载模型权重到 `emotion/.deepface/weights/`。
- 这一步仍然是独立实验阶段，还没有接入 `server-2.py` 或前端页面。
