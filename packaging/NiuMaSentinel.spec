# -*- mode: python ; coding: utf-8 -*-
import os
from pathlib import Path
import runpy

from PyInstaller.utils.hooks import collect_dynamic_libs
from PyInstaller.utils.win32.versioninfo import FixedFileInfo, StringFileInfo, StringStruct, StringTable, VarFileInfo, VarStruct, VSVersionInfo


root = Path(SPECPATH).resolve().parent
info = runpy.run_path(str(root / "release_info.py"))
version = info["APP_VERSION"]
windows_version = (*map(int, version.split(".")), 0)
fields = {
    "CompanyName": info["COMPANY"],
    "FileDescription": info["APP_NAME"],
    "FileVersion": version,
    "InternalName": "NiuMaSentinel",
    "OriginalFilename": "NiuMaSentinel.exe",
    "ProductName": info["APP_NAME"],
    "ProductVersion": version,
}
version_info = VSVersionInfo(
    ffi=FixedFileInfo(filevers=windows_version, prodvers=windows_version),
    kids=[StringFileInfo([StringTable("080404B0", [StringStruct(k, v) for k, v in fields.items()])]),
          VarFileInfo([VarStruct("Translation", [2052, 1200])])],
)

# Whitelist release assets. Saved habits, custom sounds and camera data live outside the payload.
assets = ("dialogue.json", "achievement-soft.wav", "soft-chime.wav", "niuma-cheer.png", "niuma-mascot.png",
          "niuma-stern.png", "niuma.ico", "studio-mark.png", "piglets")
models = ("efficientdet_lite0.tflite", "face_landmarker.task", "hand_landmarker.task", "person_face.onnx", "sface_int8.onnx")
data = [(str(root / "assets" / filename), "assets" if filename != "piglets" else "assets/piglets")
        for filename in assets]
data += [(str(root / "models" / filename), "models") for filename in models]
data += [(str(root / "rules" / "defaults.json"), "rules"),
         (str(root / "design" / "小猪哨声-20款试听.html"), "design"),
         (os.environ["NIUMA_RELEASE_LICENSES"], "licenses")]

analysis = Analysis(
    [str(root / "app.py")], pathex=[str(root)], binaries=collect_dynamic_libs("mediapipe"), datas=data,
    hiddenimports=["pystray._win32", "_miniaudio", "mediapipe.tasks.c"],
    hooksconfig={"matplotlib": {"backends": ["Agg"]}},
    module_collection_mode={"pystray": "py"},
)
# Windows webcam capture uses DirectShow or Media Foundation.
analysis.binaries = [entry for entry in analysis.binaries if "opencv_videoio_ffmpeg" not in entry[0].lower()]
pyz = PYZ(analysis.pure)
exe = EXE(pyz, analysis.scripts, [], exclude_binaries=True, name="NiuMaSentinel",
          console=False, upx=False, icon=str(root / "assets" / "niuma.ico"), version=version_info)
bundle = COLLECT(exe, analysis.binaries, analysis.datas, strip=False, upx=False, name="NiuMaSentinel")
