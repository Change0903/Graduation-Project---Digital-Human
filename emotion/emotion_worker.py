from __future__ import annotations

import base64
import json
import sys
import time
from collections import deque

import cv2
import numpy as np

from deepface_runtime import load_deepface
from realtime_emotion import EmotionAnalyzer, build_empty_scores, dominant_emotion


ANALYZE_INTERVAL_SECONDS = 0.20
SMOOTHING_ALPHA = 0.55
NO_FACE_GRACE_SECONDS = 1.0
SCORE_HISTORY_SIZE = 5
REGION_HISTORY_SIZE = 3


class LiveEmotionSession:
    def __init__(self, deepface) -> None:
        self.deepface = deepface
        self.analyzer = EmotionAnalyzer(self.deepface)
        self.last_analyze_time = 0.0
        self.last_face_seen_time = 0.0
        self.last_quality_reason = "waiting"
        self.last_brightness = 0.0
        self.last_sharpness = 0.0
        self.latest_region: dict[str, int] | None = None
        self.latest_face_confidence = 0.0
        self.latest_backend = "haar"
        self.score_history: deque[dict[str, float]] = deque(maxlen=SCORE_HISTORY_SIZE)
        self.region_history: deque[dict[str, int]] = deque(maxlen=REGION_HISTORY_SIZE)
        self.smoothed_scores = build_empty_scores()

    def analyze_image(self, image_base64: str) -> dict:
        frame = decode_frame(image_base64)
        now = time.time()

        result = None
        if now - self.last_analyze_time >= ANALYZE_INTERVAL_SECONDS:
            result = self.analyzer.analyze_frame(frame)
            self.last_analyze_time = now

        if result is not None:
            self.last_face_seen_time = now
            self.latest_region = result["region"]
            self.latest_face_confidence = float(result["confidence"])
            self.region_history.append(self.latest_region)
            averaged_region = average_regions(self.region_history)
            if averaged_region is not None:
                self.latest_region = averaged_region

            self.last_quality_reason = str(result["quality_reason"])
            self.last_brightness = float(result["brightness"])
            self.last_sharpness = float(result["sharpness"])

            current_scores = result.get("scores")
            if current_scores is not None:
                self.latest_backend = str(result["backend"])
                self.score_history.append(current_scores)
                averaged_scores = average_scores(self.score_history)
                self.smoothed_scores = smooth_scores(
                    self.smoothed_scores,
                    averaged_scores,
                    SMOOTHING_ALPHA,
                )
        elif now - self.last_face_seen_time > NO_FACE_GRACE_SECONDS:
            self.latest_region = None
            self.latest_face_confidence = 0.0
            self.latest_backend = "haar"
            self.last_quality_reason = "no_face"
            self.last_brightness = 0.0
            self.last_sharpness = 0.0
            self.score_history.clear()
            self.region_history.clear()
            self.smoothed_scores = build_empty_scores()

        has_face = self.latest_region is not None
        dominant = dominant_emotion(self.smoothed_scores) if has_face else None

        return {
            "type": "result",
            "has_face": has_face,
            "region": self.latest_region,
            "scores": self.smoothed_scores,
            "dominant_emotion": dominant,
            "confidence": self.latest_face_confidence,
            "backend": self.latest_backend,
            "quality_reason": self.last_quality_reason,
            "brightness": self.last_brightness,
            "sharpness": self.last_sharpness,
        }


class LiveEmotionWorker:
    def __init__(self) -> None:
        self.deepface = load_deepface()
        self.sessions: dict[str, LiveEmotionSession] = {}

    def warmup(self) -> None:
        print("正在预热 DeepFace Emotion 模型...", file=sys.stderr, flush=True)
        self.deepface.build_model(task="facial_attribute", model_name="Emotion")
        print("DeepFace Emotion 模型预热完成", file=sys.stderr, flush=True)

    def analyze_image(self, session_id: str, image_base64: str) -> dict:
        session = self.sessions.setdefault(session_id, LiveEmotionSession(self.deepface))
        return session.analyze_image(image_base64)


def decode_frame(image_base64: str):
    raw = base64.b64decode(image_base64)
    array = np.frombuffer(raw, dtype=np.uint8)
    frame = cv2.imdecode(array, cv2.IMREAD_COLOR)
    if frame is None:
        raise ValueError("无法解码前端发送的摄像头图像")
    return frame


def smooth_scores(previous_scores: dict[str, float], current_scores: dict[str, float], alpha: float) -> dict[str, float]:
    blended: dict[str, float] = {}
    for emotion, previous in previous_scores.items():
        current = current_scores.get(emotion, 0.0)
        blended[emotion] = previous * (1.0 - alpha) + current * alpha
    return blended


def average_scores(score_history: deque[dict[str, float]]) -> dict[str, float]:
    if not score_history:
        return build_empty_scores()

    averaged = build_empty_scores()
    for scores in score_history:
        for emotion, value in scores.items():
            averaged[emotion] += value

    history_size = float(len(score_history))
    for emotion in averaged:
        averaged[emotion] /= history_size
    return averaged


def average_regions(region_history: deque[dict[str, int]]) -> dict[str, int] | None:
    if not region_history:
        return None

    return {
        "x": int(sum(region["x"] for region in region_history) / len(region_history)),
        "y": int(sum(region["y"] for region in region_history) / len(region_history)),
        "w": int(sum(region["w"] for region in region_history) / len(region_history)),
        "h": int(sum(region["h"] for region in region_history) / len(region_history)),
    }


def main() -> None:
    worker = LiveEmotionWorker()
    worker.warmup()
    print(json.dumps({"type": "ready"}, ensure_ascii=False), flush=True)

    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue

        request_id = ""
        try:
            payload = json.loads(line)
            request_id = str(payload.get("request_id", ""))
            message_type = payload.get("type")

            if message_type == "ping":
                response = {"type": "result", "request_id": request_id, "ok": True}
            elif message_type == "analyze":
                session_id = str(payload.get("session_id", "default"))
                image_base64 = str(payload.get("image", ""))
                if not image_base64:
                    raise ValueError("未收到图像数据")
                response = worker.analyze_image(session_id, image_base64)
                response["request_id"] = request_id
            else:
                raise ValueError(f"未知消息类型: {message_type}")
        except Exception as exc:
            response = {
                "type": "result",
                "request_id": request_id,
                "error": str(exc),
            }
            print(f"worker 处理失败: {exc}", file=sys.stderr, flush=True)

        print(json.dumps(response, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
