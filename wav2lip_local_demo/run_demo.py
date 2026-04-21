from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import cv2


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEMO_DIR = Path(__file__).resolve().parent
WAV2LIP_DIR = PROJECT_ROOT / "Wav2Lip"
OUTPUT_DIR = DEMO_DIR / "output"
BOX_FILE = DEMO_DIR / "demo_box.json"

DEFAULT_FACE = PROJECT_ROOT / "Video" / "HeyGen.mp4"
DEFAULT_AUDIO = WAV2LIP_DIR / "inputs" / "audio.mp3"
DEFAULT_CHECKPOINT = WAV2LIP_DIR / "checkpoints" / "wav2lip_gan.pth"
DEFAULT_OUTPUT = OUTPUT_DIR / "wav2lip_demo.mp4"
DEFAULT_SFD_WEIGHT = WAV2LIP_DIR / "face_detection" / "detection" / "sfd" / "s3fd.pth"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="独立的 Wav2Lip 本地演示脚本，不影响主项目。"
    )
    parser.add_argument("--face", default=str(DEFAULT_FACE), help="输入的人脸视频或图片路径")
    parser.add_argument("--audio", default=str(DEFAULT_AUDIO), help="输入音频路径")
    parser.add_argument(
        "--checkpoint",
        default=str(DEFAULT_CHECKPOINT),
        help="Wav2Lip 权重路径",
    )
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT),
        help="输出视频路径",
    )
    parser.add_argument(
        "--select-box",
        action="store_true",
        help="先在第一帧上手动框选脸，再保存为独立配置。",
    )
    parser.add_argument(
        "--clear-box",
        action="store_true",
        help="清除当前保存的人脸框配置。",
    )
    parser.add_argument(
        "--force-detector",
        action="store_true",
        help="强制使用 Wav2Lip 自带人脸检测，而不是手动框。",
    )
    parser.add_argument("--resize-factor", type=int, default=1, help="传给 inference.py")
    parser.add_argument(
        "--pads",
        nargs=4,
        type=int,
        default=[0, 10, 0, 0],
        metavar=("TOP", "BOTTOM", "LEFT", "RIGHT"),
        help="传给 inference.py 的 pads 参数",
    )
    parser.add_argument(
        "--fps",
        type=float,
        default=25.0,
        help="输入是静态图片时使用的 fps",
    )
    parser.add_argument(
        "--nosmooth",
        action="store_true",
        help="关闭人脸框平滑",
    )
    return parser.parse_args()


def resolve_python() -> Path:
    candidates = [
        PROJECT_ROOT / ".venv" / "Scripts" / "python.exe",
        PROJECT_ROOT / ".venv" / "bin" / "python",
        Path(sys.executable),
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    raise FileNotFoundError("没有找到可用的 Python 解释器。")


def load_saved_box() -> list[int] | None:
    if not BOX_FILE.exists():
        return None
    data = json.loads(BOX_FILE.read_text(encoding="utf-8"))
    box = data.get("box")
    if not isinstance(box, list) or len(box) != 4:
        return None
    return [int(v) for v in box]


def save_box(box: list[int], source_path: Path) -> None:
    BOX_FILE.write_text(
        json.dumps(
            {
                "source": str(source_path),
                "box": box,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def clear_box() -> None:
    if BOX_FILE.exists():
        BOX_FILE.unlink()


def read_preview_frame(face_path: Path) -> tuple[bool, any]:
    suffix = face_path.suffix.lower()
    if suffix in {".jpg", ".jpeg", ".png"}:
        image = cv2.imread(str(face_path))
        return image is not None, image

    capture = cv2.VideoCapture(str(face_path))
    ok, frame = capture.read()
    capture.release()
    return ok, frame


def select_face_box(face_path: Path) -> list[int]:
    ok, frame = read_preview_frame(face_path)
    if not ok or frame is None:
        raise RuntimeError(f"无法读取预览帧：{face_path}")

    tip = (
        "请框选整张脸，尤其把下巴也包含进去。\n"
        "选完后按 Enter 确认，按 C 取消。"
    )
    print(tip)
    x, y, w, h = cv2.selectROI("Select Face Box", frame, showCrosshair=True, fromCenter=False)
    cv2.destroyAllWindows()

    if w <= 0 or h <= 0:
        raise RuntimeError("你没有选中有效的人脸框。")

    top = int(y)
    bottom = int(y + h)
    left = int(x)
    right = int(x + w)
    return [top, bottom, left, right]


def ensure_exists(path: Path, label: str) -> None:
    if not path.exists():
        raise FileNotFoundError(f"{label} 不存在：{path}")


def ensure_detector_ready(force_detector: bool, manual_box: list[int] | None) -> None:
    if manual_box is not None and not force_detector:
        return
    if DEFAULT_SFD_WEIGHT.exists():
        return
    raise FileNotFoundError(
        "缺少 Wav2Lip 人脸检测权重 s3fd.pth。\n"
        "你现在可以先用手动框模式，不需要改主项目：\n"
        "python wav2lip_local_demo\\run_demo.py --select-box"
    )


def build_command(args: argparse.Namespace, box: list[int] | None) -> list[str]:
    python_exe = resolve_python()
    face_path = Path(args.face).resolve()
    audio_path = Path(args.audio).resolve()
    checkpoint_path = Path(args.checkpoint).resolve()
    output_path = Path(args.output).resolve()

    cmd = [
        str(python_exe),
        "inference.py",
        "--checkpoint_path",
        str(checkpoint_path),
        "--face",
        str(face_path),
        "--audio",
        str(audio_path),
        "--outfile",
        str(output_path),
        "--resize_factor",
        str(args.resize_factor),
        "--pads",
        *(str(v) for v in args.pads),
    ]

    if args.nosmooth:
        cmd.append("--nosmooth")

    if face_path.suffix.lower() in {".jpg", ".jpeg", ".png"}:
        cmd.extend(["--fps", str(args.fps)])

    if box is not None and not args.force_detector:
        cmd.extend(["--box", *(str(v) for v in box)])

    return cmd


def run_inference(cmd: list[str]) -> int:
    env = os.environ.copy()
    env["PATH"] = str(WAV2LIP_DIR) + os.pathsep + env.get("PATH", "")
    env["PYTHONIOENCODING"] = "utf-8"

    print("即将执行：")
    print(" ".join(cmd))
    print(f"工作目录：{WAV2LIP_DIR}")

    completed = subprocess.run(
        cmd,
        cwd=str(WAV2LIP_DIR),
        env=env,
    )
    return completed.returncode


def main() -> int:
    args = parse_args()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    face_path = Path(args.face).resolve()
    audio_path = Path(args.audio).resolve()
    checkpoint_path = Path(args.checkpoint).resolve()

    if args.clear_box:
        clear_box()
        print(f"已清除保存的人脸框：{BOX_FILE}")
        return 0

    ensure_exists(WAV2LIP_DIR / "inference.py", "Wav2Lip 推理脚本")
    ensure_exists(face_path, "输入人脸文件")
    ensure_exists(audio_path, "输入音频文件")
    ensure_exists(checkpoint_path, "Wav2Lip 权重")
    ensure_exists(WAV2LIP_DIR / "ffmpeg.exe", "ffmpeg.exe")

    if args.select_box:
        box = select_face_box(face_path)
        save_box(box, face_path)
        print(f"已保存人脸框到：{BOX_FILE}")
        print(f"当前 box：{box}，格式为 [top, bottom, left, right]")
        return 0

    saved_box = load_saved_box()
    ensure_detector_ready(args.force_detector, saved_box)

    if saved_box and not args.force_detector:
        print(f"将使用保存的人脸框：{saved_box}")
    elif args.force_detector:
        print("将强制使用 Wav2Lip 自带人脸检测。")

    cmd = build_command(args, saved_box)
    return run_inference(cmd)


if __name__ == "__main__":
    raise SystemExit(main())
