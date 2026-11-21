"""Native artwork, startup state, and game-style reminder views."""

import ctypes
from ctypes import wintypes
from math import cos, exp, sin
from random import Random
from time import perf_counter
import tkinter as tk

from PIL import Image, ImageDraw, ImageTk

from desktop import APP_NAME, SIGNALS, Signal, brand_mark, mascot_art
from runtime_paths import FROZEN


PAPER = "#f8f9f1"
SURROUND = "#e9ede1"
INK = "#283225"
MUTED = "#53614d"
LINE = "#dce2d3"
DARK = "#24362a"
BODY = "Microsoft YaHei UI" if FROZEN else "HarmonyOS Sans SC"
DISPLAY = "Microsoft YaHei UI Light"
SERIF = "SimSun"


def label(parent, value="", *, paper=PAPER, ink=INK, size=11, face=BODY, bold=False, **options):
    return tk.Label(parent, text=value, bg=paper, fg=ink, anchor="w", justify="left", bd=0,
                    font=(face, size, "bold" if bold else "normal"), **options)


def button(parent, value, call, *, palette="light", padding=12, height=6, size=10):
    fill, ink, hover = {
        "light": ("#ecf0e5", "#324731", "#e1ead7"),
        "quiet": (PAPER, "#4a6143", "#eaf0e0"),
        "primary": ("#39513a", "#f6f8ed", "#49684b"),
        "dark": ("#314737", "#eef4e2", "#47634a"),
    }[palette]
    return tk.Button(parent, text=value, command=call, bg=fill, fg=ink, activebackground=hover,
                     activeforeground=ink, disabledforeground="#738172", cursor="hand2",
                     font=(BODY, size), bd=0, relief="flat", padx=padding, pady=height,
                     highlightthickness=1, highlightbackground=fill, highlightcolor="#668652")


def photo(master, art, size, sampler=Image.Resampling.LANCZOS):
    art = art.copy()
    art.thumbnail(size, sampler)
    return ImageTk.PhotoImage(art, master=master)


def _draw_grid(draw, left, top, right, bottom, stride=35):
    for x in range(left, right, stride):
        draw.line((x, top, x, bottom), fill="#344439")
    for y in range(top, bottom, stride):
        draw.line((left, y, right, y), fill="#344439")


def motion_enabled():
    enabled = wintypes.BOOL(True)
    ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(enabled), 0)
    return bool(enabled.value)


def spring_progress(elapsed, *, stiffness=27, damping=.81):
    """Evaluate a damped spring at elapsed seconds, independently of display refresh."""
    oscillation = stiffness * (1 - damping ** 2) ** .5
    decay = stiffness * damping
    return 1 - exp(-decay * elapsed) * (cos(oscillation * elapsed) + decay / oscillation * sin(oscillation * elapsed))


def _blend(first, second, amount):
    fraction = min(1.0, max(0.0, amount))
    colors = [round(int(a, 16) * (1 - fraction) + int(b, 16) * fraction)
              for a, b in zip((first[i:i+2] for i in (1, 3, 5)), (second[i:i+2] for i in (1, 3, 5)))]
    return "#%02x%02x%02x" % tuple(colors)


class MotionSwitch(tk.Canvas):
    def __init__(self, parent, enabled, change):
        super().__init__(parent, width=60, height=29, bg=PAPER, highlightthickness=0, borderwidth=0,
                         cursor="hand2", takefocus=1)
        self.enabled = enabled
        self.position = float(enabled)
        self.change = change
        self.frame = None
        self.focused = self.pressed = False
        self.track = self.create_line(14, 14, 46, 14, width=24, capstyle="round")
        self.state_text = self.create_text(42, 14, font=(BODY, -10, "bold"))
        self.knob = self.create_oval(0, 0, 0, 0, fill="#fafbf4", outline="#fdfdf9")
        self.bind("<FocusIn>", lambda _: self._focus(True))
        self.bind("<FocusOut>", lambda _: self._focus(False))
        self.bind("<ButtonPress-1>", self._press)
        self.bind("<ButtonRelease-1>", self._release)
        self.bind("<space>", self._toggle)
        self.bind("<Return>", self._toggle)
        self._paint()

    def _focus(self, focused):
        self.focused = focused
        self._paint()

    def _press(self, _):
        self.focus_set()
        self.pressed = True
        self._paint()

    def _release(self, event):
        self.pressed = False
        if 0 <= event.x < self.winfo_width() and 0 <= event.y < self.winfo_height():
            return self._toggle(event)
        self._paint()

    def _toggle(self, _):
        target = not self.enabled
        if self.change(target):
            self.enabled = target
            if self.frame is not None:
                self.after_cancel(self.frame)
                self.frame = None
            if motion_enabled():
                self._animate(perf_counter(), self.position)
            else:
                self.position = float(target)
        self._paint()
        return "break"

    def _animate(self, started, origin):
        elapsed = perf_counter() - started
        target = float(self.enabled)
        self.position = target if elapsed >= .31 else origin + (target - origin) * spring_progress(elapsed)
        self._paint()
        self.frame = self.after(16, self._animate, started, origin) if elapsed < .31 else None

    def _paint(self):
        off, on = ("#c6cebd", "#476e4e") if self.focused else ("#d5ddce", "#587f56")
        self.itemconfigure(self.track, fill=_blend(off, on, self.position))
        self.itemconfigure(self.state_text, text="开" if self.enabled else "关", fill="#f9fbf3" if self.enabled else "#4c5d46")
        self.coords(self.state_text, 17 if self.enabled else 43, 14)
        center = 14 + self.position * 32
        radius = 8 if self.pressed else 9
        self.coords(self.knob, center - radius, 14 - radius, center + radius, 14 + radius)

    def destroy(self):
        if self.frame is not None:
            self.after_cancel(self.frame)
        super().destroy()


class Splash:
    def __init__(self, root):
        self.root = root
        self.opened = perf_counter()
        self.finished = False
        self.frame_token = None
        self.toplevel = window = tk.Toplevel(root)
        window.withdraw()
        window.overrideredirect(True)
        window.attributes("-topmost", True)
        window.attributes("-alpha", 0.0 if motion_enabled() else 1.0)
        w, h = 456, 276
        x, y = (root.winfo_screenwidth() - w) // 2, (root.winfo_screenheight() - h) // 2
        window.geometry(f"{w}x{h}+{x}+{y}")
        self.canvas = board = tk.Canvas(window, bg="#f8f9f1", width=w, height=h, highlightthickness=0)
        board.pack(fill="both", expand=True)
        board.create_rectangle(1, 1, w - 2, h - 2, outline="#becbb6")
        board.create_rectangle(9, 9, w - 10, h - 10, outline="#e8ebdf")
        board.create_line(33, 38, w - 33, 38, fill="#e5ebdd")
        board.create_text(34, 26, text=APP_NAME, anchor="w", fill=MUTED, font=(BODY, -11))
        self.mark = photo(window, brand_mark(), (120, 50))
        board.create_image(w // 2, 89, image=self.mark)
        board.create_text(w//2, 125, text="派徕设计", fill="#495545", font=(BODY, -13))
        self.heading = board.create_text(w//2, 165, text="正在加载摄像头", fill=INK, font=(DISPLAY, -26))
        self.detail = board.create_text(w//2, 198, text="正在初始化本地环境", fill=MUTED, font=(BODY, -12))
        board.create_line(88, 228, 368, 228, fill="#dce4d2", width=3, capstyle="round")
        self.progress = board.create_line(88, 228, 144, 228, fill="#77925e", width=3, capstyle="round")
        window.bind("<Escape>", lambda _: root.destroy())
        window.deiconify()
        self._animate()

    def show(self, detail, heading="正在加载摄像头"):
        if not self.finished:
            self.canvas.itemconfigure(self.detail, text=detail)
            self.canvas.itemconfigure(self.heading, text=heading)

    def _animate(self):
        elapsed = perf_counter() - self.opened
        if motion_enabled():
            self.toplevel.attributes("-alpha", min(1.0, elapsed / .24))
            step = (elapsed % 1.6) / 1.6
            x = 88 + 223 * step
            self.canvas.coords(self.progress, x, 228, x + 56, 228)
            self.frame_token = self.root.after(34, self._animate)

    def finish(self, reveal):
        if self.finished:
            return
        self.finished = True
        wait = max(0, round((.55 - (perf_counter() - self.opened)) * 1000))
        def dissolve():
            if self.frame_token is not None:
                self.root.after_cancel(self.frame_token)
                self.frame_token = None
            reveal()
            started = perf_counter()
            def fade():
                remaining = max(0, 1 - (perf_counter() - started) / .18)
                if remaining <= 0 or not motion_enabled():
                    self.toplevel.destroy()
                    return
                self.toplevel.attributes("-alpha", remaining)
                self.root.after(22, fade)
            fade()
        self.root.after(wait, dissolve)


class WatchCard(tk.Canvas):
    def __init__(self, parent, change_monitoring):
        super().__init__(parent, bg=PAPER, highlightthickness=0, width=0, height=0)
        self.cow = mascot_art()
        self.backdrop = None
        self.current = (Signal.OFF, "准备值班", "正在加载摄像头", "查找摄像头")
        self.button = button(self, "暂停", change_monitoring, palette="dark", height=7)
        self.bind("<Configure>", lambda _: self._compose())

    def _compose(self):
        w, h = self.winfo_width(), self.winfo_height()
        if w < 76 or h < 80:
            return
        self.delete("all")
        base = Image.new("RGB", (w, h), PAPER)
        panel = Image.new("RGB", (w, h), "#253528")
        draw = ImageDraw.Draw(panel)
        step = 35
        _draw_grid(draw, step * max(1,(w//3)//step), 1, w, h, step)
        center = (round(w * .742), round(h * .49))
        radius = min(round(w * .227), round(h * .378))
        for offset, color in ((15, "#53634c"), (7, "#687b56")):
            r = radius + offset
            draw.ellipse((center[0]-r, center[1]-r, center[0]+r, center[1]+r), outline=color, width=1)
        for offset, color in ((0, "#aabe83"), (2, "#b7cb8e"), (7, "#bfd39a")):
            r = radius - offset
            draw.ellipse((center[0]-r, center[1]-r, center[0]+r, center[1]+r), fill=color)
        r = max(1, radius - 15)
        draw.ellipse((center[0]-r, center[1]-r, center[0]+r, center[1]+r), outline="#a7ba83")
        for x, y, hx, vy in ((w*.50,h*.095,1,1),(w*.949,h*.095,-1,1),(w*.50,h*.89,1,-1),(w*.949,h*.89,-1,-1)):
            draw.line((x+12*hx,y,x,y,x,y+12*vy), fill="#768976", width=1)
        mask = Image.new("L", (w,h))
        ImageDraw.Draw(mask).rounded_rectangle((0,0,w-1,h-1),radius=16,fill=255)
        cow = self.cow.copy()
        cow.thumbnail((round(radius * 2.06), round(radius * 1.76)),Image.Resampling.NEAREST)
        panel.paste(cow,(center[0]-cow.width//2,center[1]-cow.height//2),cow)
        base.paste(panel,(0,0),mask)
        self.backdrop = ImageTk.PhotoImage(base, master=self)
        self.create_image(0, 0, image=self.backdrop, anchor="nw")
        x = 28 if w > 500 else 20
        top = max(32, round(h*.22))
        self.dot = self.create_oval(x+1,top+4,x+10,top+13, fill="#a3d65d", outline="")
        self.create_text(x+20,top+8,text="值班台",anchor="w", fill="#dce8d3",font=(BODY,-13))
        self.title = self.create_text(x,top+28,text="",anchor="nw",fill="#f4f6e8",font=(DISPLAY,-min(60,round(w*.085))))
        width = max(145, round(w*.41) - x//2)
        self.subtitle = self.create_text(x,top+104,text="",anchor="nw",fill="#d6e1d1",font=(BODY,-14),width=width)
        self.create_window(x, min(h-90,top+160),window=self.button,anchor="nw")
        self.note = self.create_text(x,h-22,text="",anchor="w", fill="#c9d6c2",font=(BODY,-12),width=w-2*x)
        self.update_status(*self.current)

    def update_status(self, signal, title, subtitle, note):
        self.current = signal, title, subtitle, note
        if self.backdrop is not None:
            self.itemconfigure(self.dot, fill=SIGNALS[signal][1])
            self.itemconfigure(self.title, text=title)
            self.itemconfigure(self.subtitle, text=subtitle)
            self.itemconfigure(self.note, text=note)


def _show_popup(window, x, y, width, height):
    window.update_idletasks()
    user = ctypes.windll.user32
    user.GetAncestor.restype = wintypes.HWND
    hwnd = user.GetAncestor(wintypes.HWND(window.winfo_id()), 2)
    if hwnd:
        handle = wintypes.HWND(hwnd)
        style = user.GetWindowLongW(handle, -20)
        user.SetWindowLongW(handle, -20, style | 0x08000080)
    window.deiconify()
    if hwnd:
        user.SetWindowPos(handle, wintypes.HWND(-1), x, y, width, height, 0x0210)
    else:
        window.geometry(f"{width}x{height}+{max(x,0)}+{max(y,0)}")


class CornerNotice:
    def __init__(self, root, work_area, event, caption, close, *, tone="guard"):
        width, height = 342, 139
        x = work_area.right - width - 13
        y = work_area.bottom - height - 13
        self.window = window = tk.Toplevel(root)
        window.withdraw()
        window.overrideredirect(True)
        window.attributes("-topmost", True)
        window.geometry(f"{width}x{height}+0+0")
        key = "#030105"
        paper, shadow, border, inset, circle, accent, ink = {
            "guard": ("#f3ecd6", "#ba9c69", "#b59158", "#d9c9a1", "#e0cf9d", "#79503b", "#35291e"),
            "cheer": ("#edf3df", "#96ad7b", "#93a978", "#c8d9ad", "#d8e7ba", "#486543", "#2e4833"),
            "rest": ("#e9f0ea", "#93a9a0", "#90aaa0", "#c9dbd1", "#cbded0", "#426258", "#2b443b"),
        }[tone]
        window.attributes("-transparentcolor", key)
        board = tk.Canvas(window, bg=key, highlightthickness=0)
        board.pack(fill="both", expand=True)
        card = Image.new("RGB", (width, height), key)
        draw = ImageDraw.Draw(card)
        draw.rounded_rectangle((38, 31, width - 2, height - 2), 15, fill=shadow)
        draw.rounded_rectangle((37, 27, width - 5, height - 6), 14, fill=paper, outline=border, width=1)
        draw.rounded_rectangle((43, 32, width - 11, height - 12), 10, outline=inset, width=1)
        draw.ellipse((15, 29, 113, 129), fill=circle, outline=border, width=1)
        self.background = ImageTk.PhotoImage(card, master=window)
        self.cow = photo(window, mascot_art(stern=tone == "guard", cheerful=tone == "cheer"),
                         (123, 121), Image.Resampling.NEAREST)
        self.frame = None
        board.create_image(0, 0, image=self.background, anchor="nw")
        cow = board.create_image(63, 63, image=self.cow)
        board.create_polygon(130, 43, 134, 47, 130, 51, 126, 47, fill=accent, outline="")
        board.create_text(141, 48, text=event.label, anchor="w", fill=accent, font=(BODY, -11))
        title_y = 58 if len(event.title) > 12 else 61
        board.create_text(127, title_y, text=event.title, anchor="nw", fill=ink,
                          width=183, font=(SERIF, -20 if len(event.title) <= 9 else -15, "bold"))
        board.create_text(128, 91, text=caption, anchor="nw", fill=accent, width=192, font=(BODY, -12))
        close_button = tk.Button(board, text="×", command=close, bg=paper, fg=accent,
                                 activebackground=inset, activeforeground=ink,
                                 cursor="hand2", font=(BODY, -14), padx=5, pady=0, relief="flat",
                                 bd=0, highlightthickness=1, highlightcolor=border, highlightbackground=paper)
        board.create_window(width - 16, 35, window=close_button, anchor="ne")
        window.bind("<Escape>", lambda _: close())
        _show_popup(window, x, y, width, height)
        if tone == "cheer" and motion_enabled():
            started = perf_counter()

            def settle():
                elapsed = perf_counter() - started
                board.coords(cow, 63, 63 + 11 * (1 - spring_progress(elapsed, stiffness=20)))
                self.frame = window.after(16, settle) if elapsed < .39 else None

            settle()

    def destroy(self):
        if self.frame is not None:
            self.window.after_cancel(self.frame)
        self.window.destroy()


class AchievementNotice:
    def __init__(self, root, work_area, event, close):
        width, height = 476, 241
        x = (work_area.left + work_area.right - width) // 2
        y = (work_area.top + work_area.bottom - height) // 2
        self.frame = None
        self.window = window = tk.Toplevel(root)
        window.withdraw()
        window.overrideredirect(True)
        window.attributes("-topmost", True)
        window.geometry(f"{width}x{height}+0+0")
        key, self.paper = "#030105", "#f1f4e5"
        window.attributes("-transparentcolor", key)
        self.board = board = tk.Canvas(window, bg=key, highlightthickness=0)
        board.pack(fill="both", expand=True)
        card = Image.new("RGB", (width, height), key)
        draw = ImageDraw.Draw(card)
        draw.rounded_rectangle((6, 10, 469, 235), 23, fill="#90a486")
        draw.rounded_rectangle((3, 4, 466, 230), 22, fill=self.paper, outline="#a4b68e", width=1)
        draw.rounded_rectangle((11, 12, 458, 222), 16, outline="#d9e2cc", width=1)
        draw.ellipse((27, 44, 188, 208), fill="#e1ebd0", outline="#bfcea7")
        draw.ellipse((36, 53, 180, 198), outline="#ccd9b8")
        draw.line((206, 139, 427, 139), fill="#d2dbc5")
        self.background = ImageTk.PhotoImage(card, master=window)
        board.create_image(0, 0, image=self.background, anchor="nw")
        self.particles = []
        colors = ("#b68b55", "#84995a", "#bb86a0", "#8887a9", "#c69d6a")
        rng = Random(21)
        for index in range(18):
            origin_x = 106 if index % 2 else 323
            origin_y = 120 if index % 2 else 73
            mark = board.create_polygon(0, 0, 0, 0, 0, 0, state="hidden", outline="")
            self.particles.append((mark, origin_x, origin_y, rng.uniform(-90, 91), rng.uniform(-116, -32),
                                   rng.uniform(1.6, 3.8), rng.uniform(0, .18), colors[index % len(colors)]))
        self.cow = photo(window, mascot_art(cheerful=True), (167, 157), Image.Resampling.NEAREST)
        self.cow_id = board.create_image(108, 123, image=self.cow)
        board.create_text(207, 69, text=event.label, anchor="w", fill="#5c7756", font=(BODY, -13, "bold"))
        board.create_text(205, 104, text=event.title, anchor="w", fill="#2b472e", font=(DISPLAY, -29))
        board.create_text(207, 153, text=event.message, anchor="nw", fill="#496048", width=221,
                          font=(BODY, -13), justify="left")
        close_button = tk.Button(board, text="×", command=close, bg=self.paper, fg="#496048",
                                 activebackground="#dce6ce", cursor="hand2", font=(BODY, -17), padx=6,
                                 pady=0, relief="flat", bd=0, highlightthickness=0)
        board.create_window(443, 24, window=close_button)
        window.bind("<Escape>", lambda _: close())
        _show_popup(window, x, y, width, height)
        if motion_enabled():
            self._animate(perf_counter())

    def _animate(self, started):
        elapsed = perf_counter() - started
        self.board.coords(self.cow_id, 108, 123 + 12 * (1 - spring_progress(elapsed, stiffness=17)))
        for mark, x, y, dx, dy, size, delay, color in self.particles:
            t = elapsed - delay
            if t < 0 or t >= 1.30:
                self.board.itemconfigure(mark, state="hidden")
                continue
            px, py = x + dx * t, y + dy * t + 105 * t ** 2
            radius = size * (1 - max(0, t - .58) / .72)
            if not (14 < px < 452 and 15 < py < 217):
                self.board.itemconfigure(mark, state="hidden")
                continue
            tilt = radius * cos(t * 5)
            self.board.coords(mark, px, py - radius, px + tilt, py, px, py + radius, px - tilt, py)
            self.board.itemconfigure(mark, state="normal", fill=_blend(color, self.paper, max(0, t - .50)))
        self.frame = self.window.after(20, self._animate, started) if elapsed < 1.48 else None

    def destroy(self):
        if self.frame is not None:
            self.window.after_cancel(self.frame)
        self.window.destroy()



class GameNotice:
    def __init__(self, root, monitor, event, next_line, close, replay, extend, can_replay,
                 *, allow_rephrase=True, tone="guard"):
        monitor_w = monitor.right - monitor.left
        monitor_h = monitor.bottom - monitor.top
        width = min(960, monitor_w - 40, max(620, round(monitor_w * .62)))
        height = min(390, monitor_h - 40, max(285, round(width * .405)))
        x = monitor.left + (monitor_w - width)//2
        y = monitor.top + (monitor_h - height)//2
        window = self.window = tk.Toplevel(root)
        window.withdraw()
        window.overrideredirect(True)
        window.attributes("-topmost", True)
        window.attributes("-alpha", 0.0 if motion_enabled() else 1.0)
        window.geometry(f"{width}x{height}+0+0")
        self.began = perf_counter()
        self.motion = motion_enabled()
        self.animation = None
        self.typing = None
        if tone == "cheer":
            paper, metal, rich, seal = "#edf3df", "#a4b489", "#456b41", "#d8e7ba"
        elif tone == "rest" or event.id != "bite":
            paper, metal, rich, seal = "#eee9d4", "#a39669", "#3f6349", "#d6dbaa"
        else:
            paper, metal, rich, seal = "#f2e8ce", "#bc9b63", "#814b35", "#ecd39d"
        board = self.board = tk.Canvas(window,bg=paper,highlightthickness=0)
        board.pack(fill="both",expand=True)
        for inset, color, thickness in ((1,"#8f7755",2),(5,"#cfbb92",4),(11,metal,1),(15,"#f7efdb",1)):
            board.create_rectangle(inset,inset,width-inset,height-inset,outline=color,width=thickness)
        for xx, yy, dx, dy in ((12,12,1,1),(width-12,12,-1,1),(width-12,height-12,-1,-1),(12,height-12,1,-1)):
            for depth, length in ((2,20),(7,13)):
                ax, ay = xx + depth*dx, yy + depth*dy
                board.create_line(ax+length*dx,ay,ax,ay,ax,ay+length*dy,fill=metal,width=1)
        pivot_x, pivot_y = round(width*.224), round(height*.49)
        radius = min(round(width*.134),round(height*.34))
        for addon, color, edge in ((7, "#f8f0d9",metal),(0,seal,metal),(-10,seal,"#b6a77d")):
            r = radius+addon
            board.create_oval(pivot_x-r,pivot_y-r,pivot_x+r,pivot_y+r,fill=color,outline=edge,width=1)
        face = mascot_art(stern=tone == "guard", cheerful=tone == "cheer")
        self.face = photo(window,face,(radius*2+14,radius*2),Image.Resampling.NEAREST)
        board.create_image(pivot_x,pivot_y,image=self.face)
        badge_y = pivot_y + radius + 4
        board.create_rectangle(pivot_x-24,badge_y-11,pivot_x+24,badge_y+12,fill="#48392c",outline=metal)
        board.create_text(pivot_x,badge_y+1,text="哨 兵",fill="#faeed2",font=(BODY,-12))
        column = round(width*.445)
        self.body_width = width - column - 40
        board.create_polygon(column,49,column+4,45,column+8,49,column+4,53,fill=rich,outline="")
        board.create_text(column+17,49,anchor="w",text=event.label,fill=rich,font=(BODY,-14,"bold"))
        title_size = min(55, round(self.body_width / max(5.5, len(event.title))))
        board.create_text(column,69,anchor="nw",text=event.title,fill="#443221",font=(SERIF,-title_size,"bold"),width=self.body_width)
        self.caption = board.create_text(column,round(height*.46),anchor="nw",text="",fill="#4c3f31",
                                         width=self.body_width,font=(SERIF,-min(22,round(width*.026))))
        def action(name, call, fill, fg, hover, padding):
            return tk.Button(board,text=name,command=call,bd=0,relief="flat",cursor="hand2",bg=fill,fg=fg,
                             activebackground=hover,activeforeground=fg,font=(BODY,-12),padx=padding,pady=7,
                             highlightthickness=1,highlightbackground=metal,highlightcolor=rich)
        ack = action("知道了", close, rich, "#fff8e8", "#55442f", 13)
        def replace_line():
            self.write_line(next_line())
            extend()
        def repeat():
            replay()
            extend()
        hear = action("重听铃声" if tone in ("cheer", "rest") else "重听声音", repeat, "#f9f1dd", "#654e37", "#fff9ea", 10)
        if not can_replay:
            hear.config(state="disabled")
        action_y = height - 43
        board.create_window(column, action_y, window=ack,anchor="w")
        if allow_rephrase:
            rotate = action("换一句", replace_line, "#f9f1dd", "#654e37", "#fff9ea", 10)
            board.create_window(column + 78, action_y, window=rotate, anchor="w")
        board.create_window(column + (147 if allow_rephrase else 78), action_y, window=hear, anchor="w")
        xbutton = action("×", close, "#f9f1df", "#65543d", "#fff9ed", 8)
        board.create_window(width-18, 18, window=xbutton,anchor="ne")
        window.bind("<Escape>",lambda _: close())
        _show_popup(window, x, y, width, height)
        self.write_line(next_line())
        self._fade()

    def _fade(self):
        if not self.motion:
            return
        fraction = min(1, (perf_counter()-self.began)/.23)
        self.window.attributes("-alpha",1-(1-fraction)**3)
        if fraction < 1:
            self.animation = self.window.after(18,self._fade)
        else:
            self.animation = None

    def write_line(self, line):
        if self.typing is not None:
            self.window.after_cancel(self.typing)
            self.typing = None
        if not self.motion:
            self.board.itemconfigure(self.caption, text=line)
            return
        def print_to(index):
            self.board.itemconfigure(self.caption,text=line[:index])
            self.typing = self.window.after(15,print_to,index+1) if index<len(line) else None
        print_to(1)

    def destroy(self):
        for token in (self.animation,self.typing):
            if token is not None:
                self.window.after_cancel(token)
        self.window.destroy()

