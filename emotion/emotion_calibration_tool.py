from __future__ import annotations

import time
from collections import deque

import cv2
import numpy as np

from deepface_runtime import load_deepface
from emotion_calibration import (
    DEFAULT_PROFILE_PATH,
    DEFAULT_SAMPLES_PATH,
    EmotionCalibrator,
    apply_manual_weights,
    append_sample,
    fit_profile,
    load_samples,
    sample_counts,
)
from realtime_emotion import EMOTIONS, EmotionAnalyzer, build_empty_scores, dominant_emotion


ANALYZE_INTERVAL_SECONDS = 0.25
SCORE_HISTORY_SIZE = 4
SMOOTHING_ALPHA = 0.55
WINDOW_NAME = "Emotion Calibration Tool"
PANEL_WIDTH = 520
PANEL_HEIGHT = 300

KEY_LABELS = {
    ord("1"): "happy",
    ord("2"): "neutral",
    ord("3"): "surprise",
    ord("4"): "sad",
    ord("5"): "angry",
    ord("6"): "fear",
    ord("7"): "disgust",
}


def open_camera(camera_index: int = 0):
    """依次尝试 Windows 上更稳定的摄像头后端。"""
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

        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

        ok, _frame = cap.read()
        if ok:
            print(f"摄像头已打开: index={camera_index}, backend={backend_name}")
            return cap

        cap.release()

    raise RuntimeError(
        "摄像头打开失败。请确认浏览器、Edge、启动器或其他程序没有占用摄像头。"
    )


def smooth_scores(previous: dict[str, float], current: dict[str, float]) -> dict[str, float]:
    blended = build_empty_scores()
    for emotion in EMOTIONS:
        blended[emotion] = previous.get(emotion, 0.0) * (1.0 - SMOOTHING_ALPHA) + current.get(
            emotion, 0.0
        ) * SMOOTHING_ALPHA
    return blended


def average_scores(history: deque[dict[str, float]]) -> dict[str, float]:
    if not history:
        return build_empty_scores()

    averaged = build_empty_scores()
    for scores in history:
        for emotion in EMOTIONS:
            averaged[emotion] += scores.get(emotion, 0.0)
    for emotion in EMOTIONS:
        averaged[emotion] /= len(history)
    return averaged


def draw_text(frame, text: str, x: int, y: int, color=(255, 255, 255), scale=0.55) -> None:
    cv2.putText(frame, text, (x, y), cv2.FONT_HERSHEY_SIMPLEX, scale, color, 1, cv2.LINE_AA)


def make_side_panel(
    raw_scores: dict[str, float],
    calibrated_scores: dict[str, float],
    counts: dict[str, int],
    quality: str,
) -> object:
    panel = np.zeros((PANEL_HEIGHT, PANEL_WIDTH, 3), dtype=np.uint8)
    panel[:, :] = (24, 26, 32)

    draw_text(panel, "Emotion Calibration Tool", 18, 30, (90, 220, 255), 0.68)
    draw_text(panel, "1 happy | 2 neutral | 3 surprise | 4 sad", 18, 58)
    draw_text(panel, "5 angry | 6 fear | 7 disgust | s save | c reload | q quit", 18, 82)

    raw_top = dominant_emotion(raw_scores)
    calibrated_top = dominant_emotion(calibrated_scores)
    draw_text(panel, f"raw top: {raw_top}", 18, 116, (255, 230, 120))
    draw_text(panel, f"fixed top: {calibrated_top}", 250, 116, (120, 255, 170))
    draw_text(panel, f"quality: {quality}", 18, 142, (220, 220, 220))

    y = 172
    for emotion in EMOTIONS:
        raw_value = raw_scores.get(emotion, 0.0)
        calibrated_value = calibrated_scores.get(emotion, 0.0)
        count = counts.get(emotion, 0)
        draw_text(
            panel,
            f"{emotion:<8} raw={raw_value:5.1f}%  fixed={calibrated_value:5.1f}%  samples={count}",
            18,
            y,
        )
        y += 18

    return panel


def compose_display(frame, panel):
    frame_h, frame_w = frame.shape[:2]
    panel_h, panel_w = panel.shape[:2]
    canvas_h = max(frame_h, panel_h)
    canvas_w = frame_w + panel_w
    canvas = np.zeros((canvas_h, canvas_w, 3), dtype=np.uint8)
    canvas[:, :] = (58, 61, 66)
    canvas[0:frame_h, 0:frame_w] = frame
    canvas[0:panel_h, frame_w : frame_w + panel_w] = panel
    return canvas


def print_help() -> None:
    print("情绪校准工具已启动。")
    print("按键说明：")
    print("1=happy, 2=neutral, 3=surprise, 4=sad, 5=angry, 6=fear, 7=disgust")
    print("s=根据已采样数据生成校准文件")
    print("c=重新加载校准文件")
    print("fixed 会默认压低 neutral，避免一直被判成 neutral")
    print("q=退出")
    print(f"采样文件: {DEFAULT_SAMPLES_PATH}")
    print(f"校准文件: {DEFAULT_PROFILE_PATH}")


def main() -> None:
    print_help()
    deepface = load_deepface()
    analyzer = EmotionAnalyzer(deepface)
    calibrator = EmotionCalibrator.load(DEFAULT_PROFILE_PATH)

    print("正在预热 DeepFace Emotion 模型...")
    deepface.build_model(task="facial_attribute", model_name="Emotion")
    print("预热完成。")

    cap = open_camera(0)

    score_history: deque[dict[str, float]] = deque(maxlen=SCORE_HISTORY_SIZE)
    smoothed_scores = build_empty_scores()
    calibrated_scores = build_empty_scores()
    last_analyze_time = 0.0
    latest_region: dict[str, int] | None = None
    latest_quality = "waiting"
    counts = sample_counts(load_samples(DEFAULT_SAMPLES_PATH))

    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                print("读取摄像头失败。")
                break

            now = time.time()
            if now - last_analyze_time >= ANALYZE_INTERVAL_SECONDS:
                result = analyzer.analyze_frame(frame)
                last_analyze_time = now

                if result is not None:
                    latest_region = result["region"]
                    latest_quality = str(result["quality_reason"])
                    current_scores = result.get("scores")
                    if current_scores is not None:
                        score_history.append(current_scores)
                        averaged = average_scores(score_history)
                        smoothed_scores = smooth_scores(smoothed_scores, averaged)
                        calibrated_scores = calibrator.apply(smoothed_scores)
                    else:
                        calibrated_scores = apply_manual_weights(smoothed_scores)
                else:
                    latest_region = None
                    latest_quality = "no_face"

            display = frame.copy()

            if latest_region is not None:
                x = latest_region["x"]
                y = latest_region["y"]
                w = latest_region["w"]
                h = latest_region["h"]
                cv2.rectangle(display, (x, y), (x + w, y + h), (80, 220, 255), 2)

            panel = make_side_panel(smoothed_scores, calibrated_scores, counts, latest_quality)
            canvas = compose_display(display, panel)
            cv2.imshow(WINDOW_NAME, canvas)
            key = cv2.waitKey(1) & 0xFF

            if key == ord("q"):
                break
            if key == ord("s"):
                profile = fit_profile(DEFAULT_SAMPLES_PATH, DEFAULT_PROFILE_PATH)
                calibrator = EmotionCalibrator(profile)
                counts = sample_counts(load_samples(DEFAULT_SAMPLES_PATH))
                print(f"已生成校准文件: {DEFAULT_PROFILE_PATH}")
                print(f"各类样本数: {profile['sample_counts']}")
                continue
            if key == ord("c"):
                calibrator = EmotionCalibrator.load(DEFAULT_PROFILE_PATH)
                print(f"已重新加载校准文件: {DEFAULT_PROFILE_PATH}")
                continue
            if key in KEY_LABELS:
                if latest_quality != "ok":
                    print(f"当前画面质量不适合采样: {latest_quality}")
                    continue
                label = KEY_LABELS[key]
                append_sample(DEFAULT_SAMPLES_PATH, label, smoothed_scores)
                counts = sample_counts(load_samples(DEFAULT_SAMPLES_PATH))
                print(f"已采样: {label} | 当前样本数: {counts[label]}")
    finally:
        cap.release()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
