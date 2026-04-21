# Wav2Lip 本地独立演示

这个目录是单独开的实验区，目的只有一个：

- **本地单独跑 Wav2Lip**
- **不影响你现在已经跑通的语音主链路**

## 当前思路

你仓库里已经有这些资源：

- 权重：`Wav2Lip/checkpoints/wav2lip_gan.pth`
- 示例音频：`Wav2Lip/inputs/audio.mp3`
- 示例视频：`Video/HeyGen.mp4`
- `ffmpeg.exe`：`Wav2Lip/ffmpeg.exe`

但目前缺一个常见的人脸检测权重：

- `Wav2Lip/face_detection/detection/sfd/s3fd.pth`

所以这里默认先走一个更稳的办法：

- **手动框选人脸**
- 然后把这个框保存下来
- 后续推理直接复用这个框

这样就不用改主项目，也不用先补齐 `s3fd.pth`。

## 文件说明

- `wav2lip_local_demo/run_demo.py`
  - 独立运行脚本
  - 可先手动选框，再执行推理
- `wav2lip_local_demo/demo_box.json`
  - 运行 `--select-box` 后自动生成
- `wav2lip_local_demo/output/`
  - 独立输出目录

## 第一步：先选脸框

在项目根目录打开 PowerShell：

```powershell
cd E:\PycharmDemo\Deepseek
python wav2lip_local_demo\run_demo.py --select-box
```

执行后会弹一个窗口：

- 框住整张脸
- 尤其把下巴也包含进去
- 按 `Enter` 确认
- 按 `C` 取消

选完后会生成：

- `wav2lip_local_demo/demo_box.json`

## 第二步：运行独立演示

```powershell
cd E:\PycharmDemo\Deepseek
python wav2lip_local_demo\run_demo.py
```

默认使用：

- 人脸视频：`Video/HeyGen.mp4`
- 音频：`Wav2Lip/inputs/audio.mp3`
- 输出：`wav2lip_local_demo/output/wav2lip_demo.mp4`

## 常用命令

### 1. 重新选框

```powershell
python wav2lip_local_demo\run_demo.py --select-box
```

### 2. 清除旧框

```powershell
python wav2lip_local_demo\run_demo.py --clear-box
```

### 3. 指定别的音频

```powershell
python wav2lip_local_demo\run_demo.py --audio E:\你的音频.mp3
```

### 4. 指定别的视频或图片

```powershell
python wav2lip_local_demo\run_demo.py --face E:\你的视频.mp4
```

或：

```powershell
python wav2lip_local_demo\run_demo.py --face E:\你的头像.png
```

## 如果你后面补齐了 `s3fd.pth`

把文件放到：

- `Wav2Lip/face_detection/detection/sfd/s3fd.pth`

然后可以强制使用 Wav2Lip 自带检测：

```powershell
python wav2lip_local_demo\run_demo.py --force-detector
```

## 注意

- 这个目录是独立实验区，不会改你当前主项目逻辑。
- 这一步的目标只是先把 **Wav2Lip 本地演示跑通**。
- 后面如果你要接回网页或主后端，再单独整合。
