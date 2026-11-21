"""Small Windows desktop integrations for the tray, brand mark, and memory readout."""

import ctypes
from ctypes import wintypes
from enum import StrEnum
from queue import SimpleQueue
from threading import Event, Thread

from PIL import Image, ImageDraw
import pystray

from release_info import APP_NAME
from runtime_paths import ROOT

ASSETS = ROOT / "assets"
ICON_PATH = ASSETS / "niuma.ico"


class Signal(StrEnum):
    OFF = "off"
    CLEAR = "clear"
    BITE = "bite"
    OTHER = "other"


SIGNALS = {
    Signal.OFF: ("已暂停", "#9A9D97"),
    Signal.CLEAR: ("值班中", "#A3D65D"),
    Signal.BITE: ("提醒中", "#F3C75D"),
    Signal.OTHER: ("有人入镜", "#EF7771"),
}


def brand_mark() -> Image.Image:
    with Image.open(ASSETS / "studio-mark.png") as image:
        return image.convert("RGBA")


def mascot_art(stern=False, *, cheerful=False) -> Image.Image:
    filename = "niuma-cheer.png" if cheerful else ("niuma-stern.png" if stern else "niuma-mascot.png")
    with Image.open(ASSETS / filename) as image:
        art = image.convert("RGBA")
    alpha = art.getchannel("A")
    if cheerful:
        # Tk's color-key windows expose almost transparent pixels as dark dots.
        alpha = alpha.point(lambda value: value if value >= 110 else 0)
        art.putalpha(alpha)
    return art.crop(alpha.getbbox())


def desktop_active() -> bool:
    user = ctypes.windll.user32
    user.OpenInputDesktop.restype = wintypes.HANDLE
    desktop = user.OpenInputDesktop(0, False, 0x0001)  # DESKTOP_READOBJECTS
    if not desktop:
        return False
    handle = wintypes.HANDLE(desktop)
    name = ctypes.create_unicode_buffer(128)
    try:
        return bool(user.GetUserObjectInformationW(handle, 2, name, ctypes.sizeof(name), None)
                    and name.value.casefold() == "default")
    finally:
        user.CloseDesktop(handle)


def signal_icon(signal: Signal) -> Image.Image:
    icon = Image.new("RGBA", (192, 192))
    draw = ImageDraw.Draw(icon)
    draw.rounded_rectangle((8, 8, 183, 183), radius=46, fill="#27382A")
    cow = mascot_art()
    cow.thumbnail((151, 148), Image.Resampling.LANCZOS)
    icon.alpha_composite(cow, ((192 - cow.width) // 2, (185 - cow.height) // 2))
    draw.ellipse((116, 113, 188, 185), fill="#27382A")
    draw.ellipse((126, 123, 178, 175), fill=SIGNALS[signal][1])
    return icon.resize((64, 64), Image.Resampling.LANCZOS)


class Tray:
    def __init__(self):
        self.actions: SimpleQueue[str] = SimpleQueue()
        self.ready = Event()
        self.closed = Event()
        self.icons = {signal: signal_icon(signal) for signal in Signal}
        self.signal = Signal.OFF
        self.detail = SIGNALS[self.signal][0]
        self.monitoring = False
        self.icon = pystray.Icon(
            "NiuMaSentinel", self.icons[self.signal], f"{APP_NAME} · {self.detail}",
            menu=pystray.Menu(
                pystray.MenuItem(f"打开{APP_NAME}", self._post("open"), default=True),
                pystray.MenuItem(lambda _: "暂停监测" if self.monitoring else "开启监测", self._post("toggle")),
                pystray.MenuItem("访客记录", self._post("visitors")),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem(f"退出{APP_NAME}", self._post("exit")),
            ),
        )
        self.worker = Thread(target=self.icon.run, kwargs={"setup": self._setup}, name="windows-tray", daemon=True)

    def _post(self, action: str):
        def callback(icon, item):
            self.actions.put(action)
        return callback

    def _setup(self, icon) -> None:
        if self.closed.is_set():
            icon.stop()
            return
        icon.visible = True
        self.ready.set()

    def start(self) -> None:
        self.worker.start()

    def update(self, signal: Signal, detail: str, monitoring: bool) -> None:
        if self.closed.is_set():
            return
        if signal != self.signal:
            self.signal = signal
            self.icon.icon = self.icons[signal]
        if detail != self.detail:
            self.detail = detail
            self.icon.title = f"{APP_NAME} · {detail}"
        if monitoring != self.monitoring:
            self.monitoring = monitoring
            self.icon.update_menu()

    def close(self) -> None:
        self.closed.set()
        self.icon.stop()
        self.worker.join(timeout=0.5)


class ProcessMemory(ctypes.Structure):
    _fields_ = [
        ("size", wintypes.DWORD),
        ("faults", wintypes.DWORD),
        ("peak_working_set", ctypes.c_size_t),
        ("working_set", ctypes.c_size_t),
        ("peak_paged", ctypes.c_size_t),
        ("paged", ctypes.c_size_t),
        ("peak_nonpaged", ctypes.c_size_t),
        ("nonpaged", ctypes.c_size_t),
        ("pagefile", ctypes.c_size_t),
        ("peak_pagefile", ctypes.c_size_t),
    ]


def memory_mib() -> float | None:
    memory = ProcessMemory(size=ctypes.sizeof(ProcessMemory))
    ok = ctypes.windll.psapi.GetProcessMemoryInfo(
        wintypes.HANDLE(-1), ctypes.byref(memory), memory.size
    )
    return memory.working_set / (1024 * 1024) if ok else None
