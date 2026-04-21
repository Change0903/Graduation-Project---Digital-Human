# OpenCV Zoo ONNX 情绪识别独立测试

这个目录用于单独测试 OpenCV Zoo / Progressive Teacher 的 ONNX 表情识别模型。

它不影响当前主程序：

- 不改 `server-2.py`
- 不改网页
- 不改 DeepFace worker

## 模型来源

表情识别模型：

- `opencv/facial_expression_recognition`
- https://huggingface.co/opencv/facial_expression_recognition

人脸检测模型：

- `opencv/face_detection_yunet`
- https://huggingface.co/opencv/face_detection_yunet

## 第一步：下载模型

在项目根目录执行：

```powershell
cd E:\PycharmDemo\Deepseek
.\.venv-deepface\Scripts\python.exe .\emotion_onnx_demo\download_models.py
```

下载后模型会放到：

```text
emotion_onnx_demo\models
```

## 第二步：启动摄像头测试

```powershell
cd E:\PycharmDemo\Deepseek
.\.venv-deepface\Scripts\python.exe .\emotion_onnx_demo\run_camera.py
```

启动后：

- 左侧是摄像头
- 右侧是情绪占比面板
- 按 `q` 退出

## 情绪类别

模型输出 7 类：

- angry
- disgust
- fear
- happy
- neutral
- sad
- surprise

## 为什么先独立测试

现在 DeepFace 不准，所以先不直接替换主项目。

正确流程是：

1. 先独立跑 ONNX 模型
2. 你肉眼判断它是否比 DeepFace 准
3. 如果更准，再接入网页

