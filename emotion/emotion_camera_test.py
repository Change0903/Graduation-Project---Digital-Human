from collections import deque
import time

import cv2

from deepface_runtime import load_deepface
from realtime_emotion import EMOTIONS, EmotionAnalyzer, build_empty_scores, dominant_emotion


WINDOW_NAME = "DeepFace Emotion Live"
ANALYZE_INTERVAL_SECONDS = 0.20
SMOOTHING_ALPHA = 0.55
PANEL_WIDTH = 260
NO_FACE_GRACE_SECONDS = 1.0
SCORE_HISTORY_SIZE = 5
REGION_HISTORY_SIZE = 3


def smooth_scores(
    previous_scores: dict[str, float],
    current_scores: dict[str, float],
    alpha: float,
) -> dict[str, float]:
    blended: dict[str, float] = {}
    for emotion in EMOTIONS:
        previous = previous_scores.get(emotion, 0.0)
        current = current_scores.get(emotion, 0.0)
        blended[emotion] = previous * (1.0 - alpha) + current * alpha
    return blended


def average_scores(score_history: deque[dict[str, float]]) -> dict[str, float]:
    if not score_history:
        return build_empty_scores()

    averaged = build_empty_scores()
    for scores in score_history:
        for emotion in EMOTIONS:
            averaged[emotion] += scores.get(emotion, 0.0)

    history_size = float(len(score_history))
    for emotion in EMOTIONS:
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


def draw_face_box(frame, region: dict[str, int], label: str, face_confidence: float) -> None:
    x = region["x"]
    y = region["y"]
    w = region["w"]
    h = region["h"]

    cv2.rectangle(frame, (x, y), (x + w, y + h), (47, 184, 255), 2)
    cv2.putText(
        frame,
        f"{label}  ({face_confidence:.2f})",
        (x, max(30, y - 10)),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.7,
        (47, 184, 255),
        2,
    )


def draw_emotion_panel(frame, region: dict[str, int], scores: dict[str, float]) -> None:
    x = region["x"]
    y = region["y"]
    w = region["w"]
    h = region["h"]

    panel_height = 44 + len(EMOTIONS) * 26
    panel_x = x + w + 12
    panel_y = y

    if panel_x + PANEL_WIDTH > frame.shape[1]:
        panel_x = max(12, x - PANEL_WIDTH - 12)

    if panel_y + panel_height > frame.shape[0]:
        panel_y = max(12, frame.shape[0] - panel_height - 12)

    overlay = frame.copy()
    cv2.rectangle(
        overlay,
        (panel_x, panel_y),
        (panel_x + PANEL_WIDTH, panel_y + panel_height),
        (24, 28, 36),
        cv2.FILLED,
    )
    cv2.addWeighted(overlay, 0.55, frame, 0.45, 0, frame)

    top_label = dominant_emotion(scores)
    cv2.putText(
        frame,
        f"Top: {top_label}",
        (panel_x + 12, panel_y + 22),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.65,
        (255, 255, 255),
        2,
    )

    sorted_scores = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    bar_left = panel_x + 92
    bar_max_width = PANEL_WIDTH - 110

    for index, (emotion, score) in enumerate(sorted_scores):
        row_y = panel_y + 48 + index * 26
        bar_width = int(bar_max_width * max(0.0, min(score, 100.0)) / 100.0)
        bar_color = (47, 184, 255) if index == 0 else (220, 220, 220)

        cv2.putText(
            frame,
            f"{emotion:<8}",
            (panel_x + 12, row_y),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            (255, 255, 255),
            1,
        )
        cv2.rectangle(
            frame,
            (bar_left, row_y - 10),
            (bar_left + bar_max_width, row_y - 3),
            (70, 78, 92),
            cv2.FILLED,
        )
        cv2.rectangle(
            frame,
            (bar_left, row_y - 10),
            (bar_left + bar_width, row_y - 3),
            bar_color,
            cv2.FILLED,
        )
        cv2.putText(
            frame,
            f"{score:5.1f}%",
            (panel_x + PANEL_WIDTH - 64, row_y),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.46,
            (255, 255, 255),
            1,
        )


def draw_status(frame, text: str, color: tuple[int, int, int]) -> None:
    cv2.rectangle(frame, (12, 12), (390, 86), (20, 20, 20), cv2.FILLED)
    cv2.putText(
        frame,
        text,
        (24, 39),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.65,
        color,
        2,
    )


def main() -> None:
    deepface = load_deepface()
    analyzer = EmotionAnalyzer(deepface)
    print("正在预热 Emotion 模型...")
    deepface.build_model(task="facial_attribute", model_name="Emotion")

    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        raise SystemExit("摄像头打开失败，请确认摄像头没有被别的程序占用。")
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

    print("实时情绪占比面板已启动。按 q 退出。")
    print("当前人脸检测器: haarcascade_frontalface_default.xml")

    last_analyze_time = 0.0
    last_face_seen_time = 0.0
    last_quality_reason = "waiting"
    last_quality_metrics = ""
    score_history: deque[dict[str, float]] = deque(maxlen=SCORE_HISTORY_SIZE)
    region_history: deque[dict[str, int]] = deque(maxlen=REGION_HISTORY_SIZE)
    smoothed_scores = build_empty_scores()
    latest_region: dict[str, int] | None = None
    latest_face_confidence = 0.0
    latest_backend = ""
    last_printed_label = ""

    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                print("读取摄像头画面失败，已停止。")
                break

            now = time.time()
            if now - last_analyze_time >= ANALYZE_INTERVAL_SECONDS:
                result = analyzer.analyze_frame(frame)
                if result is not None:
                    last_face_seen_time = now
                    latest_region = result["region"]
                    latest_face_confidence = result["confidence"]
                    region_history.append(latest_region)
                    averaged_region = average_regions(region_history)
                    if averaged_region is not None:
                        latest_region = averaged_region

                    last_quality_reason = str(result["quality_reason"])
                    last_quality_metrics = (
                        f"b={result['brightness']:.0f} s={result['sharpness']:.0f}"
                    )

                    current_scores = result.get("scores")
                    if current_scores is not None:
                        latest_backend = str(result["backend"])
                        score_history.append(current_scores)
                        averaged_scores = average_scores(score_history)
                        smoothed_scores = smooth_scores(smoothed_scores, averaged_scores, SMOOTHING_ALPHA)
                        current_label = dominant_emotion(smoothed_scores)
                        if current_label != last_printed_label:
                            print(
                                f"当前主情绪: {current_label} | detector={latest_backend} | {last_quality_metrics}"
                            )
                            last_printed_label = current_label
                else:
                    if now - last_face_seen_time > NO_FACE_GRACE_SECONDS:
                        latest_region = None
                        score_history.clear()
                        region_history.clear()
                last_analyze_time = now

            display = frame.copy()
            if latest_region is None:
                draw_status(display, "No face detected", (90, 200, 255))
            else:
                top_label = dominant_emotion(smoothed_scores)
                draw_face_box(display, latest_region, top_label, latest_face_confidence)
                draw_emotion_panel(display, latest_region, smoothed_scores)
                status_color = (90, 255, 160) if last_quality_reason == "ok" else (90, 220, 255)
                draw_status(
                    display,
                    f"Live emotion | top={top_label} | {latest_backend}",
                    status_color,
                )
                cv2.putText(
                    display,
                    f"quality={last_quality_reason} {last_quality_metrics}",
                    (24, 74),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.55,
                    (225, 225, 225),
                    1,
                )

            cv2.imshow(WINDOW_NAME, display)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break
    finally:
        cap.release()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
