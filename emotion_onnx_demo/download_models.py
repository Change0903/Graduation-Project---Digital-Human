from __future__ import annotations

from pathlib import Path
from urllib.request import urlretrieve


MODEL_DIR = Path(__file__).resolve().parent / "models"

FILES = {
    "facial_expression_recognition_mobilefacenet_2022july.onnx": (
        "https://huggingface.co/opencv/facial_expression_recognition/resolve/main/"
        "facial_expression_recognition_mobilefacenet_2022july.onnx"
    ),
    "face_detection_yunet_2023mar.onnx": (
        "https://huggingface.co/opencv/face_detection_yunet/resolve/main/"
        "face_detection_yunet_2023mar.onnx"
    ),
}


def main() -> None:
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    for filename, url in FILES.items():
        output_path = MODEL_DIR / filename
        if output_path.exists() and output_path.stat().st_size > 0:
            print(f"已存在，跳过: {output_path}")
            continue
        print(f"正在下载: {filename}")
        urlretrieve(url, output_path)
        print(f"下载完成: {output_path} ({output_path.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
