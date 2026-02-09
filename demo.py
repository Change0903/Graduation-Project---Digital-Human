import cv2
import asyncio
import torch
import numpy as np
import sys
import os

# 添加 Wav2Lip 目录到路径
sys.path.insert(0, "E:/PycharmDemo/Deepseek/Wav2Lip")

from Wav2Lip.face_detection import FaceAlignment, LandmarksType
from Wav2Lip.models.wav2lip import Wav2Lip
import audio  # 使用 Wav2Lip 目录下的 audio.py

# 加载Wav2Lip模型
device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
print(f"Using device: {device}")

# 确保Wav2Lip模型路径正确
model_path = "E:/PycharmDemo/Deepseek/Wav2Lip/checkpoints/wav2lip_gan.pth"
model = Wav2Lip().to(device)
checkpoint = torch.load(model_path, map_location=device)
state_dict = checkpoint["state_dict"]
new_state_dict = {k.replace('module.', ''): v for k, v in state_dict.items()}
model.load_state_dict(new_state_dict)
model.eval()
print("Wav2Lip model loaded successfully!")

IMG_SIZE = 96
mel_step_size = 16

def load_audio(audio_path):
    """加载音频并转换为梅尔频谱图，支持 MP3 和 WAV"""
    import subprocess

    ffmpeg_path = "E:/PycharmDemo/Deepseek/Wav2Lip/ffmpeg.exe"

    # 如果是 MP3，先转换为 WAV
    if audio_path.endswith('.mp3'):
        wav_path = audio_path.replace('.mp3', '_converted.wav')
        if not os.path.exists(wav_path):
            subprocess.run([ffmpeg_path, '-y', '-i', audio_path, '-ar', '16000', wav_path],
                          check=True, shell=True)
        audio_path = wav_path

    wav = audio.load_wav(audio_path, 16000)
    mel = audio.melspectrogram(wav)
    return mel

def get_windowed_frame(frame, face_coords):
    """根据检测到的人脸坐标裁剪并resize到96x96"""
    y1, y2, x1, x2 = face_coords
    face = frame[y1:y2, x1:x2]
    face = cv2.resize(face, (IMG_SIZE, IMG_SIZE))
    return face

def preprocess_frame(frame, face_coords):
    """预处理帧：裁剪人脸并创建masked图像"""
    face = get_windowed_frame(frame, face_coords)

    # 创建 masked 图像（下半部分为0）
    face_masked = face.copy()
    face_masked[IMG_SIZE//2:, :] = 0

    # 拼接 masked 和原图 (6通道)
    img_combined = np.concatenate([face_masked, face], axis=2)
    img_combined = img_combined.astype(np.float32) / 255.0

    # 转换为 (C, H, W) 格式
    img_combined = np.transpose(img_combined, (2, 0, 1))
    return img_combined, face

def sync_audio_video(audio_path, video_path, face_detector):
    """音频视频同步处理"""
    audio = load_audio(audio_path)
    mel_chunks = audio.shape[1]
    chunk_duration = mel_chunks * 80 / 16000  # 约等于音频时长

    # 打开视频
    video_cap = cv2.VideoCapture(video_path)
    fps = video_cap.get(cv2.CAP_PROP_FPS)

    mel_idx_multiplier = 80. / fps
    mel_chunks_list = []

    # 分割音频为 chunks
    i = 0
    while 1:
        start_idx = int(i * mel_idx_multiplier)
        if start_idx + mel_step_size > len(audio[0]):
            mel_chunks_list.append(audio[:, len(audio[0]) - mel_step_size:])
            break
        mel_chunks_list.append(audio[:, start_idx:start_idx + mel_step_size])
        i += 1

    print(f"Audio has {len(mel_chunks_list)} mel chunks")
    frame_idx = 0
    total_frames = int(video_cap.get(cv2.CAP_PROP_FRAME_COUNT))

    # 创建输出视频
    frame_w = int(video_cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    frame_h = int(video_cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    out = cv2.VideoWriter("E:/PycharmDemo/Deepseek/output.mp4",
                          cv2.VideoWriter_fourcc(*'mp4v'), fps, (frame_w, frame_h))

    for mel_chunk in mel_chunks_list:
        # 读取视频帧
        ret, frame = video_cap.read()
        if not ret:
            break

        # 检测人脸
        detections = face_detector.get_detections_for_batch(np.array([frame]))
        if detections[0] is None:
            print("No face detected, skipping frame")
            continue

        rect = detections[0]
        face_coords = (max(0, int(rect[1])), min(frame.shape[0], int(rect[3])),
                       max(0, int(rect[0])), min(frame.shape[1], int(rect[2])))

        # 预处理
        img_input, face_orig = preprocess_frame(frame, face_coords)

        # 转换为 tensor
        img_tensor = torch.FloatTensor(img_input).unsqueeze(0).to(device)
        # mel_chunk 形状是 (80, 16)，需要 reshape 为 (B, 1, 80, 16)
        mel_tensor = torch.FloatTensor(mel_chunk).unsqueeze(0).unsqueeze(1).to(device)

        # Wav2Lip 推理
        with torch.no_grad():
            pred = model(mel_tensor, img_tensor)

        # 处理输出
        pred = pred.squeeze(0).cpu().numpy()
        pred = np.transpose(pred, (1, 2, 0)) * 255.0
        pred = cv2.resize(pred.astype(np.uint8), (face_coords[3]-face_coords[2], face_coords[1]-face_coords[0]))

        # 将生成的嘴型区域合成到原图
        y1, y2, x1, x2 = face_coords
        frame[y1:y2, x1:x2] = pred
        out.write(frame)

        frame_idx += 1
        if frame_idx % 10 == 0:
            print(f"Processed {frame_idx}/{total_frames} frames")

    video_cap.release()
    out.release()
    print(f"Output saved to E:/PycharmDemo/Deepseek/output.mp4")

async def offer(pc, player):
    track = VideoAudioStreamTrack(player)
    pc.addTrack(track)

    # 对等连接的offer（WebRTC连接）
    offer = await pc.createOffer()
    await pc.setLocalDescription(offer)

    return offer

async def run():
    # 初始化人脸检测器
    print("Loading face detector...")
    face_detector = FaceAlignment(LandmarksType._2D, flip_input=False, device=str(device))

    # 音频和视频文件路径
    audio_path = "E:/PycharmDemo/Deepseek/Wav2Lip/inputs/audio.mp3"
    video_path = "E:/PycharmDemo/Deepseek/Video/HeyGen.mp4"

    # 检查文件是否存在
    if not os.path.exists(audio_path):
        raise FileNotFoundError(f"Audio file not found: {audio_path}")
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"Video file not found: {video_path}")

    print(f"Processing: {video_path} with audio: {audio_path}")

    # 执行唇形同步
    sync_audio_video(audio_path, video_path, face_detector)

    # WebRTC 部分（可选，用于实时传输）
    # player = MediaPlayer(video_path)
    # pc = RTCPeerConnection()
    # offer = await offer(pc, player)
    # print("Offer:", offer.sdp)
    # await pc.setRemoteDescription(RTCSessionDescription(sdp=offer.sdp, type="offer"))

if __name__ == "__main__":
    asyncio.run(run())
