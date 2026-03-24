import os
import sys
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parent


def prepare_deepface_env() -> None:
    """
    Keep DeepFace cache files inside the project so the main environment stays untouched.
    """
    os.environ.setdefault("DEEPFACE_HOME", str(BASE_DIR))
    os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def load_deepface():
    prepare_deepface_env()
    try:
        from deepface import DeepFace
    except ModuleNotFoundError as exc:
        if exc.name == "deepface":
            raise RuntimeError(
                "没有在当前 Python 环境中找到 deepface。\n"
                "请不要用主项目的 Python 直接运行 emotion 脚本。\n"
                "请改用独立环境：\n"
                r"  .\.venv-deepface\Scripts\python.exe .\emotion\emotion_camera_test.py"
            ) from exc
        raise

    return DeepFace


def analyze_image(
    image_path: str | Path,
    detector_backend: str = "opencv",
    enforce_detection: bool = False,
) -> Any:
    deepface = load_deepface()
    return deepface.analyze(
        img_path=str(image_path),
        actions=["emotion"],
        detector_backend=detector_backend,
        enforce_detection=enforce_detection,
    )
