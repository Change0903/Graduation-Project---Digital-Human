from __future__ import annotations

import json
import math
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

from realtime_emotion import EMOTIONS, build_empty_scores, dominant_emotion


DEFAULT_PROFILE_PATH = Path(__file__).resolve().parent / "calibration_profile.json"
DEFAULT_SAMPLES_PATH = Path(__file__).resolve().parent / "calibration_samples.jsonl"
DEFAULT_MANUAL_WEIGHTS = {
    "happy": 1.0,
    "neutral": 0.55,
    "surprise": 1.15,
    "sad": 1.2,
    "angry": 1.2,
    "fear": 1.1,
    "disgust": 1.1,
}


def normalize_to_100(scores: dict[str, Any]) -> dict[str, float]:
    normalized = build_empty_scores()
    total = 0.0
    for emotion in EMOTIONS:
        value = max(0.0, float(scores.get(emotion, 0.0)))
        normalized[emotion] = value
        total += value

    if total <= 0:
        return normalized

    return {emotion: value * 100.0 / total for emotion, value in normalized.items()}


def score_distance(a: dict[str, float], b: dict[str, float]) -> float:
    total = 0.0
    for emotion in EMOTIONS:
        diff = a.get(emotion, 0.0) - b.get(emotion, 0.0)
        total += diff * diff
    return math.sqrt(total / len(EMOTIONS))


def apply_manual_weights(
    scores: dict[str, Any],
    weights: dict[str, float] | None = None,
) -> dict[str, float]:
    """用手动权重压低/提高某些情绪，默认压低 neutral。"""
    source = normalize_to_100(scores)
    active_weights = weights or DEFAULT_MANUAL_WEIGHTS
    weighted = build_empty_scores()

    for emotion in EMOTIONS:
        weighted[emotion] = source.get(emotion, 0.0) * float(active_weights.get(emotion, 1.0))

    return normalize_to_100(weighted)


def append_sample(samples_path: Path, label: str, scores: dict[str, Any]) -> None:
    if label not in EMOTIONS:
        raise ValueError(f"未知情绪标签: {label}")

    samples_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "label": label,
        "scores": normalize_to_100(scores),
        "raw_top": dominant_emotion(normalize_to_100(scores)),
    }
    with samples_path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(payload, ensure_ascii=False) + "\n")


def load_samples(samples_path: Path) -> list[dict[str, Any]]:
    if not samples_path.exists():
        return []

    samples: list[dict[str, Any]] = []
    for line in samples_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            sample = json.loads(line)
        except json.JSONDecodeError:
            continue
        if sample.get("label") in EMOTIONS and isinstance(sample.get("scores"), dict):
            samples.append(sample)
    return samples


def sample_counts(samples: list[dict[str, Any]]) -> dict[str, int]:
    counts = {emotion: 0 for emotion in EMOTIONS}
    for sample in samples:
        label = str(sample.get("label", ""))
        if label in counts:
            counts[label] += 1
    return counts


def fit_profile(
    samples_path: Path = DEFAULT_SAMPLES_PATH,
    profile_path: Path = DEFAULT_PROFILE_PATH,
    min_samples_per_emotion: int = 3,
) -> dict[str, Any]:
    samples = load_samples(samples_path)
    grouped: dict[str, list[dict[str, float]]] = defaultdict(list)

    for sample in samples:
        label = str(sample["label"])
        grouped[label].append(normalize_to_100(sample["scores"]))

    centroids: dict[str, dict[str, float]] = {}
    used_counts: dict[str, int] = {}
    for label, label_scores in grouped.items():
        if len(label_scores) < min_samples_per_emotion:
            continue

        centroid = build_empty_scores()
        for scores in label_scores:
            for emotion in EMOTIONS:
                centroid[emotion] += scores.get(emotion, 0.0)

        for emotion in EMOTIONS:
            centroid[emotion] /= len(label_scores)

        centroids[label] = normalize_to_100(centroid)
        used_counts[label] = len(label_scores)

    profile = {
        "version": 1,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "method": "nearest_centroid_blend",
        "raw_weight": 0.45,
        "calibrated_weight": 0.55,
        "manual_weights": DEFAULT_MANUAL_WEIGHTS,
        "min_samples_per_emotion": min_samples_per_emotion,
        "sample_counts": sample_counts(samples),
        "used_counts": used_counts,
        "centroids": centroids,
    }

    profile_path.parent.mkdir(parents=True, exist_ok=True)
    profile_path.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
    return profile


class EmotionCalibrator:
    def __init__(self, profile: dict[str, Any] | None = None) -> None:
        self.profile = profile or {}
        self.centroids: dict[str, dict[str, float]] = {
            label: normalize_to_100(scores)
            for label, scores in self.profile.get("centroids", {}).items()
            if label in EMOTIONS and isinstance(scores, dict)
        }
        self.raw_weight = float(self.profile.get("raw_weight", 0.45))
        self.calibrated_weight = float(self.profile.get("calibrated_weight", 0.55))
        self.manual_weights = self.profile.get("manual_weights", DEFAULT_MANUAL_WEIGHTS)

    @classmethod
    def load(cls, profile_path: Path = DEFAULT_PROFILE_PATH) -> "EmotionCalibrator":
        if not profile_path.exists():
            return cls()
        try:
            profile = json.loads(profile_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return cls()
        return cls(profile)

    def is_ready(self) -> bool:
        return len(self.centroids) >= 2

    def apply(self, scores: dict[str, Any]) -> dict[str, float]:
        raw = apply_manual_weights(scores, self.manual_weights)
        if not self.is_ready():
            return raw

        centroid_votes = build_empty_scores()
        for label, centroid in self.centroids.items():
            distance = score_distance(raw, centroid)
            centroid_votes[label] = 1.0 / max(distance, 1e-6)

        calibrated = normalize_to_100(centroid_votes)
        blended = build_empty_scores()
        for emotion in EMOTIONS:
            blended[emotion] = (
                raw.get(emotion, 0.0) * self.raw_weight
                + calibrated.get(emotion, 0.0) * self.calibrated_weight
            )
        return normalize_to_100(blended)
