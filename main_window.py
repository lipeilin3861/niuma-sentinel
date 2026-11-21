"""Windows dashboard for the camera worker, tray, rules, and reminder library."""

from collections import deque
import ctypes
from ctypes import wintypes
from dataclasses import replace
from datetime import datetime
import json
import os
from pathlib import Path
from queue import Empty, SimpleQueue
from random import shuffle
import subprocess
from threading import Thread
from time import perf_counter, time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import webbrowser

import cv2
from PIL import Image, ImageTk

from camera import DEFAULT_HOLD, CameraSession, FrameSample, list_cameras
from desktop import APP_NAME, ASSETS, ICON_PATH, SIGNALS, Signal, Tray, brand_mark, desktop_active, mascot_art, memory_mib
from habit_settings import (AWARD_HOUR, AWARD_TITLES, BITE_AWARD, BREAK_MINUTES, BUBBLE, DIALOG, INTERVALS,
                            SECRET_AWARDS, SECRET_DESCRIPTIONS, STYLE_NAMES, WATER_AWARD, WATER_GOAL,
                            ZERO_BITE_COVERAGE, DayRecord, Habits, WorkClock)
from local_rules import (ACHIEVEMENT, BITE, DRINK_DONE, EYE_GLASSES, EYE_RUB, FACE_AWARD, GENTLE_REMINDERS, GUEST, STRETCH, WATER,
                         RULES_PATH, load_rule_document, write_rule_document)
from native_views import (BODY, DARK, DISPLAY, INK, LINE, MUTED, PAPER, SURROUND, AchievementNotice, CornerNotice,
                          GameNotice, MotionSwitch, WatchCard, button, label, photo)
from presence import RESULT_FRESHNESS, PresenceResult
from release_info import (APP_VERSION, BRAND, COMPANY, COOPERATION_EMAIL, CREATORS, DESCRIPTION, FEATURES,
                          PRIVACY, SERVICES, SLOGAN, SYSTEM_REQUIREMENTS, WEBSITE, WEBSITE_URL)
from reminder_audio import SOUND_NAMES, ReminderAudio
from runtime_paths import ROOT
from visitor_log import VisitorLog, last_input_at

VIEW = "#18271d"
AMBER = "#f3c75d"
BODY_FONT = BODY
DISPLAY_FONT = DISPLAY
HOLD_OPTIONS = {"较快": 0.22, "标准": DEFAULT_HOLD, "少打扰": 0.62}
SHORT_STYLES = {BUBBLE: "气泡", DIALOG: "大窗", None: "逐条"}
MONITOR_EVENTS = {
    BITE.id: "bite", WATER.id: "water", DRINK_DONE.id: "water", EYE_GLASSES.id: "eye", EYE_RUB.id: "eye",
    STRETCH.id: "break", GUEST.id: "guest", FACE_AWARD.id: "expression", ACHIEVEMENT.id: None,
}
EYE_LINES = {
    EYE_GLASSES.id: (
        "眼镜坐得很稳，手先下岗。", "镜框收到，再扶就要交接班了。", "镜腿已经就位，请撤回牛蹄。",
        "眼镜今天续了防滑假，放心吧。", "把手放下，给镜框一点独处时间。", "镜框开会中，请勿反复签到。",
        "鼻梁已经把活接过去了。", "扶一次是帮忙，再扶就算催工了。", "手和镜框先各忙各的。",
        "眼镜上线了，牛蹄可以离线了。", "镜框申请今天最后一次被扶。", "这副眼镜有自己的站位。",
    ),
    EYE_RUB.id: (
        "揉归揉，眼睛可不是刷新键。", "眼睛申请带薪休息两秒。", "手先下班，让眼睛眨两下。",
        "揉不出新进度，先看远一点。", "别给眼睛做手动抛光。", "眼睛来电，请把牛蹄收回。",
        "先让手离场，眨眼还来得及。", "眼皮说它正在忙，麻烦稍后。", "把这点劲留给伸懒腰。",
        "给眼睛一点休息时间。", "手停一下，眼睛还在值班。", "暂停搓屏模式，眨两下再回来。",
    ),
}

def tray_signal(result: PresenceResult | None, now: float, bite_until: float) -> Signal:
    fresh = result is not None and 0 <= now - result.timestamp <= RESULT_FRESHNESS
    if fresh and result.extra_person:
        return Signal.OTHER
    if now < bite_until:
        return Signal.BITE
    return Signal.CLEAR if fresh else Signal.OFF


class MonitorInfo(ctypes.Structure):
    _fields_ = [
        ("size", wintypes.DWORD), ("monitor", wintypes.RECT),
        ("work_area", wintypes.RECT), ("flags", wintypes.DWORD),
    ]


def active_monitor(root: tk.Tk, *, work_area=False) -> wintypes.RECT:
    user32 = ctypes.windll.user32
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.MonitorFromWindow.restype = wintypes.HANDLE
    monitor = user32.MonitorFromWindow(wintypes.HWND(user32.GetForegroundWindow()), 2)
    info = MonitorInfo(size=ctypes.sizeof(MonitorInfo))
    if monitor and user32.GetMonitorInfoW(wintypes.HANDLE(monitor), ctypes.byref(info)):
        return info.work_area if work_area else info.monitor
    return wintypes.RECT(0, 0, root.winfo_screenwidth(), root.winfo_screenheight())


class Preview(tk.Canvas):
    def __init__(self, parent):
        super().__init__(parent, bg=VIEW, highlightthickness=0, width=0, height=0)
        self.sample: FrameSample | None = None
        self.photo = None
        self.title = "摄像头已暂停"
        self.detail = "开启监测后显示本机画面"
        self.bind("<Configure>", lambda _: self.repaint())

    def standby(self, title, detail="") -> None:
        self.sample = None
        self.title, self.detail = title, detail
        self.repaint()

    def paint(self, sample: FrameSample) -> None:
        self.sample = sample
        self.repaint()

    def repaint(self) -> None:
        self.delete("all")
        width, height = self.winfo_width(), self.winfo_height()
        if min(width, height) < 2:
            return
        if self.sample is None:
            self.photo = None
            self._standby(width, height)
            return
        rgb = self.sample.rgb
        sh, sw = rgb.shape[:2]
        scale = min(width / sw, height / sh)
        shown_w, shown_h = max(1, round(sw * scale)), max(1, round(sh * scale))
        picture = Image.fromarray(rgb).resize((shown_w, shown_h), Image.Resampling.BILINEAR)
        self.photo = ImageTk.PhotoImage(picture, master=self)
        x, y = (width - shown_w) // 2, (height - shown_h) // 2
        self.create_image(x, y, image=self.photo, anchor="nw")
        hit = self.sample.hit
        if hit:
            px, py = x + hit.tip[0] * scale, y + hit.tip[1] * scale
            radius = max(15, hit.scale * scale * 0.17)
            bounds = (px - radius, py - radius, px + radius, py + radius)
            self.create_oval(bounds, outline=DARK, width=5)
            self.create_oval(bounds, outline=AMBER, width=2)

    def _standby(self, width, height) -> None:
        edge = 20
        for x, y, dx, dy in (
            (edge, edge, 1, 1), (width - edge, edge, -1, 1),
            (width - edge, height - edge, -1, -1), (edge, height - edge, 1, -1),
        ):
            self.create_line(x + dx * 17, y, x, y, x, y + dy * 17, fill="#456054", width=2)
        cx, cy = width / 2, height * 0.41
        r = min(80, height * 0.23)
        self.create_oval(cx-r, cy-r, cx+r, cy+r, outline="#314B3F", width=1)
        self.create_arc(cx-r, cy-r, cx+r, cy+r, start=18, extent=68, style="arc", outline="#648773", width=2)
        self.create_arc(cx-r, cy-r, cx+r, cy+r, start=198, extent=68, style="arc", outline="#648773", width=2)
        self.create_oval(cx-r*0.69, cy-r*0.69, cx+r*0.69, cy+r*0.69, outline="#354B41", width=1)
        self.create_line(cx-9, cy+10, cx-9, cy-6, fill="#C0DFCA", width=8, capstyle="round")
        self.create_line(cx+8, cy+10, cx+8, cy-16, fill="#DCECDF", width=8, capstyle="round")
        self.create_oval(cx+19, cy-20, cx+25, cy-14, fill="#9FC7AA", outline="")
        text_y = min(height - 58, cy + r + 30)
        self.create_text(cx, text_y, text=self.title, fill="#ECF1E6", font=(DISPLAY_FONT, 16))
        self.create_text(
            cx, text_y + 26, text=self.detail, fill="#AABEB0", font=(BODY_FONT, 10),
            width=max(160, width - 76), justify="center", anchor="n",
        )




class App:
    def __init__(self, root: tk.Tk, *, start_paused=False, activation_requested=None, splash=None):
        self.root = root
        self.activation_requested = activation_requested
        self.splash = splash
        self.startup_at = perf_counter()
        self.reveal_on_frame = splash is not None
        self.auto_start = not start_paused
        self.session: CameraSession | None = None
        self.cameras = []
        self.devices = SimpleQueue()
        self.scanning = False
        self.camera_choice = tk.StringVar(root)
        self.hold_choice = tk.StringVar(root, value="标准")
        self.sound = ReminderAudio()
        self.habits = Habits()
        self.visitors = VisitorLog()
        self.visitors.set_suspended(not self.habits.data.guest_enabled)
        self.style_choices = {}
        self.monitor_changed_at = {}
        self.guest_changed_at = -100.0
        self.guest_present = False
        self.water_error = self.habits.error
        self.water_view = None
        self.bite_view = None
        self.award_view = None
        self.work_clock = WorkClock()
        self.watch_day = datetime.now().date().isoformat()
        self.watch_buffer = 0.0
        self.watch_tick = perf_counter()
        self.was_watching = False
        self.tray = Tray()
        self.signal = Signal.OFF
        self.status_values = None
        self.last_frame = -1
        self.alert_until = 0.0
        self.notice = None
        self.notice_id = None
        self.notice_timer = None
        self.pending = deque(maxlen=5)
        self.feedback = deque(maxlen=8)
        self.close_deadline = None
        self.panels = {}
        self.camera_menu = None
        self.previewing = False
        self.rules = ()
        self.rule_detail = ""
        self._fetch_rules()
        self.lines = json.loads((ASSETS / "dialogue.json").read_text(encoding="utf-8"))
        self.lines.update(EYE_LINES)
        self.decks = {}
        self.last_lines = {}

        root.title(APP_NAME)
        root.configure(bg=SURROUND)
        root.iconbitmap(str(ICON_PATH))
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        width, height = min(sw - 60, 1116), min(sh - 120, 653)
        root.geometry(f"{width}x{height}+{max(0,(sw-width)//2)}+{max(0,(sh-height)//2-18)}")
        root.minsize(min(860, width), min(600, height))
        root.protocol("WM_DELETE_WINDOW", self.hide_to_tray)
        self._layout()
        self.set_signal(Signal.OFF, "准备值班", "正在查找摄像头。", "摄像头 · 正在加载")
        self.tray.start()
        self.read_memory()
        self.refresh_cameras()
        root.after(25, self.poll)
        root.after(1000, self.poll_habits)
        if splash:
            splash.toplevel.bind("<Escape>", lambda _: self.close())

    def _layout(self) -> None:
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("Play.TCombobox", padding=(6, 5), bordercolor=LINE, lightcolor=LINE,
                        darkcolor=LINE, fieldbackground=PAPER, foreground=INK, background="#e8eee0")
        style.configure("Play.Treeview", background=PAPER, fieldbackground=PAPER, foreground=INK,
                        rowheight=30, borderwidth=0, font=(BODY, 10))
        style.configure("Play.Treeview.Heading", font=(BODY, 10))
        style.configure("Habit.Horizontal.TProgressbar", background="#7f9d68", troughcolor="#e4ebdb",
                        bordercolor="#e4ebdb", lightcolor="#7f9d68", darkcolor="#7f9d68", thickness=7)
        self.root.option_add("*TCombobox*Listbox.font", (BODY, 10))

        top = tk.Frame(self.root, bg=PAPER, padx=23, pady=12)
        top.pack(fill="x")
        self.product_icon = photo(self.root, mascot_art(), (35, 33), Image.Resampling.NEAREST)
        tk.Label(top, image=self.product_icon, bg="#e5edcf", width=42, height=39, bd=0).pack(side="left",padx=(0,10))
        label(top, APP_NAME, size=15, bold=True).pack(side="left")
        label(top, f"v{APP_VERSION}", size=9, ink=MUTED).pack(side="left", padx=(10, 0))
        self.studio = photo(self.root, brand_mark(), (38, 16))
        label(top, BRAND, size=8, ink=MUTED).pack(side="right", padx=(5, 0))
        tk.Label(top,image=self.studio,bg=PAPER,bd=0).pack(side="right")
        button(top, "产品介绍", self.open_about, palette="quiet", size=9, height=3).pack(side="right", padx=(0, 12))
        tk.Frame(self.root, bg=LINE, height=1).pack(fill="x")

        bottom = tk.Frame(self.root, bg="#eef2e6", padx=23, pady=12)
        bottom.pack(side="bottom",fill="x")
        self.memory_label = label(bottom, "", paper="#eef2e6",ink=MUTED,size=9)
        self.memory_label.pack(side="right")
        label(bottom,"本机处理",paper="#eef2e6",ink=MUTED,size=9).pack(side="right", padx=(0,14))
        label(bottom,"托盘状态",paper="#eef2e6",ink=MUTED,size=9).pack(side="left",padx=(0,9))
        tray_pill = tk.Frame(bottom,bg=DARK,padx=8,pady=4)
        tray_pill.pack(side="left")
        self.tray_pictures = {value: photo(self.root, self.tray.icons[value], (17,17)) for value in Signal}
        self.tray_icon = tk.Label(tray_pill,bg=DARK,image=self.tray_pictures[Signal.OFF],bd=0)
        self.tray_icon.pack(side="left",padx=(0,6))
        self.tray_text = label(tray_pill,"已暂停",paper=DARK,ink="#f3f6e9",size=9)
        self.tray_text.pack(side="left")
        for value, name in ((Signal.CLEAR,"值班"),(Signal.BITE,"提醒"),(Signal.OTHER,"来人"),(Signal.OFF,"暂停")):
            color = SIGNALS[value][1]
            dot = tk.Canvas(bottom,width=9,height=9,bg="#eef2e6",highlightthickness=0)
            dot.create_oval(1,1,8,8,fill=color,outline="")
            dot.pack(side="left",padx=(10,3))
            label(bottom,name,paper="#eef2e6",ink=MUTED,size=9).pack(side="left")

        desk = tk.Frame(self.root, bg=PAPER, padx=22, pady=21)
        desk.pack(fill="both",expand=True)
        desk.rowconfigure(0, weight=1)
        desk.columnconfigure(0, weight=1)
        self.stage = tk.Frame(desk,bg=PAPER)
        self.stage.grid(row=0,column=0,sticky="nsew",padx=(0,23))
        self.watch = WatchCard(self.stage,self.toggle_camera)
        self.watch.pack(fill="both",expand=True)
        self.camera_button = self.watch.button
        self.preview = Preview(self.stage)

        panel = tk.Frame(desk,bg=PAPER,width=348)
        panel.grid(row=0,column=1,sticky="nsew")
        panel.pack_propagate(False)
        actions = tk.Frame(panel,bg=PAPER)
        actions.pack(side="bottom",fill="x")
        self.view_button = button(actions,"查看画面",self.toggle_preview,size=9,padding=10)
        self.view_button.pack(side="left")
        button(actions,"收起到托盘",self.hide_to_tray,palette="quiet",size=9,padding=11).pack(side="left",padx=(6,0))
        heading = tk.Frame(panel,bg=PAPER)
        heading.pack(fill="x",pady=(4,12))
        label(heading,"监测",size=17,bold=True).pack(side="left")
        button(heading,"设置",self.open_settings,palette="quiet",size=9,padding=9,height=4).pack(side="right")
        button(heading,"声音",self.open_sounds,size=9,padding=10,height=4).pack(side="right",padx=(0,5))
        button(heading,"成就",self.open_achievements,size=9,padding=10,height=4).pack(side="right",padx=(0,5))
        tk.Frame(panel,bg=LINE,height=1).pack(fill="x")
        self.bite_status, _ = self._row(panel, "bite", "手", "防啃手", "#f2ead7", "#846835", self.open_bite)
        self.water_status, _ = self._row(panel, "water", "水", "喝水记录", "#e4edf0", "#48687d", self.open_water)
        self.eye_status, self.eye_switch = self._row(panel, "eye", "眼", "扶眼镜 / 揉眼", "#eee7f1", "#765d7d")
        self.break_status, _ = self._row(panel, "break", "动", "活动提醒", "#e5ecdf", "#4a6950", self.open_break)
        self.guest_status, _ = self._row(panel, "guest", "人", "老板来了", "#f0e5e0", "#87594c", self.open_visitors)
        self.expression_status, _ = self._row(panel, "expression", "彩", "表情彩蛋", "#f2ebd6", "#896a46", self.open_achievements)
        self.rules_status, _ = self._row(panel, "rules", "规", "本地规则", "#e6ecda", "#557047", self.open_rules)
        self.note = label(panel,"",size=9,ink=MUTED,wraplength=340)
        self.note.pack(anchor="w",pady=(7,3))
        self._rule_status()
        self._sync_water()
        self._sync_bites()
        self._sync_eye_status()
        self._sync_break()
        self._sync_guest_status()
        self._sync_expressions()

    def _row(self, container, feature, glyph, name, tile, ink, details=None):
        row = tk.Frame(container,bg=PAPER)
        row.pack(fill="x", pady=4)
        icon = tk.Canvas(row,bg=PAPER,width=34,height=34,highlightthickness=0)
        icon.create_oval(1,1,33,33,fill=tile,outline="")
        icon.create_text(17,17,text=glyph,fill=ink,font=(BODY,-13))
        icon.pack(side="left",padx=(0,10))
        control = MotionSwitch(row, self.habits.data.monitor_enabled(feature),
                               lambda enabled: self.set_monitor(feature, enabled))
        control.pack(side="right", padx=(8, 0))
        self._style_dropdown(row, feature).pack(side="right")
        titles = tk.Frame(row,bg=PAPER)
        titles.pack(side="left",fill="x",expand=True)
        if details is None:
            label(titles,name,size=11,bold=True).pack(anchor="w")
        else:
            tk.Button(titles, text=name + "  ›", command=details, bg=PAPER, fg=INK, activebackground="#eef2e7",
                      activeforeground=DARK, font=(BODY, 11, "bold"), bd=0, relief="flat", highlightthickness=0,
                      cursor="hand2", padx=0, pady=0).pack(anchor="w")
        detail=label(titles,"待命",size=9,ink=MUTED)
        detail.pack(anchor="w",pady=(1,0))
        tk.Frame(container,bg=LINE,height=1).pack(fill="x")
        return detail,control

    def _style_dropdown(self, parent, feature):
        options = SHORT_STYLES if feature == "rules" else {key: SHORT_STYLES[key] for key in (BUBBLE, DIALOG)}
        variable = self.style_choices.get(feature)
        if variable is None:
            variable = tk.StringVar(self.root, value=SHORT_STYLES[self.habits.data.monitor_style(feature)])
            self.style_choices[feature] = variable
        picker = ttk.Combobox(parent, textvariable=variable, values=tuple(options.values()), width=5,
                              style="Play.TCombobox", state="readonly", font=(BODY, 9))

        def select(_):
            style = next(key for key, title in options.items() if title == variable.get())
            try:
                self.habits.set_monitor_style(feature, style)
            except (OSError, ValueError) as error:
                variable.set(SHORT_STYLES[self.habits.data.monitor_style(feature)])
                messagebox.showerror("提醒样式", str(error), parent=parent.winfo_toplevel())

        picker.bind("<<ComboboxSelected>>", select)
        return picker

    def _fetch_rules(self):
        try:
            _, self.rules = load_rule_document()
            self.rule_detail = ""
            if self.session is not None:
                self.session.rules = self.rules
            return True
        except (OSError, ValueError) as error:
            self.rule_detail = str(error)
            return False

    def _rule_status(self):
        active = sum(rule.enabled for rule in self.rules)
        if not self.habits.data.rules_enabled:
            detail = "规则检测已暂停"
        else:
            detail = "规则文件待检查" if self.rule_detail else (f"{active} 条已启用" if active else "编辑本地动作规则")
        self.rules_status.configure(text=detail)

    def _finish_splash(self, detail, heading="摄像头已就绪"):
        if self.reveal_on_frame:
            self.reveal_on_frame = False
            self.splash.show(detail,heading)
            self.splash.finish(self.bring_to_front)

    def refresh_cameras(self):
        if self.session or self.scanning or self.close_deadline is not None:
            return
        self.scanning = True
        self.camera_button.configure(state="disabled",text="正在查找")
        def scan():
            try:
                self.devices.put((list_cameras(), None))
            except (OSError, RuntimeError, cv2.error) as error:
                self.devices.put(([],str(error)))
        Thread(target=scan,name="camera-discovery",daemon=True).start()

    def accept_devices(self, devices, error):
        self.scanning = False
        auto_start, self.auto_start = self.auto_start, False
        previous = self.camera_choice.get()
        self.cameras = devices
        names = [device.name for device in devices]
        self.camera_choice.set(previous if previous in names else (names[0] if names else "未发现摄像头"))
        self._camera_menu()
        if not names:
            self.camera_button.configure(state="normal",text="重新查找")
            detail = "连接摄像头后，可以重新查找。"
            self.note.configure(text=error or detail)
            self.set_signal(Signal.OFF,"未找到","请连接摄像头。","摄像头 · 未找到")
            self.preview.standby("未发现摄像头",detail)
            self._finish_splash("连接设备后可重新查找","未发现摄像头")
        elif auto_start:
            self.start_camera()
        else:
            self.camera_stopped(None)

    def _camera_menu(self):
        if self.camera_menu is not None and self.camera_menu.winfo_exists():
            self.camera_menu.configure(values=[camera.name for camera in self.cameras],
                                       state="disabled" if self.session else "readonly")

    def toggle_camera(self):
        if self.close_deadline is not None:
            return
        if self.session is None:
            self.start_camera() if self.cameras else self.refresh_cameras()
        elif not self.session.stop_requested.is_set():
            self.session.stop()
            self.visitors.cancel_enrollment()
            self.pending.clear()
            self.alert_until = 0
            if self.notice_id not in GENTLE_REMINDERS:
                self.dismiss_notice()
            self.camera_button.configure(text="正在暂停",state="disabled")
            self.preview.standby("正在暂停摄像头")
            self.set_signal(Signal.OFF,"正在暂停","摄像头正在关闭。","摄像头 · 正在关闭")

    def start_camera(self):
        device = next((item for item in self.cameras if item.name==self.camera_choice.get()),None)
        if device is None or self.session is not None or self.close_deadline is not None:
            return
        self.session = CameraSession(device, HOLD_OPTIONS[self.hold_choice.get()], self.rules,
                                     visitors=self.visitors, preferences=self.habits.data)
        self.last_frame = -1
        self.guest_present = False
        self.note.configure(text="")
        self.camera_button.configure(text="暂停值班",state="normal")
        self.preview.standby("正在加载摄像头")
        self.set_signal(Signal.OFF,"准备值班","正在加载摄像头与本地模型。",device.name,monitoring=True)
        self._camera_menu()
        self.session.start()

    def camera_stopped(self,error):
        self.session = None
        self.pending.clear()
        self.alert_until=0
        self.camera_button.configure(text="开始值班",state="normal")
        self._camera_menu()
        self._sync_bites()
        self._sync_eye_status()
        self.guest_present = False
        self._sync_guest_status()
        name=self.camera_choice.get()
        if error:
            self.note.configure(text=error)
            self.preview.standby("摄像头未就绪",error)
            self.set_signal(Signal.OFF,"设备出错","检查连接后可以再次开启。",name)
            self._finish_splash("可以在设置中更换设备","摄像头未就绪")
        else:
            self.preview.standby("摄像头已暂停","开始值班后可查看本机画面")
            self.set_signal(Signal.OFF,"已暂停","可随时开始值班。",name)
            self._finish_splash(name,"摄像头已暂停")

    def set_signal(self, signal, title, subtitle, device, *, monitoring=False):
        value=(signal,title,subtitle,device,monitoring)
        if value==self.status_values:
            return
        self.status_values=value
        self.signal=signal
        self.watch.update_status(signal,title,subtitle,device)
        self.tray_icon.configure(image=self.tray_pictures[signal])
        self.tray_text.configure(text=SIGNALS[signal][0] if signal!=Signal.OFF else title)
        self.tray.update(signal,title,monitoring)

    def read_memory(self):
        if self.close_deadline is None:
            value=memory_mib()
            self.memory_label.configure(text=f"内存 {value:.0f} MiB" if value is not None else "内存 —")
            self.root.after(1700,self.read_memory)

    def toggle_preview(self):
        self.previewing=not self.previewing
        if self.previewing:
            self.watch.pack_forget()
            self.preview.pack(fill="both",expand=True)
            sample=self.session.latest if self.session is not None else None
            if sample is not None and not self.session.stop_requested.is_set():
                self.preview.paint(sample)
        else:
            self.preview.pack_forget()
            self.preview.standby("摄像头画面已收起")
            self.watch.pack(fill="both",expand=True)
        self.view_button.configure(text="回到值班台" if self.previewing else "查看画面")

    def hide_to_tray(self):
        if self.reveal_on_frame:
            return
        if self.tray.ready.is_set() and self.tray.worker.is_alive():
            self.root.withdraw()
        else:
            self.root.iconify()

    def bring_to_front(self):
        if self.close_deadline is None and not self.reveal_on_frame:
            self.root.deiconify()
            self.root.lift()

    def poll(self):
        if self.close_deadline is not None:
            if self.session is None or not self.session.worker.is_alive() or perf_counter()>=self.close_deadline:
                if self.session is not None:
                    self._collect_camera_events(self.session, perf_counter(), show=False)
                self._save_watch_time()
                self.tray.close()
                self.root.destroy()
                return
        else:
            if self.activation_requested and self.activation_requested():
                self.bring_to_front()
            self._tray_actions()
            if self.close_deadline is None:
                try:
                    devices,error=self.devices.get_nowait()
                    self.accept_devices(devices,error)
                except Empty:
                    pass
                self._poll_camera()
                self._show_queued(perf_counter())
                if self.reveal_on_frame and perf_counter()-self.startup_at>12:
                    self._finish_splash("设备仍在连接中","正在加载摄像头")
        self.root.after(30,self.poll)

    def _tray_actions(self):
        while True:
            try:
                action=self.tray.actions.get_nowait()
            except Empty:
                return
            if action=="open":
                self.bring_to_front()
            elif action=="toggle":
                self.toggle_camera()
            elif action=="visitors":
                self.bring_to_front()
                self.open_visitors()
            elif action=="exit":
                self.close()
                return

    def _poll_camera(self):
        session=self.session
        if session is None:
            return
        if not session.worker.is_alive():
            self._collect_camera_events(session, perf_counter(), show=not session.stop_requested.is_set())
            self.camera_stopped(session.error)
            return
        if session.stop_requested.is_set():
            return
        sample=session.latest
        if sample is None:
            if self.reveal_on_frame:
                self.splash.show(session.phase)
            return
        self._finish_splash(session.camera.name)
        now=perf_counter()
        if sample.number!=self.last_frame:
            if sample.hit is not None:
                self.alert_until=max(self.alert_until,now+.35)
            self.last_frame=sample.number
            if self.previewing and self.root.state() in ("normal","zoomed"):
                self.preview.paint(sample)
        self._collect_camera_events(session, now)
        result = self._sync_guest_status(sample, now)
        behavior_notice = self.notice_id is not None and self.notice_id not in GENTLE_REMINDERS and self.notice_id != GUEST.id
        alert_until = max(self.alert_until, now + 1 if behavior_notice else 0)
        signal=tray_signal(result,now,alert_until)
        name=session.camera.name
        if signal==Signal.OTHER:
            self.set_signal(signal,"有人来了","第二人入镜，托盘已亮红灯。",name,monitoring=True)
        elif signal==Signal.BITE:
            self.set_signal(signal,"提醒中","哨兵有句话想说。",name,monitoring=True)
        elif signal==Signal.CLEAR:
            self.set_signal(signal,"值班中","已开启的项目在本机运行。",name,monitoring=True)
        elif session.presence.error:
            self.set_signal(signal,"身后待确认","可暂停后重新开启摄像头。",name,monitoring=True)
        else:
            self.set_signal(signal,"确认画面","摄像头已开启，正在确认是否有人入镜。",name,monitoring=True)

    def _sync_guest_status(self, sample=None, now=None):
        result = None
        if not self.habits.data.guest_enabled:
            detail = "来人提醒已暂停"
            if sample is not None:
                result = PresenceResult(False, sample.capture_time)
        elif sample is None or self.session is None:
            detail = "等待摄像头值班"
        elif self.session.presence.error:
            detail = "检测待重试"
        else:
            result = self.session.presence.latest
            if result is None or result.timestamp < self.guest_changed_at or now - result.timestamp > RESULT_FRESHNESS:
                detail = "正在确认画面"
                result = None
            else:
                if result.extra_person and not self.guest_present:
                    self.pending.append((now, GUEST))
                self.guest_present = result.extra_person
                detail = "有人入镜 · 托盘红灯" if self.guest_present else "第二人入镜时提醒"
        if self.guest_status.cget("text") != detail:
            self.guest_status.configure(text=detail)
        return result

    def _event_enabled(self, event, stamp=None):
        feature = MONITOR_EVENTS.get(event.id, "rules")
        if feature is None:
            return True
        if stamp is not None and stamp < self.monitor_changed_at.get(feature, 0):
            return False
        if not self.habits.data.monitor_enabled(feature):
            return False
        return feature != "rules" or any(rule.enabled and rule.reminder.id == event.id for rule in self.rules)

    def _collect_camera_events(self, session, now, *, show=True):
        while True:
            try:
                stamp = session.drinks.get_nowait()
            except Empty:
                break
            if self.habits.data.records_drinks and self._event_enabled(DRINK_DONE, stamp):
                self.record_visual_drink(stamp, notify=show)
        while True:
            try:
                stamp, event = session.reminders.get_nowait()
            except Empty:
                break
            if not self._event_enabled(event, stamp):
                continue
            if event.id == BITE.id:
                try:
                    self.habits.record_bite(stamp)
                    self.water_error = ""
                except (OSError, ValueError) as error:
                    self.water_error = str(error)
                self._sync_bites(update_log=True)
            if show:
                self.pending.append((now, event))
        while True:
            try:
                stamp, award = session.expressions.get_nowait()
            except Empty:
                break
            if not self._event_enabled(FACE_AWARD, stamp):
                continue
            try:
                unlocked = self.habits.record_expression(award, stamp)
            except (OSError, ValueError) as error:
                self.water_error = str(error)
                continue
            if unlocked:
                self._sync_expressions()
                self._sync_achievements(datetime.now())
                self._apply_preferences()
                if show:
                    event = replace(FACE_AWARD, title=AWARD_TITLES[award], message=SECRET_DESCRIPTIONS[award])
                    self.feedback.append((now, event))

    def _show_queued(self, now):
        if self.reveal_on_frame:
            return
        while self.pending and (now - self.pending[0][0] > 8 or not self._event_enabled(self.pending[0][1])):
            self.pending.popleft()
        if self.pending and (self.notice is None or self.notice_id in (WATER.id, DRINK_DONE.id, STRETCH.id)):
            self.show_notice(self.pending.popleft()[1])
        elif self.notice is None:
            while self.feedback:
                stamp, event = self.feedback.popleft()
                if now - stamp <= 45 and self._event_enabled(event):
                    self.show_notice(event)
                    break

    def next_line(self, event):
        if event.message:
            return event.message
        bag=self.decks.setdefault(event.id,[])
        if not bag:
            bag.extend(self.lines[event.id])
            shuffle(bag)
            if len(bag)>1 and bag[-1]==self.last_lines.get(event.id):
                bag[0],bag[-1]=bag[-1],bag[0]
        line=bag.pop()
        self.last_lines[event.id]=line
        return line

    def show_notice(self, event=BITE):
        self.dismiss_notice()
        self.notice_id = event.id
        if event.id not in GENTLE_REMINDERS and event.id != GUEST.id:
            self.alert_until = perf_counter() + 1.0
        style = self.reminder_style(event)
        tone = {DRINK_DONE.id: "cheer", FACE_AWARD.id: "cheer", STRETCH.id: "rest"}.get(event.id, "guard")
        if event.id == ACHIEVEMENT.id or (event.id == FACE_AWARD.id and style == DIALOG):
            self.notice = AchievementNotice(self.root, active_monitor(self.root, work_area=True), event, self.dismiss_notice)
        elif style == BUBBLE:
            caption = "手从嘴边放下来。" if event.id == BITE.id else self.next_line(event)
            self.notice = CornerNotice(self.root, active_monitor(self.root, work_area=True), event,
                                       caption, self.dismiss_notice, tone=tone)
        else:
            self.notice = GameNotice(
                self.root, active_monitor(self.root), event, lambda: self.next_line(event),
                self.dismiss_notice, self.sound.replay, self.extend_notice, self.sound.choice != "silent",
                allow_rephrase=not event.message, tone=tone,
            )
        if event.id in (DRINK_DONE.id, ACHIEVEMENT.id, FACE_AWARD.id, STRETCH.id):
            self.sound.cheer(achievement=event.id in (ACHIEVEMENT.id, FACE_AWARD.id))
        else:
            self.sound.play()
        self.extend_notice()

    def extend_notice(self):
        if self.notice_timer is not None:
            self.root.after_cancel(self.notice_timer)
        duration_ms = 5000 if isinstance(self.notice, CornerNotice) else (7000 if isinstance(self.notice, AchievementNotice) else 8500)
        self.notice_timer = self.root.after(duration_ms, self.dismiss_notice)

    def dismiss_notice(self):
        if self.notice_timer is not None:
            self.root.after_cancel(self.notice_timer)
            self.notice_timer = None
        if self.notice is not None:
            self.sound.stop()
            self.notice.destroy()
            self.notice = None
        self.notice_id = None

    def close(self):
        if self.close_deadline is not None:
            return
        self.close_deadline=perf_counter()+3.5
        self.dismiss_notice()
        self.sound.stop()
        if self.session is not None:
            self.session.stop()
        self.visitors.cancel_enrollment()
        self.root.withdraw()



    def _panel(self,key,title,width,height):
        previous=self.panels.get(key)
        if previous is not None and previous.winfo_exists():
            previous.deiconify()
            previous.lift()
            return None,None
        win=tk.Toplevel(self.root)
        win.title(f"{title} · {APP_NAME}")
        win.configure(bg=PAPER)
        win.iconbitmap(str(ICON_PATH))
        win.transient(self.root)
        self.root.update_idletasks()
        x=max(0,self.root.winfo_rootx()+(self.root.winfo_width()-width)//2)
        y=max(0,self.root.winfo_rooty()+(self.root.winfo_height()-height)//2)
        win.geometry(f"{width}x{height}+{x}+{y}")
        win.minsize(width-30,height-45)
        win.bind("<Escape>",lambda _: win.destroy())
        self.panels[key]=win
        body=tk.Frame(win,bg=PAPER,padx=23,pady=18)
        body.pack(fill="both",expand=True)
        label(body,title,size=20,face=DISPLAY).pack(anchor="w",pady=(0,8))
        return win,body

    def _sync_water(self):
        data = self.habits.data
        count = self.habits.today_count()
        latest = datetime.fromtimestamp(data.drinks[-1]).strftime("%H:%M") if data.drinks else ""
        detail = f"今天 {count} 杯"
        if not data.water_active:
            detail += " · 已暂停"
        elif latest and count:
            detail += f" · 最近 {latest}"
        elif self.session and not self.session.stop_requested.is_set() and data.records_drinks:
            detail += " · 摄像头自动识别"
        elif data.water_detection:
            detail += " · 等待摄像头"
        else:
            detail += " · 自动识别关闭"
        if self.water_error:
            detail = "本地记录待检查"
        if str(self.water_status.cget("text")) != detail:
            self.water_status.configure(text=detail)
        if self.water_view is not None:
            panel, summary, log, undo = self.water_view
            if panel.winfo_exists():
                next_due = data.next_at
                if not data.water_active:
                    timing = "喝水监测已暂停"
                else:
                    timing = ("下次 " + datetime.fromtimestamp(next_due).strftime("%m-%d %H:%M")
                              if next_due is not None else "间隔提醒关闭")
                summary.configure(text=f"今天 {count} 杯　·　{timing}")
                undo.configure(state="normal" if data.drinks else "disabled")
                return log
            self.water_view = None
        return None

    def _update_water_list(self):
        log = self._sync_water()
        if log is None:
            return
        log.delete(0, "end")
        for stamp in reversed(self.habits.data.drinks[-45:]):
            log.insert("end", "  " + datetime.fromtimestamp(stamp).strftime("%Y-%m-%d　%H:%M:%S"))
        if not self.habits.data.drinks:
            log.insert("end", "  识别到杯或瓶贴近嘴唇后，会自动出现在这里。")

    def _save_watch_time(self):
        if not self.watch_buffer:
            return
        try:
            self.habits.add_watch_time(self.watch_day, self.watch_buffer)
            self.watch_buffer = 0.0
            self.water_error = ""
        except (OSError, ValueError) as error:
            self.water_error = str(error)

    def poll_habits(self):
        if self.close_deadline is not None:
            return
        moment = datetime.now()
        day = moment.date().isoformat()
        now = perf_counter()
        delta, self.watch_tick = now - self.watch_tick, now
        if day != self.watch_day:
            self._save_watch_time()
            self.watch_day = day
            self.watch_buffer = 0.0
            self.was_watching = False
        session = self.session
        sample = session.latest if session is not None and not session.stop_requested.is_set() else None
        watching = bool(sample and sample.has_face and not sample.face_reused and now - sample.capture_time < .8)
        watching_bites = watching and self.habits.data.bite_enabled
        if watching_bites and self.was_watching and 0 <= delta <= 3:
            self.watch_buffer += delta
        if self.watch_buffer >= 45 or (self.was_watching and not watching_bites):
            self._save_watch_time()
        self.was_watching = watching_bites
        data = self.habits.data
        if data.break_enabled:
            last_input = last_input_at()
            active = desktop_active() and bool((watching and sample.at_desk)
                                               or (last_input is not None and 0 <= now + .15 - last_input <= 75))
            if self.work_clock.update(now, active=active, minutes=data.break_minutes):
                event = replace(STRETCH, message=f"连续工作 {data.break_minutes} 分钟了，起来走两步。")
                self.feedback.append((now, event))

        available = not self.reveal_on_frame and self.notice is None and not self.pending and not self.feedback
        if available and not self.water_error and moment.hour >= AWARD_HOUR:
            row = data.days.get(day, DayRecord())
            if row.watched_seconds < ZERO_BITE_COVERAGE <= row.watched_seconds + self.watch_buffer:
                self._save_watch_time()
            try:
                awards = self.habits.collect_awards(moment)
                if awards:
                    self.show_notice(self._award_notice(awards, moment))
                    available = False
            except (OSError, ValueError) as error:
                self.water_error = str(error)
        if available and not self.water_error and self.habits.remind_due():
            try:
                self.habits.mark_reminded()
                self.show_notice(WATER)
            except (OSError, ValueError) as error:
                self.water_error = str(error)
        self._sync_water()
        self._sync_bites()
        self._sync_eye_status()
        self._sync_break()
        self._sync_expressions()
        self._sync_achievements(moment)
        self.root.after(1000, self.poll_habits)

    def _award_notice(self, awards, moment):
        count = self.habits.today_count(moment.timestamp())
        if len(awards) > 1:
            return replace(ACHIEVEMENT, label="今日双成就", title="下班撒花！",
                           message=f"合格的水牛 · {count} 杯\n手指今日平安 · 0 次啃手")
        if awards[0] == WATER_AWARD:
            return replace(ACHIEVEMENT, title=AWARD_TITLES[WATER_AWARD],
                           message=f"今天打卡 {count} 杯。恭喜你成为一只合格的水牛！")
        return replace(ACHIEVEMENT, title=AWARD_TITLES[BITE_AWARD],
                       message="今日值班记录 0 次啃手。十根手指集体宣布安全下班！")

    def record_visual_drink(self, timestamp, *, notify=True):
        try:
            self.habits.record(timestamp)
            self.water_error = ""
        except (OSError, ValueError) as error:
            self.water_error = str(error)
            return
        if self.notice_id == WATER.id:
            self.dismiss_notice()
        self._update_water_list()
        if notify and datetime.fromtimestamp(timestamp).date() == datetime.now().date():
            count = self.habits.today_count(timestamp)
            event = replace(DRINK_DONE, title=f"今天第 {count} 杯", message="又喝了一杯，牛蹄给你点个赞。")
            self.feedback.append((perf_counter(), event))

    def reminder_style(self, reminder):
        feature = MONITOR_EVENTS.get(reminder.id, "rules")
        style = self.habits.data.monitor_style(feature) if feature is not None else None
        return style or reminder.style or BUBBLE

    def _sync_bites(self, *, update_log=False):
        count = self.habits.today_bites()
        if self.water_error:
            detail = "本地记录待检查"
        elif not self.habits.data.bite_enabled:
            detail = f"今天 {count} 次 · 已暂停"
        elif count:
            recent = datetime.fromtimestamp(self.habits.data.bites[-1]).strftime("%H:%M")
            detail = f"今天 {count} 次 · 最近 {recent}"
        else:
            status = "自动记录" if self.session and not self.session.stop_requested.is_set() else "摄像头暂停"
            detail = f"今天 0 次 · {status}"
        if self.bite_status.cget("text") != detail:
            self.bite_status.configure(text=detail)
        if self.bite_view is None:
            return
        panel, summary, log = self.bite_view
        if not panel.winfo_exists():
            self.bite_view = None
            return
        summary.configure(text=f"今天识别到 {count} 次")
        if update_log:
            log.delete(0, "end")
            for stamp in reversed(self.habits.data.bites[-45:]):
                log.insert("end", "  " + datetime.fromtimestamp(stamp).strftime("%Y-%m-%d　%H:%M:%S"))
            if not self.habits.data.bites:
                log.insert("end", "  识别到啃手后，时间会出现在这里。")

    def _sync_eye_status(self):
        if not self.habits.data.eye_enabled:
            detail = "开启后识别两种动作"
        elif self.session is None or self.session.stop_requested.is_set():
            detail = "等待摄像头开启"
        else:
            detail = "镜框推扶和眼周反复揉动"
        if self.eye_status.cget("text") != detail:
            self.eye_status.configure(text=detail)

    def _apply_preferences(self):
        if self.session is not None:
            self.session.set_preferences(self.habits.data)

    def set_monitor(self, feature, enabled):
        try:
            self.habits.set_monitor_enabled(feature, enabled)
        except (OSError, ValueError) as error:
            messagebox.showerror("监测开关", str(error), parent=self.root)
            return False
        self.water_error = ""
        self.monitor_changed_at[feature] = time()
        if feature == "guest":
            self.guest_present = False
            self.guest_changed_at = perf_counter()
            self.visitors.set_suspended(not enabled)
        self._apply_preferences()
        if not enabled:
            self.pending = deque((item for item in self.pending if self._event_enabled(item[1])), maxlen=self.pending.maxlen)
            self.feedback = deque((item for item in self.feedback if self._event_enabled(item[1])), maxlen=self.feedback.maxlen)
            if self.notice_id is not None and MONITOR_EVENTS.get(self.notice_id, "rules") == feature:
                self.dismiss_notice()
                self.alert_until = 0.0
            if feature == "break":
                self.work_clock = WorkClock()
            if feature == "bite":
                self.was_watching = False
                self._save_watch_time()
        self._sync_water()
        self._sync_bites()
        self._sync_eye_status()
        self._sync_break()
        self._sync_guest_status()
        self._sync_expressions()
        self._rule_status()
        return True

    def _sync_expressions(self):
        data = self.habits.data
        total = len(data.unlocked_secrets)
        if not data.expression_enabled:
            status = "已暂停"
        elif total == len(SECRET_AWARDS):
            status = "已找齐"
        elif self.session is None or self.session.stop_requested.is_set():
            status = "等待摄像头"
        else:
            status = "值班中"
        detail = f"已发现 {total} / {len(SECRET_AWARDS)} · {status}"
        if self.expression_status.cget("text") != detail:
            self.expression_status.configure(text=detail)

    def _sync_break(self):
        data = self.habits.data
        detail = f"已工作 {int(self.work_clock.elapsed // 60)} 分钟 · {data.break_minutes} 分钟提醒" if data.break_enabled else "活动提醒已关闭"
        if self.break_status.cget("text") != detail:
            self.break_status.configure(text=detail)

    def open_break(self):
        win, body = self._panel("break", "活动提醒", 466, 248)
        if win is None:
            return
        data = self.habits.data
        label(body, "连续用电脑达到设定时间时，提醒站起来动一动。", ink=MUTED, size=10).pack(anchor="w", pady=(0, 12))
        timing = tk.Frame(body, bg=PAPER)
        timing.pack(fill="x", pady=(10, 7))
        label(timing, "连续工作", size=10).pack(side="left", padx=(0, 9))
        minutes = tk.StringVar(win, value=f"{data.break_minutes} 分钟")
        ttk.Combobox(timing, textvariable=minutes, values=tuple(f"{item} 分钟" for item in BREAK_MINUTES),
                     style="Play.TCombobox", state="readonly", width=9, font=(BODY, 10)).pack(side="left")
        label(body, "键鼠活跃或人在镜头前朝向电脑时计时。休息约 5 分钟后重新开始。",
              ink=MUTED, size=9, wraplength=400).pack(anchor="w", pady=(4, 6))

        def save():
            try:
                self.habits.configure_break(self.habits.data.break_enabled, int(minutes.get().split()[0]))
            except (OSError, ValueError) as error:
                messagebox.showerror("活动提醒", str(error), parent=win)
                return
            self._apply_preferences()
            self._sync_break()
            win.destroy()

        button(body, "保存", save, palette="primary", size=9).pack(side="right", pady=(5, 0))

    def _sync_achievements(self, now):
        if self.award_view is None:
            return
        panel, water_text, water_meter, bite_text, bite_meter, secret_count, secret_slots, log = self.award_view
        if not panel.winfo_exists():
            self.award_view = None
            return
        data = self.habits.data
        day = now.date().isoformat()
        current = data.days.get(day, DayRecord())
        water_count = self.habits.today_count(now.timestamp())
        bites = self.habits.today_bites(now.timestamp())
        coverage = current.watched_seconds + (self.watch_buffer if self.watch_day == day else 0)
        water_meter.configure(value=min(WATER_GOAL, water_count))
        water_text.configure(text=f"今日 {water_count} / {WATER_GOAL} 杯" + ("　已达成" if WATER_AWARD in current.awards else ""))
        bite_meter.configure(value=min(ZERO_BITE_COVERAGE, coverage) if not bites else 0)
        played = "　已达成" if BITE_AWARD in current.awards else ""
        bite_text.configure(text=f"今日啃手 {bites} 次 · 有效值班 {int(coverage // 60)} 分钟" + played)
        unlocked = data.unlocked_secrets
        secret_count.configure(text=f"已发现 {len(unlocked)} / {len(SECRET_AWARDS)}" if data.expression_enabled
                               else f"已发现 {len(unlocked)} / {len(SECRET_AWARDS)} · 表情彩蛋暂停")
        for award, (mark, title, description) in secret_slots.items():
            earned = award in unlocked
            mark.configure(text=("眨" if award == SECRET_AWARDS[0] else "牛") if earned else "？")
            title.configure(text=AWARD_TITLES[award] if earned else "未发现的彩蛋")
            description.configure(text=SECRET_DESCRIPTIONS[award] if earned else "解锁后揭晓")
        history = [f"  {day}　{' / '.join(AWARD_TITLES[award] for award in info.awards)}"
                   for day, info in sorted(data.days.items(), reverse=True) if info.awards]
        rows = tuple(history[:60] or ("  达成后的勋章会出现在这里。",))
        if log.get(0, "end") != rows:
            log.delete(0, "end")
            log.insert("end", *rows)

    def open_achievements(self):
        win, body = self._panel("achievements", "成就簿", 555, 554)
        if win is None:
            return
        daily_heading = tk.Frame(body, bg=PAPER)
        daily_heading.pack(fill="x", pady=(0, 13))
        label(daily_heading, "每日成就", size=11, bold=True).pack(side="left")
        label(daily_heading, "每天 18:00 起结算", ink=MUTED, size=9).pack(side="right")
        label(body, AWARD_TITLES[WATER_AWARD], bold=True, size=11).pack(anchor="w")
        water_text = label(body, "", ink=MUTED, size=9)
        water_text.pack(anchor="w", pady=(2, 3))
        water = ttk.Progressbar(body, style="Habit.Horizontal.TProgressbar", maximum=WATER_GOAL)
        water.pack(fill="x", pady=(0, 13))
        label(body, AWARD_TITLES[BITE_AWARD], bold=True, size=11).pack(anchor="w")
        bite_text = label(body, "", ink=MUTED, size=9)
        bite_text.pack(anchor="w", pady=(2, 3))
        bite = ttk.Progressbar(body, style="Habit.Horizontal.TProgressbar", maximum=ZERO_BITE_COVERAGE)
        bite.pack(fill="x")
        label(body, "当天至少有效值班 2 小时，且记录为 0 次。", ink=MUTED, size=9).pack(anchor="w", pady=(4, 12))
        secret_heading = tk.Frame(body, bg=PAPER)
        secret_heading.pack(fill="x", pady=(2, 6))
        label(secret_heading, "隐藏成就", size=11, bold=True).pack(side="left")
        secret_count = label(secret_heading, size=9, ink=MUTED)
        secret_count.pack(side="right")
        tiles = tk.Frame(body, bg=PAPER)
        tiles.pack(fill="x", pady=(0, 12))
        secret_slots = {}
        for column, award in enumerate(SECRET_AWARDS):
            tiles.columnconfigure(column, weight=1, uniform="secret")
            fill = "#edf0e3" if column == 0 else "#f1ede2"
            tile = tk.Frame(tiles, bg=fill, padx=9, pady=9, highlightthickness=1, highlightbackground=LINE)
            tile.grid(row=0, column=column, sticky="nsew", padx=(0, 4) if column == 0 else (4, 0))
            mark = label(tile, "？", paper="#dfe6d1", ink="#596746", size=13, bold=True, padx=5, pady=3)
            mark.pack(side="left", anchor="n", padx=(0, 8))
            copy = tk.Frame(tile, bg=fill)
            copy.pack(side="left", fill="both", expand=True)
            title = label(copy, paper=fill, size=10, bold=True)
            title.pack(anchor="w")
            description = label(copy, paper=fill, ink=MUTED, size=9, wraplength=174)
            description.pack(anchor="w", pady=(2, 0))
            secret_slots[award] = (mark, title, description)
        label(body, "已获得的成就", size=10, bold=True).pack(anchor="w", pady=(0, 4))
        log = tk.Listbox(body, bg="#fbfcf5", fg=INK, font=(BODY, 10), bd=0, highlightthickness=1,
                         highlightbackground=LINE, selectbackground="#e5ecd8", height=5)
        log.pack(fill="both", expand=True)
        self.award_view = (win, water_text, water, bite_text, bite, secret_count, secret_slots, log)
        self._sync_achievements(datetime.now())

    def open_bite(self):
        win, body = self._panel("bite", "防啃手", 471, 388)
        if win is None:
            return
        summary = label(body, "", size=11, ink=MUTED)
        summary.pack(anchor="w", pady=(1, 10))
        label(body, "手指在唇边形成持续啃咬迹象时，自动记一次并提醒。", size=9, ink=MUTED).pack(anchor="w", pady=(0, 10))
        form = tk.Frame(body, bg=PAPER)
        form.pack(fill="x")
        label(form, "提醒样式", size=10, bold=True).pack(side="left", padx=(0, 12))
        self._style_dropdown(form, "bite").pack(side="left")
        label(body, "气泡显示 5 秒。两种样式都使用「声音」里的设置。", size=9, ink=MUTED).pack(anchor="w", pady=(7, 0))
        log = tk.Listbox(body, bg="#fbfcf5", fg=INK, font=(BODY, 10), bd=0, highlightthickness=1,
                         highlightbackground=LINE, selectbackground="#e5ecd8", height=6)
        log.pack(fill="both", expand=True, pady=(10, 0))
        self.bite_view = (win, summary, log)
        self._sync_bites(update_log=True)
        button(body, "关闭", win.destroy, palette="quiet").pack(side="right", pady=(14, 0))

    def open_water(self):
        win, body = self._panel("water", "喝水记录", 595, 481)
        if win is None:
            return
        data = self.habits.data
        enabled = tk.BooleanVar(win, value=data.water_enabled)
        detection = tk.BooleanVar(win, value=data.water_detection)
        hours = tk.StringVar(win, value=f"{data.water_hours} 小时")
        summary = label(body, "", size=12, ink=MUTED)
        summary.pack(anchor="w", pady=(0, 11))
        row = tk.Frame(body, bg=PAPER)
        row.pack(fill="x")
        tk.Checkbutton(row, text="开启间隔提醒", variable=enabled, bg=PAPER, fg=INK,
                       activebackground=PAPER, selectcolor="#e6ecdc", font=(BODY, 10), bd=0).pack(side="left", padx=(0, 9))
        ttk.Combobox(row, values=tuple(f"{v} 小时" for v in INTERVALS), textvariable=hours,
                     style="Play.TCombobox", state="readonly", font=(BODY, 10), width=6).pack(side="left", padx=(0, 7))
        label(row, "提示样式", size=9, ink=MUTED).pack(side="left", padx=(8, 6))
        self._style_dropdown(row, "water").pack(side="left")
        tk.Checkbutton(body, text="摄像头自动识别喝水", variable=detection, bg=PAPER, fg=INK,
                       activebackground=PAPER, selectcolor="#e6ecdc", font=(BODY, 10), bd=0).pack(anchor="w", pady=(8, 0))
        label(body, "主界面开关控制记录和间隔提醒。两种喝水提示共用所选样式。",
              size=9, ink=MUTED).pack(anchor="w", pady=(4, 9))
        logs = tk.Frame(body, bg=PAPER, highlightthickness=1, highlightbackground=LINE)
        logs.pack(fill="both", expand=True)
        scroll = ttk.Scrollbar(logs)
        log = tk.Listbox(logs, bg="#fbfcf5", fg=INK, font=(BODY, 10), bd=0, relief="flat",
                         highlightthickness=0, yscrollcommand=scroll.set, selectbackground="#e5ecd8")
        scroll.configure(command=log.yview)
        log.pack(side="left", fill="both", expand=True, padx=7, pady=5)
        scroll.pack(side="right", fill="y")
        actions = tk.Frame(body, bg=PAPER)
        actions.pack(fill="x", pady=(11, 0))
        def undo_last():
            try:
                self.habits.undo_last()
                self.water_error = ""
            except (OSError, ValueError) as error:
                messagebox.showerror("撤销记录", str(error), parent=win)
                return
            self._update_water_list()
        undo = button(actions, "撤销误记", undo_last, palette="quiet", size=9)
        undo.pack(side="left", padx=(7, 0))
        def save():
            previous = self.habits.data
            try:
                self.habits.configure_water(
                    enabled=enabled.get(), detection=detection.get(), hours=int(hours.get().split()[0]),
                    style=previous.water_style,
                )
            except (OSError, ValueError) as error:
                messagebox.showerror("喝水提醒", str(error), parent=win)
                return
            self.water_error = ""
            if previous.water_detection != self.habits.data.water_detection:
                self.monitor_changed_at["water"] = time()
            self._apply_preferences()
            if ((not self.habits.data.water_enabled and self.notice_id == WATER.id)
                    or (not self.habits.data.records_drinks and self.notice_id == DRINK_DONE.id)):
                self.dismiss_notice()
            if not self.habits.data.records_drinks:
                self.feedback = deque((item for item in self.feedback if item[1].id != DRINK_DONE.id), maxlen=self.feedback.maxlen)
            self._sync_water()
            win.destroy()
        button(actions, "保存提醒设置", save, palette="primary", size=9).pack(side="right")
        self.water_view = (win, summary, log, undo)
        self._update_water_list()


    def open_visitors(self):
        win, body = self._panel("visitors", "谁来过桌前", 680, 531)
        if win is None:
            return
        label(
            body, "登记本人后，近距离正脸停留并出现新的键鼠活动时，才会保存陌生访客的面部照片和时间。",
            size=9, ink=MUTED, wraplength=610,
        ).pack(anchor="w", pady=(0, 11))
        row = tk.Frame(body, bg=PAPER)
        row.pack(fill="x")
        enabled = tk.BooleanVar(win, value=self.visitors.config.enabled)
        def activate():
            try:
                self.visitors.enable(enabled.get())
            except (OSError, ValueError) as error:
                enabled.set(self.visitors.config.enabled)
                messagebox.showinfo("访客记录", str(error), parent=win)
            refresh_status()
        toggle = tk.Checkbutton(row, text="开启陌生访客记录", variable=enabled, command=activate,
                                bg=PAPER, fg=INK, activebackground=PAPER, selectcolor="#e7eddf",
                                font=(BODY, 10), bd=0)
        toggle.pack(side="left")
        def enroll():
            if self.session is None or self.session.latest is None or self.session.stop_requested.is_set():
                messagebox.showinfo("登记本人", "先开始摄像头值班，再靠近并正对镜头。", parent=win)
                return
            try:
                self.visitors.start_enrollment()
            except ValueError as error:
                messagebox.showerror("登记本人", str(error), parent=win)
            refresh_status()
        enroll_button = button(row, "登记本人", enroll, size=9)
        enroll_button.pack(side="right")
        hint = label(body, "", size=9, ink=MUTED, wraplength=600)
        hint.pack(anchor="w", pady=(5, 10))

        browser = tk.Frame(body, bg=PAPER)
        browser.pack(fill="both", expand=True)
        browser.columnconfigure(0, weight=1)
        browser.columnconfigure(1, weight=1)
        browser.rowconfigure(0, weight=1)
        listing = tk.Frame(browser, bg="#fbfcf5", highlightthickness=1, highlightbackground=LINE)
        listing.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        scroll = ttk.Scrollbar(listing)
        entries = tk.Listbox(listing, bg="#fbfcf5", fg=INK, font=(BODY, 10), exportselection=False, bd=0,
                             highlightthickness=0, yscrollcommand=scroll.set, selectbackground="#e5ecd8")
        scroll.configure(command=entries.yview)
        entries.pack(side="left", fill="both", expand=True, padx=6, pady=4)
        scroll.pack(side="right", fill="y")
        portrait = tk.Label(browser, bg="#ebefe4", fg=MUTED, font=(BODY, 10), text="暂无访客照片",
                            highlightthickness=1, highlightbackground=LINE, compound="center")
        portrait.grid(row=0, column=1, sticky="nsew")
        current_files = []
        last_revision = -1
        def display(_=None):
            selection = entries.curselection()
            item = current_files[selection[0]] if selection else None
            if item is None:
                portrait.image = None
                portrait.configure(image="", text="暂无访客照片")
                return
            try:
                with Image.open(item) as original:
                    portrait.image = photo(win, original.convert("RGB"), (244, 218))
                portrait.configure(image=portrait.image, text="")
            except (OSError, ValueError):
                portrait.image = None
                portrait.configure(image="", text="照片暂时无法打开")
        entries.bind("<<ListboxSelect>>", display)

        def reload_list():
            nonlocal current_files, last_revision
            selected = entries.curselection()
            previous = current_files[selected[0]].name if selected else None
            current_files = self.visitors.recent()
            entries.delete(0, "end")
            position = 0
            for i, file in enumerate(current_files):
                date, stamp, _ = file.stem.split("_", 2)
                entries.insert("end", "  " + date + "   " + stamp.replace("-", ":"))
                if file.name == previous:
                    position = i
            if current_files:
                entries.selection_set(position)
                entries.see(position)
            last_revision = self.visitors.revision
            display()

        def refresh_status():
            state = self.visitors.config
            if self.visitors.error:
                detail = self.visitors.status
            elif self.visitors.suspended:
                detail = "「老板来了」已关，访客记录暂停"
            elif state.enroll_at is not None:
                detail = self.visitors.status or "正在登记本人"
            else:
                mode = "记录已开启" if state.enabled else "记录已关闭"
                enrolled = "本人已登记" if state.owner else "等待登记本人"
                detail = f"{mode} · {enrolled}"
                if self.visitors.status:
                    detail += " · " + self.visitors.status
            hint.configure(text=detail)
            enabled.set(state.enabled)
            enroll_button.configure(state="disabled" if self.visitors.suspended or state.enroll_at is not None else "normal")

        def refresh():
            if win.winfo_exists():
                refresh_status()
                if last_revision != self.visitors.revision:
                    reload_list()
                win.after(650, refresh)

        controls = tk.Frame(body, bg=PAPER)
        controls.pack(fill="x", pady=(12, 0))
        def open_folder():
            if self.visitors.photos.is_dir():
                os.startfile(self.visitors.photos)
        def delete_photo():
            selected = entries.curselection()
            if selected and messagebox.askyesno("删除照片", "删除选中的访客照片？", parent=win):
                try:
                    self.visitors.delete(current_files[selected[0]].name)
                except (OSError, ValueError) as error:
                    messagebox.showerror("删除照片", str(error), parent=win)
                reload_list()
        def forget():
            if self.visitors.config.owner and messagebox.askyesno("清除本人登记", "清除本人特征并关闭访客记录？", parent=win):
                try:
                    self.visitors.forget_owner()
                except (OSError, ValueError) as error:
                    messagebox.showerror("清除本人登记", str(error), parent=win)
                refresh_status()
        button(controls, "打开照片目录", open_folder, size=9).pack(side="left")
        button(controls, "删除选中照片", delete_photo, palette="quiet", size=9).pack(side="left", padx=7)
        button(controls, "清除本人登记", forget, palette="quiet", size=9).pack(side="right")
        refresh()


    def open_settings(self):
        win,body=self._panel("settings","设置",565,362)
        if win is None:
            return
        label(body,"摄像头",size=11,bold=True).pack(anchor="w",pady=(7,3))
        tools=tk.Frame(body,bg=PAPER)
        tools.pack(fill="x")
        self.camera_menu=ttk.Combobox(tools,textvariable=self.camera_choice,style="Play.TCombobox",font=(BODY,10))
        self.camera_menu.pack(side="left",fill="x",expand=True)
        self._camera_menu()
        button(tools,"刷新设备",self.refresh_cameras,size=9,height=4).pack(side="left",padx=(9,0))
        label(body,"暂停值班后可切换设备。",size=9,ink=MUTED).pack(anchor="w",pady=(4,10))
        label(body,"啃手提醒速度",size=11,bold=True).pack(anchor="w",pady=(4,3))
        hold=ttk.Combobox(body,textvariable=self.hold_choice,values=list(HOLD_OPTIONS),state="readonly",style="Play.TCombobox",font=(BODY,10))
        hold.pack(anchor="w")
        def update_hold(_):
            if self.session is not None:
                self.session.bite_hold=HOLD_OPTIONS[self.hold_choice.get()]
        hold.bind("<<ComboboxSelected>>",update_hold)
        row=tk.Frame(body,bg=PAPER)
        row.pack(fill="x",pady=(14,0))
        button(row, "防啃手样式", self.open_bite, size=9).pack(side="left")
        button(row, "喝水", self.open_water, size=9).pack(side="left", padx=6)
        button(row, "声音", self.open_sounds, size=9).pack(side="left")
        button(row, "规则", self.open_rules, size=9).pack(side="left", padx=6)
        button(row,"关闭",win.destroy,palette="quiet").pack(side="right")

    def open_about(self):
        win, body = self._panel("about", "产品介绍", 610, 522)
        if win is None:
            return
        header = tk.Frame(body, bg="#e9eedf", padx=13, pady=12)
        header.pack(fill="x", pady=(0, 11))
        icon = photo(win, mascot_art(), (58, 54), Image.Resampling.NEAREST)
        picture = tk.Label(header, image=icon, bg="#e9eedf", bd=0)
        picture.image = icon
        picture.pack(side="left", padx=(0, 11))
        titles = tk.Frame(header, bg="#e9eedf")
        titles.pack(side="left", fill="x", expand=True)
        label(titles, f"{APP_NAME}  v{APP_VERSION}", paper="#e9eedf", size=16, bold=True).pack(anchor="w")
        label(titles, SYSTEM_REQUIREMENTS, paper="#e9eedf", ink=MUTED, size=9).pack(anchor="w", pady=(3, 0))
        label(body, DESCRIPTION, size=10, wraplength=552).pack(anchor="w", pady=(0, 7))
        for heading, copy in FEATURES:
            row = tk.Frame(body, bg=PAPER)
            row.pack(fill="x", pady=1)
            label(row, heading, size=9, bold=True, width=9).pack(side="left")
            label(row, copy, size=9, ink=MUTED).pack(side="left")
        label(body, PRIVACY, size=9, ink=MUTED).pack(anchor="w", pady=(7, 10))
        tk.Frame(body, bg=LINE, height=1).pack(fill="x")
        label(body, COMPANY, size=12, bold=True).pack(anchor="w", pady=(10, 2))
        label(body, f"{SLOGAN}。{'、'.join(SERVICES)}。", size=10, ink=MUTED).pack(anchor="w")
        label(body, f"制作人  {CREATORS}", size=10).pack(anchor="w", pady=(8, 6))
        links = tk.Frame(body, bg=PAPER)
        links.pack(fill="x")
        button(links, WEBSITE, lambda: webbrowser.open(WEBSITE_URL), size=9).pack(side="left")
        button(links, COOPERATION_EMAIL, lambda: webbrowser.open(f"mailto:{COOPERATION_EMAIL}"), size=9).pack(side="left", padx=6)
        notices = ROOT / "licenses" / "THIRD-PARTY-NOTICES.txt"
        if notices.is_file():
            button(links, "第三方许可", lambda: os.startfile(notices), palette="quiet", size=9).pack(side="right")

    def open_sounds(self):
        win,body=self._panel("sounds","小猪哨声",594,512)
        if win is None:
            return
        modes=tk.Frame(body,bg=PAPER)
        modes.pack(fill="x",pady=(2,11))
        label(modes,"提醒音效",size=10,bold=True).pack(side="left",padx=(0,9))
        choice=tk.StringVar(win,value=SOUND_NAMES[self.sound.choice])
        menu=ttk.Combobox(modes,textvariable=choice,values=[SOUND_NAMES[item] for item in self.sound.options()],
                          style="Play.TCombobox",state="readonly",font=(BODY,10),width=21)
        menu.pack(side="left")
        def choose(_):
            key=next(key for key,title in SOUND_NAMES.items() if choice.get()==title)
            try:
                self.sound.select(key)
            except (OSError,ValueError) as error:
                messagebox.showerror("声音",str(error),parent=win)
                choice.set(SOUND_NAMES[self.sound.choice])
        menu.bind("<<ComboboxSelected>>",choose)
        box=tk.Frame(body,bg=PAPER,highlightthickness=1,highlightbackground=LINE)
        box.pack(fill="both",expand=True)
        scroll=ttk.Scrollbar(box)
        entries=tk.Listbox(box,font=(BODY,11),bg="#fcfcf5",fg=INK,selectbackground="#dce9ca",
                           selectforeground=INK,relief="flat",bd=0,highlightthickness=0,activestyle="none",
                           yscrollcommand=scroll.set,exportselection=False)
        scroll.configure(command=entries.yview)
        entries.pack(side="left",fill="both",expand=True,padx=8,pady=5)
        scroll.pack(side="right",fill="y")
        for index,(title,_) in enumerate(self.sound.piglets,1):
            entries.insert("end",f"  {index:02}    {title}")
        if self.sound.piglets:
            entries.selection_set(0)
        def preview(_=None):
            selected=entries.curselection()
            if selected:
                self.sound.preview(selected[0])
        entries.bind("<Double-Button-1>",preview)
        entries.bind("<Return>",preview)
        actions=tk.Frame(body,bg=PAPER)
        actions.pack(fill="x",pady=(12,0))
        button(actions,"听选中",preview,palette="primary",size=9).pack(side="left")
        button(actions,"随机试听",self.sound.preview,size=9).pack(side="left",padx=(6,0))
        button(actions,"来源与许可",lambda: os.startfile(ROOT/"design"/"小猪哨声-20款试听.html"),palette="quiet",size=9).pack(side="left",padx=(5,0))
        def import_clip():
            path=filedialog.askopenfilename(parent=win,title="导入本地音频",
                                            filetypes=[("音频文件","*.wav *.mp3 *.m4a *.aac *.flac *.ogg")])
            if not path:
                return
            try:
                self.sound.import_clip(Path(path))
                menu.configure(values=[SOUND_NAMES[item] for item in self.sound.options()])
                choice.set(SOUND_NAMES[self.sound.choice])
            except (OSError,RuntimeError,ValueError) as error:
                messagebox.showerror("导入音频",str(error),parent=win)
        button(actions,"导入",import_clip,size=9).pack(side="right")

    def open_rules(self):
        win,body=self._panel("rules","本地规则",643,410)
        if win is None:
            return
        label(body,"手靠近设定区域并持续停留时提醒。主界面选「逐条」时，使用下表各自的样式。",
              size=10,ink=MUTED).pack(anchor="w",pady=(0,9))
        targets={"eyes":"两眼","left_eye":"左眼","right_eye":"右眼","mouth":"嘴边","nose":"鼻子"}
        table=ttk.Treeview(body,columns=("target","state","style"),style="Play.Treeview",height=6,selectmode="browse")
        table.heading("#0",text="规则")
        table.heading("target",text="识别部位")
        table.heading("state",text="状态")
        table.heading("style", text="提醒样式")
        table.column("#0",width=205,stretch=True)
        table.column("target",width=89,stretch=False,anchor="center")
        table.column("state",width=83,stretch=False,anchor="center")
        table.column("style",width=133,stretch=False,anchor="center")
        table.pack(fill="both",expand=True)
        conditions = label(body, "选择一条查看触发条件。", size=9, ink=MUTED)
        conditions.pack(anchor="w", pady=(7, 0))
        def describe(_=None):
            selected = table.selection()
            rule = next((item for item in self.rules if selected and item.reminder.id == selected[0]), None)
            if rule is None:
                conditions.configure(text="选择一条查看触发条件。")
            else:
                conditions.configure(text=f"指尖靠近{targets[rule.target]}并持续 {rule.hold_seconds:g} 秒时提醒。")
        table.bind("<<TreeviewSelect>>", describe)
        def present(selection=None):
            values=table.get_children()
            if values:
                table.delete(*values)
            for item in self.rules:
                table.insert("","end",iid=item.reminder.id,text=item.reminder.label,
                             values=(targets[item.target], "已启用" if item.enabled else "已关闭", STYLE_NAMES[item.reminder.style]))
            if selection and table.exists(selection):
                table.selection_set(selection)
            elif self.rules:
                table.selection_set(self.rules[0].reminder.id)
            self._rule_status()
            describe()
        def reload_rules():
            selected=table.selection()
            if not self._fetch_rules():
                messagebox.showerror("读取本地规则",self.rule_detail,parent=win)
            present(selected[0] if selected else None)
        def change_selected(field):
            selected=table.selection()
            if not selected:
                return
            try:
                document,_=load_rule_document()
                item=next((item for item in document["rules"] if item["id"]==selected[0]),None)
                if item is None:
                    raise ValueError("规则列表刚刚被编辑，请重新载入。")
                if field == "style":
                    item["style"] = BUBBLE if item.get("style", BUBBLE) == DIALOG else DIALOG
                else:
                    item["enabled"] = not item["enabled"]
                self.rules=write_rule_document(document)
                self.rule_detail=""
                if self.session is not None:
                    self.session.rules=self.rules
                if field == "enabled" and not item["enabled"] and self.notice_id == selected[0]:
                    self.dismiss_notice()
                present(selected[0])
            except (OSError,ValueError) as error:
                messagebox.showerror("写入本地规则",str(error),parent=win)
        def edit():
            try:
                subprocess.Popen(["notepad.exe",str(RULES_PATH)])
            except OSError as error:
                messagebox.showerror("打开规则文件",str(error),parent=win)
        actions=tk.Frame(body,bg=PAPER)
        actions.pack(fill="x",pady=(11,0))
        button(actions, "启用或关闭", lambda: change_selected("enabled"), palette="primary", size=9).pack(side="left")
        button(actions, "换提示样式", lambda: change_selected("style"), size=9).pack(side="left", padx=(7,0))
        button(actions,"编辑规则文件",edit,size=9).pack(side="left",padx=7)
        button(actions,"重新载入",reload_rules,size=9).pack(side="left")
        present()

