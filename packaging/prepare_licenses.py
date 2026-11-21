"""Collect the notices accompanying the Windows release binaries."""

import argparse
from importlib import metadata
import json
from pathlib import Path
import shutil
import sys


ROOT = Path(__file__).resolve().parents[1]
COMPONENTS = (
    "absl-py", "cffi", "cv2-enumerate-cameras", "flatbuffers", "mediapipe", "miniaudio",
    "numpy", "opencv-contrib-python", "Pillow", "pycparser", "PyInstaller", "pystray", "six", "sounddevice",
    "matplotlib", "contourpy", "cycler", "fonttools", "kiwisolver", "packaging", "pyparsing", "python-dateutil", "tzdata",
)
MODEL_SOURCES = (
    ("Face Landmarker", "https://developers.google.com/edge/mediapipe/solutions/vision/face_landmarker"),
    ("Hand Landmarker", "https://developers.google.com/edge/mediapipe/solutions/vision/hand_landmarker"),
    ("EfficientDet-Lite0", "https://developers.google.com/edge/mediapipe/solutions/vision/object_detector"),
    ("YuNet (OpenCV Zoo, MIT)", "https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet"),
    ("SFace (OpenCV Zoo, Apache-2.0)", "https://github.com/opencv/opencv_zoo/tree/main/models/face_recognition_sface"),
)


def copy_license(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)


def distribution_notices(component: str, output: Path) -> str:
    package = metadata.distribution(component)
    for file in package.files or ():
        name = file.name.upper()
        if any(part.endswith(".dist-info") for part in file.parts) and name.startswith(("COPYING", "LICENSE", "NOTICE")):
            source = package.locate_file(file)
            if source.is_file():
                copy_license(source, output / component / file.name)
    if not (output / component).is_dir():
        copy_license(ROOT / "packaging" / "licenses" / f"{component}.txt", output / component / "LICENSE.txt")
    return f"{component} {package.version}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    parser.add_argument("nsis_notice", type=Path)
    parser.add_argument("yunet_notice", type=Path)
    args = parser.parse_args()
    releases = [distribution_notices(name, args.directory) for name in COMPONENTS]
    python_dir = Path(sys.base_prefix)
    copy_license(python_dir / "LICENSE.txt", args.directory / "Python" / "LICENSE.txt")
    for license_file in (python_dir / "tcl").glob("*/license.terms"):
        copy_license(license_file, args.directory / "Tcl-Tk" / f"{license_file.parent.name}.txt")
    copy_license(ROOT / "packaging" / "licenses" / "Tcl.txt", args.directory / "Tcl-Tk" / "Tcl.txt")
    copy_license(ROOT / "packaging" / "licenses" / "PortAudio.txt", args.directory / "sounddevice" / "PortAudio.txt")
    copy_license(args.nsis_notice, args.directory / "NSIS" / "COPYING.txt")
    copy_license(args.yunet_notice, args.directory / "Models" / "YuNet-LICENSE.txt")
    copy_license(ROOT / "models" / "SFace-LICENSE.txt", args.directory / "Models" / "SFace-LICENSE.txt")
    copy_license(args.directory / "mediapipe" / "LICENSE", args.directory / "Models" / "MediaPipe-Apache-2.0.txt")

    audio = json.loads((ROOT / "assets" / "piglets" / "credits.json").read_text(encoding="utf-8"))
    lines = ["牛马哨兵 · 第三方许可与署名", "", "安装程序与运行组件", "Python 3.12 · Python Software Foundation",
             "NSIS · https://nsis.sourceforge.io/License", *releases, "", "视觉模型来源"]
    lines.extend(f"{name}  {url}" for name, url in MODEL_SOURCES)
    lines.extend(("", "小猪音效来源", audio["edits"]))
    for source in audio["sources"].values():
        lines.extend((f'{source["title"]} · {source["author"]} · {source["license"]}', source["url"]))
        if source["license_url"]:
            lines.append(source["license_url"])
    lines.extend(("", "pystray 源代码随应用放在 _internal/pystray 目录。",
                  "该目录中的 pystray 库文件可以按随附的 LGPL 条款修改或替换。",
                  "随附许可证适用于相应的第三方组件。"))
    (args.directory / "THIRD-PARTY-NOTICES.txt").write_text("\n".join(lines) + "\n", encoding="utf-8-sig")


if __name__ == "__main__":
    main()
