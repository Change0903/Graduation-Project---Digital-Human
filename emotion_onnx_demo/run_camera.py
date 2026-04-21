from __future__ import annotations

import argparse
import time
from collections import deque
from pathlib import Path

import cv2
import numpy as np

import sys


BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent
EMOTION_DIR = PROJECT_ROOT / "emotion"
if str(EMOTION_DIR) not in sys.path:
    sys.path.insert(0, str(EMOTION_DIR))

from onnx_emotion_analyzer import (
    EMOTIONS,
    ONNXEmotionAnalyzer,
    build_empty_scores,
    dominant_emotion,
)

MODEL_DIR = BASE_DIR / "models"
FACE_MODEL = MODEL_DIR / "face_detection_yunet_2023mar.onnx"
EMOTION_MODEL = MODEL_DIR / "facial_expression_recognition_mobilefacenet_2022july.onnx"

WINDOW_NAME = "OpenCV Zoo Emotion ONNX Demo"
PANEL_WIDTH = 360
ANALYZE_INTERVAL_SECONDS = 0.12
HISTORY_SIZE = 5


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="独立测试 OpenCV Zoo / Progressive Teacher ONNX 表情识别。"
    )
    parser.add_argument("--camera", type=int, default=0, help="摄像头编号，默认 0")
    parser.add_argument("--width", type=int, default=640, help="摄像头宽度")
    parser.add_argument("--height", type=int, default=480, help="摄像头高度")
    return parser.parse_args()


def ensure_models() -> None:
    missing = [path for path in (FACE_MODEL, EMOTION_MODEL) if not path.exists()]
    if missing:
        missing_text = "\n".join(str(path) for path in missing)
        raise FileNotFoundError(
            "缺少 ONNX 模型文件，请先运行：\n"
            ".\\.venv-deepface\\Scripts\\python.exe .\\emotion_onnx_demo\\download_models.py\n"
            f"缺失文件：\n{missing_text}"
        )


def open_camera(camera_index: int, width: int, height: int):
    backends = [
        ("DSHOW", cv2.CAP_DSHOW),
        ("MSMF", cv2.CAP_MSMF),
        ("DEFAULT", 0),
    ]

    for backend_name, backend in backends:
        cap = cv2.VideoCapture(camera_index, backend)
        if not cap.isOpened():
            cap.release()
            continue
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        ok, frame = cap.read()
        if ok:
            print(f"摄像头已打开: index={camera_index}, backend={backend_name}")
            return cap
        cap.release()

    raise RuntimeError("摄像头打开失败，请确认没有被浏览器或其他程序占用。")


def average_scores(history: deque[dict[str, float]]) -> dict[str, float]:
    scores = {emotion: 0.0 for emotion in EMOTIONS}
    if not history:
        return scores
    for item in history:
        for emotion in EMOTIONS:
            scores[emotion] += item.get(emotion, 0.0)
    for emotion in EMOTIONS:
        scores[emotion] /= len(history)
    return scores


def draw_text(image, text: str, x: int, y: int, color=(255, 255, 255), scale=0.55) -> None:
    cv2.putText(image, text, (x, y), cv2.FONT_HERSHEY_SIMPLEX, scale, color, 1, cv2.LINE_AA)


def make_panel(scores: dict[str, float], fps: float) -> np.ndarray:
    panel = np.zeros((480, PANEL_WIDTH, 3), dtype=np.uint8)
    panel[:, :] = (24, 26, 32)

    sorted_scores = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    top = sorted_scores[0][0] if sorted_scores else "-"

    draw_text(panel, "OpenCV Zoo Emotion", 18, 34, (90, 220, 255), 0.7)
    draw_text(panel, f"top: {top}", 18, 66, (255, 230, 120), 0.65)
    draw_text(panel, f"fps: {fps:.1f}", 210, 66, (180, 220, 255), 0.55)
    draw_text(panel, "q quit", 18, 94, (220, 220, 220), 0.55)

    y = 138
    bar_left = 112
    bar_width = 190
    for emotion, value in sorted_scores:
        draw_text(panel, f"{emotion:<8}", 18, y, (255, 255, 255), 0.55)
        cv2.rectangle(panel, (bar_left, y - 12), (bar_left + bar_width, y - 4), (70, 78, 92), cv2.FILLED)
        fill = int(bar_width * max(0.0, min(value, 100.0)) / 100.0)
        color = (80, 220, 255) if emotion == top else (210, 210, 210)
        cv2.rectangle(panel, (bar_left, y - 12), (bar_left + fill, y - 4), color, cv2.FILLED)
        draw_text(panel, f"{value:5.1f}%", 306, y, (255, 255, 255), 0.48)
        y += 36

    return panel


def compose_display(frame, panel) -> np.ndarray:
    frame_h, frame_w = frame.shape[:2]
    panel_h, panel_w = panel.shape[:2]
    canvas_h = max(frame_h, panel_h)
    canvas_w = frame_w + panel_w
    canvas = np.zeros((canvas_h, canvas_w, 3), dtype=np.uint8)
    canvas[:, :] = (58, 61, 66)
    canvas[0:frame_h, 0:frame_w] = frame
    canvas[0:panel_h, frame_w : frame_w + panel_w] = panel
    return canvas


def draw_region(frame, region: dict[str, int], scores: dict[str, float]) -> None:
    x = int(region["x"])
    y = int(region["y"])
    w = int(region["w"])
    h = int(region["h"])
    x1, y1 = max(0, x), max(0, y)
    x2, y2 = max(0, x + w), max(0, y + h)
    top = max(scores, key=scores.get) if scores else "-"
    cv2.rectangle(frame, (x1, y1), (x2, y2), (80, 220, 255), 2)
    draw_text(frame, top, x1, max(28, y1 - 8), (80, 220, 255), 0.7)


def main() -> None:
    args = parse_args()
    ensure_models()

    analyzer = ONNXEmotionAnalyzer(MODEL_DIR)

    cap = open_camera(args.camera, args.width, args.height)
    score_history: deque[dict[str, float]] = deque(maxlen=HISTORY_SIZE)
    latest_region: dict[str, int] | None = None
    latest_scores = build_empty_scores()
    last_analyze_time = 0.0
    last_frame_time = time.perf_counter()
    fps = 0.0

    print("ONNX 情绪识别已启动。按 q 退出。")

    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                print("读取摄像头失败。")
                break

            now = time.perf_counter()
            delta = now - last_frame_time
            if delta > 0:
                fps = fps * 0.9 + (1.0 / delta) * 0.1
            last_frame_time = now

            if now - last_analyze_time >= ANALYZE_INTERVAL_SECONDS:
                result = analyzer.analyze_frame(frame)
                if result is not None and result.get("scores") is not None:
                    current_scores = result["scores"]
                    score_history.append(current_scores)
                    latest_scores = average_scores(score_history)
                    latest_region = result["region"]
                else:
                    latest_region = None
                    score_history.clear()
                    latest_scores = build_empty_scores()
                last_analyze_time = now

            display = frame.copy()
            if latest_region is not None:
                draw_region(display, latest_region, latest_scores)
            else:
                draw_text(display, "No face detected", 18, 34, (90, 220, 255), 0.7)

            panel = make_panel(latest_scores, fps)
            canvas = compose_display(display, panel)
            cv2.imshow(WINDOW_NAME, canvas)

            key = cv2.waitKey(1) & 0xFF
            if key == ord("q"):
                break
    finally:
        cap.release()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
