"""Start the local Windows app and reuse its tray instance when reopened."""

import argparse
import ctypes
from ctypes import wintypes
import os
from queue import Empty, SimpleQueue
from threading import Thread
import tkinter as tk
from tkinter import messagebox

from runtime_paths import FROZEN, USER_DATA


def main() -> None:
    parser = argparse.ArgumentParser(description="牛马哨兵")
    parser.add_argument("--paused", action="store_true", help="启动时关闭摄像头")
    args = parser.parse_args()
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.CreateEventW.restype = wintypes.HANDLE
    mutex = kernel32.CreateMutexW(None, False, r"Local\TingYiXiaCameraApp")
    status = ctypes.get_last_error()
    if not mutex:
        raise ctypes.WinError(status)
    request = None
    try:
        request = kernel32.CreateEventW(None, False, False, r"Local\TingYiXiaReveal")
        if not request:
            raise ctypes.WinError(ctypes.get_last_error())
        event = wintypes.HANDLE(request)
        if status == 183:
            kernel32.SetEvent(event)
            return

        ctypes.windll.user32.SetProcessDPIAware()
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Play.Atelier.NiuMaSentinel")
        from native_views import Splash
        root = tk.Tk()
        root.withdraw()
        try:
            splash = Splash(root)
        except (OSError, RuntimeError, tk.TclError) as error:
            messagebox.showerror("牛马哨兵", f"启动页加载失败。\n{error}", parent=root)
            root.destroy()
            return
        prepared = SimpleQueue()

        def prepare() -> None:
            try:
                if FROZEN:
                    os.environ.setdefault("MPLCONFIGDIR", str(USER_DATA / "cache" / "matplotlib"))
                    os.environ["MPLBACKEND"] = "Agg"
                import cv2
                cv2.setNumThreads(1)
                from main_window import App
                prepared.put((App, None))
            except Exception as error:
                prepared.put((None, error))

        def launch() -> None:
            try:
                app, error = prepared.get_nowait()
            except Empty:
                root.after(30, launch)
                return
            if error is None:
                try:
                    app(root, start_paused=args.paused,
                        activation_requested=lambda: kernel32.WaitForSingleObject(event, 0) == 0, splash=splash)
                    return
                except (OSError, RuntimeError, ValueError, tk.TclError) as launch_error:
                    error = launch_error
            messagebox.showerror("牛马哨兵", f"应用启动失败。\n{error}", parent=splash.toplevel)
            root.destroy()

        Thread(target=prepare, name="prepare-desktop", daemon=True).start()
        root.after(30, launch)
        root.mainloop()
    finally:
        if request:
            kernel32.CloseHandle(wintypes.HANDLE(request))
        kernel32.CloseHandle(wintypes.HANDLE(mutex))


if __name__ == "__main__":
    main()

