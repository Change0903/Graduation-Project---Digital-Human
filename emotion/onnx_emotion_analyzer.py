from __future__ import annotations

from pathlib import Path
from typing import Any

import cv2
import numpy as np


EMOTIONS = ["angry", "disgust", "fear", "happy", "neutral", "sad", "surprise"]
FRONTEND_EMOTION_ORDER = ["happy", "neutral", "surprise", "sad", "angry", "fear", "disgust"]
REFERENCE_LANDMARKS = np.array(
    [
        [38.2946, 51.6963],
        [73.5318, 51.5014],
        [56.0252, 71.7366],
        [41.5493, 92.3655],
        [70.7299, 92.2041],
    ],
    dtype=np.float32,
)


def build_empty_scores() -> dict[str, float]:
    return {emotion: 0.0 for emotion in FRONTEND_EMOTION_ORDER}


def dominant_emotion(scores: dict[str, float]) -> str:
    return max(scores, key=scores.get)


def normalize_scores(scores: dict[str, Any]) -> dict[str, float]:
    normalized = build_empty_scores()
    total = 0.0
    for emotion in FRONTEND_EMOTION_ORDER:
        value = max(0.0, float(scores.get(emotion, 0.0)))
        normalized[emotion] = value
        total += value

    if total <= 0:
        return normalized

    return {emotion: value * 100.0 / total for emotion, value in normalized.items()}


def softmax(logits: np.ndarray) -> np.ndarray:
    values = logits.astype(np.float32).reshape(-1)
    values -= values.max()
    exp = np.exp(values)
    total = exp.sum()
    if total <= 0:
        return np.zeros_like(values)
    return exp / total


class ONNXEmotionAnalyzer:
    """OpenCV Zoo ONNX 表情识别器，用于替换 DeepFace 情绪分类。"""

    def __init__(
        self,
        model_dir: Path,
        score_threshold: float = 0.75,
    ) -> None:
        self.model_dir = Path(model_dir)
        self.face_model = self.model_dir / "face_detection_yunet_2023mar.onnx"
        self.emotion_model = self.model_dir / "facial_expression_recognition_mobilefacenet_2022july.onnx"
        self.score_threshold = score_threshold

        if not self.face_model.exists():
            raise FileNotFoundError(f"缺少 YuNet 人脸检测模型: {self.face_model}")
        if not self.emotion_model.exists():
            raise FileNotFoundError(f"缺少 ONNX 表情识别模型: {self.emotion_model}")

        self.detector = cv2.FaceDetectorYN.create(
            str(self.face_model),
            "",
            (320, 320),
            score_threshold=self.score_threshold,
            nms_threshold=0.3,
            top_k=5000,
        )
        self.emotion_net = cv2.dnn.readNet(str(self.emotion_model))

    def detect_primary_face(self, frame) -> np.ndarray | None:
        height, width = frame.shape[:2]
        self.detector.setInputSize((width, height))
        _retval, faces = self.detector.detect(frame)
        if faces is None or len(faces) == 0:
            return None
        return max(faces, key=lambda face: face[2] * face[3])

    def align_face(self, frame, face: np.ndarray):
        landmarks = face[4:14].reshape(5, 2).astype(np.float32)
        transform, _ = cv2.estimateAffinePartial2D(
            landmarks,
            REFERENCE_LANDMARKS,
            method=cv2.LMEDS,
        )
        if transform is None:
            x, y, w, h = face[:4].astype(int)
            crop = frame[max(0, y) : max(0, y + h), max(0, x) : max(0, x + w)]
            if crop.size == 0:
                return None
            return cv2.resize(crop, (112, 112))
        return cv2.warpAffine(frame, transform, (112, 112), borderValue=0.0)

    def preprocess_face(self, face_bgr: np.ndarray) -> np.ndarray:
        face_rgb = cv2.cvtColor(face_bgr, cv2.COLOR_BGR2RGB)
        face_rgb = face_rgb.astype(np.float32) / 255.0
        face_rgb = (face_rgb - 0.5) / 0.5
        blob = np.transpose(face_rgb, (2, 0, 1))[None, :, :, :]
        return blob.astype(np.float32)

    def predict_scores(self, aligned_face) -> dict[str, float]:
        self.emotion_net.setInput(self.preprocess_face(aligned_face))
        logits = self.emotion_net.forward()
        probabilities = softmax(logits) * 100.0
        raw_scores = {
            emotion: float(probabilities[index])
            for index, emotion in enumerate(EMOTIONS)
        }
        return {
            "happy": raw_scores["happy"],
            "neutral": raw_scores["neutral"],
            "surprise": raw_scores["surprise"],
            "sad": raw_scores["sad"],
            "angry": raw_scores["angry"],
            "fear": raw_scores["fear"],
            "disgust": raw_scores["disgust"],
        }

    def analyze_frame(self, frame) -> dict[str, Any] | None:
        face = self.detect_primary_face(frame)
        if face is None:
            return None

        x, y, w, h = face[:4].astype(int)
        region = {
            "x": max(0, int(x)),
            "y": max(0, int(y)),
            "w": max(1, int(w)),
            "h": max(1, int(h)),
        }
        confidence = float(face[14]) if len(face) > 14 else 1.0

        aligned_face = self.align_face(frame, face)
        if aligned_face is None:
            return {
                "region": region,
                "confidence": confidence,
                "quality_reason": "bad_crop",
                "brightness": 0.0,
                "sharpness": 0.0,
                "scores": None,
                "backend": "opencv_zoo_onnx",
                "dominant_emotion": None,
            }

        gray = cv2.cvtColor(aligned_face, cv2.COLOR_BGR2GRAY)
        brightness = float(gray.mean())
        sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        scores = normalize_scores(self.predict_scores(aligned_face))

        return {
            "region": region,
            "confidence": confidence,
            "quality_reason": "ok",
            "brightness": brightness,
            "sharpness": sharpness,
            "scores": scores,
            "backend": "opencv_zoo_onnx",
            "dominant_emotion": dominant_emotion(scores),
        }
