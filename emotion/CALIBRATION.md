# 情绪识别校准说明

这个校准工具是独立实验工具，不会影响当前网页和主后端。

## 先说清楚

DeepFace 本身不是严格的微表情识别模型，它更像是“单帧人脸情绪分类”。

所以这里做的不是重新训练大模型，而是：

- 采集你自己的表情样本
- 记录 DeepFace 原始分数
- 生成一个个人校准文件
- 后续用这个文件把原始分数做矫正

这样能改善“对你这个人不准”的问题，但不能把 DeepFace 变成专业微表情模型。

## 文件

- `emotion_calibration_tool.py`
  - 摄像头采样和校准工具
- `emotion_calibration.py`
  - 校准算法模块
- `calibration_samples.jsonl`
  - 采样数据，运行后自动生成
- `calibration_profile.json`
  - 校准文件，按 `s` 保存后自动生成

## 运行

在项目根目录执行：

```powershell
cd E:\PycharmDemo\Deepseek
.\.venv-deepface\Scripts\python.exe .\emotion\emotion_calibration_tool.py
```

## 按键

- `1`：采样 happy
- `2`：采样 neutral
- `3`：采样 surprise
- `4`：采样 sad
- `5`：采样 angry
- `6`：采样 fear
- `7`：采样 disgust
- `s`：根据当前样本生成校准文件
- `c`：重新加载校准文件
- `q`：退出

## 怎么采样比较靠谱

每种情绪至少采 3 次，建议 5 到 10 次。

优先采这些：

- neutral：自然放松，不笑
- happy：自然微笑
- surprise：眼睛睁大，嘴稍微张开
- sad：嘴角下压，表情低落
- angry：眉头压低，嘴唇收紧

不建议一开始就大量采：

- fear
- disgust

这两个本来就很容易和 surprise / angry 混淆。

## 建议流程

1. 先采 `neutral` 5 次
2. 再采 `happy` 5 次
3. 再采 `sad`、`angry`、`surprise` 各 3 到 5 次
4. 按 `s` 生成校准文件
5. 看面板里的 `raw` 和 `fixed` 差异

如果 `fixed` 比 `raw` 更接近你的直觉，说明校准有效。

## 后续接入网页

当前工具只负责“采样和生成校准文件”。

等你确认效果明显改善后，再把 `calibration_profile.json` 接入：

- `emotion_worker.py`

这样网页里的实时情绪面板才会使用校准后的分数。

