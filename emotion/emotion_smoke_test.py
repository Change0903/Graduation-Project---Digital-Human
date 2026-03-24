import argparse
import json
from pathlib import Path

from deepface_runtime import analyze_image, load_deepface


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="DeepFace 最小自检脚本：先测导入，再测单张图片情绪识别。"
    )
    parser.add_argument(
        "--image",
        type=Path,
        help="待分析图片路径，例如 emotion/test.jpg",
    )
    parser.add_argument(
        "--self-check",
        action="store_true",
        help="只验证 DeepFace 能否正常导入。",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if args.self_check:
        load_deepface()
        print("DEEPFACE_IMPORT_OK")
        return

    if args.image is None:
        raise SystemExit("请传入 --image，或者先执行 --self-check。")

    if not args.image.exists():
        raise SystemExit(f"找不到图片文件: {args.image}")

    result = analyze_image(args.image)
    if isinstance(result, list):
        result = result[0]

    dominant_emotion = result.get("dominant_emotion")
    emotions = {
        key: float(value)
        for key, value in result.get("emotion", {}).items()
    }

    print(f"dominant_emotion: {dominant_emotion}")
    print(json.dumps(emotions, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
