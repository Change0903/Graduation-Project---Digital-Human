from __future__ import annotations

from typing import Any

import cv2


EMOTIONS = ["happy", "neutral", "surprise", "sad", "angry", "fear", "disgust"]


def build_empty_scores() -> dict[str, float]:
    return {emotion: 0.0 for emotion in EMOTIONS}


def normalize_scores(emotions: dict[str, Any]) -> dict[str, float]:
    normalized = build_empty_scores()
    for emotion in EMOTIONS:
        normalized[emotion] = float(emotions.get(emotion, 0.0))
    return normalized


def dominant_emotion(scores: dict[str, float]) -> str:
    return max(scores, key=scores.get)


class EmotionAnalyzer:
    def __init__(
        self,
        deepface: Any,
        min_face_size: int = 80,
        min_face_brightness: float = 45.0,
        max_face_brightness: float = 210.0,
        min_face_sharpness: float = 35.0,
    ) -> None:
        self.deepface = deepface
        self.min_face_size = min_face_size
        self.min_face_brightness = min_face_brightness
        self.max_face_brightness = max_face_brightness
        self.min_face_sharpness = min_face_sharpness
        self.face_cascade = cv2.CascadeClassifier(
            cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        )

    def expand_region(self, region: dict[str, int], frame_shape) -> dict[str, int]:
        frame_height, frame_width = frame_shape[:2]
        x = region["x"]
        y = region["y"]
        w = region["w"]
        h = region["h"]

        pad_x = int(w * 0.12)
        pad_y = int(h * 0.18)

        x1 = max(0, x - pad_x)
        y1 = max(0, y - pad_y)
        x2 = min(frame_width, x + w + pad_x)
        y2 = min(frame_height, y + h + pad_y)

        return {
            "x": x1,
            "y": y1,
            "w": max(1, x2 - x1),
            "h": max(1, y2 - y1),
        }

    def detect_primary_face(self, frame) -> tuple[dict[str, int], float] | None:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray = cv2.equalizeHist(gray)

        faces = self.face_cascade.detectMultiScale(
            gray,
            scaleFactor=1.1,
            minNeighbors=5,
            minSize=(self.min_face_size, self.min_face_size),
        )

        if len(faces) == 0:
            return None

        x, y, w, h = max(faces, key=lambda item: item[2] * item[3])
        region = {"x": int(x), "y": int(y), "w": int(w), "h": int(h)}
        region = self.expand_region(region, frame.shape)
        return region, 1.0

    def measure_face_quality(self, face_crop) -> tuple[float, float]:
        gray = cv2.cvtColor(face_crop, cv2.COLOR_BGR2GRAY)
        brightness = float(gray.mean())
        sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        return brightness, sharpness

    def quality_reason(self, brightness: float, sharpness: float) -> str:
        if brightness < self.min_face_brightness:
            return "too_dark"
        if brightness > self.max_face_brightness:
            return "too_bright"
        if sharpness < self.min_face_sharpness:
            return "too_blurry"
        return "ok"

    def refine_face_crop(self, face_crop):
        try:
            refined_faces = self.deepface.extract_faces(
                img_path=face_crop,
                detector_backend="opencv",
                enforce_detection=True,
                align=True,
                color_face="bgr",
                normalize_face=False,
                max_faces=1,
            )
        except Exception:
            return face_crop, "haar"

        if not refined_faces:
            return face_crop, "haar"

        return refined_faces[0]["face"], "haar+opencv"

    def analyze_frame(self, frame) -> dict[str, Any] | None:
        detected_face = self.detect_primary_face(frame)
        if detected_face is None:
            return None

        region, confidence = detected_face
        x = region["x"]
        y = region["y"]
        w = region["w"]
        h = region["h"]
        face_crop = frame[y : y + h, x : x + w]
        if face_crop.size == 0:
            return None

        brightness, sharpness = self.measure_face_quality(face_crop)
        reason = self.quality_reason(brightness, sharpness)
        result_payload: dict[str, Any] = {
            "region": region,
            "confidence": confidence,
            "quality_reason": reason,
            "brightness": brightness,
            "sharpness": sharpness,
            "scores": None,
            "backend": "haar",
            "dominant_emotion": None,
        }
        if reason != "ok":
            return result_payload

        face_crop, backend_used = self.refine_face_crop(face_crop)

        result = self.deepface.analyze(
            img_path=face_crop,
            actions=["emotion"],
            detector_backend="skip",
            enforce_detection=False,
            silent=True,
        )
        if isinstance(result, list):
            result = result[0]

        scores = normalize_scores(result.get("emotion", {}))
        result_payload["scores"] = scores
        result_payload["backend"] = backend_used
        result_payload["dominant_emotion"] = dominant_emotion(scores)
        return result_payload
