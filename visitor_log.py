"""Optional, local visitor photos gated by proximity, owner matching, and new input."""

import ctypes
from ctypes import wintypes
from dataclasses import dataclass, replace
from datetime import datetime
import json
from pathlib import Path
import re
from threading import RLock
from time import perf_counter
from uuid import uuid4

import cv2
import numpy as np

from runtime_paths import USER_DATA

VISITOR_HOME = USER_DATA / "visitors"
MODEL_NAME = "sface_int8.onnx"
PHOTO_NAME = re.compile(r"\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}_[0-9a-f]{8}\.jpg")
NEAR_SECONDS = 1.5
ENROLL_TIMEOUT = 9.0


@dataclass(frozen=True)
class VisitorConfig:
    enabled: bool = False
    owner: tuple[float, ...] = ()
    generation: int = 0
    enroll_at: float | None = None


class VisitorLog:
    def __init__(self, directory=VISITOR_HOME):
        self.directory = directory
        self.photos = directory / "photos"
        self.path = directory / "settings.json"
        self.lock = RLock()
        self.config = VisitorConfig()
        self.suspended = False
        self.status = ""
        self.error = ""
        self.revision = 0
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if raw.get("version") != 1 or type(raw.get("enabled")) is not bool or not isinstance(raw.get("owner"), list):
                raise ValueError("访客设置格式无效。")
            owner = np.asarray(raw["owner"], dtype=np.float32)
            if owner.size and (owner.shape != (128,) or not np.all(np.isfinite(owner)) or not .95 < np.linalg.norm(owner) < 1.05):
                raise ValueError("登记数据无效。")
            self.config = VisitorConfig(raw["enabled"] and bool(owner.size), tuple(float(x) for x in owner))
        except FileNotFoundError:
            pass
        except (OSError, ValueError, TypeError, AttributeError, KeyError) as error:
            self.error = str(error)
            self.status = "访客设置待检查"

    def _persist(self, enabled, owner):
        if self.error:
            raise ValueError("请检查访客设置文件后重新打开应用。")
        self.directory.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        try:
            temporary.write_text(json.dumps({"version": 1, "enabled": enabled, "owner": list(owner)}),
                                 encoding="utf-8")
            temporary.replace(self.path)
        finally:
            temporary.unlink(missing_ok=True)
        self.config = VisitorConfig(enabled, owner, self.config.generation + 1)

    def enable(self, enabled: bool):
        with self.lock:
            if enabled and not self.config.owner:
                raise ValueError("先登记本人，再开启访客记录。")
            self._persist(enabled, self.config.owner)
            self.status = ""

    def start_enrollment(self):
        with self.lock:
            if self.suspended:
                raise ValueError("请先开启「老板来了」。")
            if self.error:
                raise ValueError("请先检查访客设置文件。")
            old = self.config
            self.config = VisitorConfig(old.enabled, old.owner, old.generation + 1, perf_counter())
            self.status = "本人保持在画面中央，正对镜头"

    def set_suspended(self, value: bool) -> None:
        with self.lock:
            if self.suspended == value:
                return
            self.suspended = value
            old = self.config
            self.config = replace(old, generation=old.generation + 1, enroll_at=None)
            self.status = ""

    def cancel_enrollment(self, status=""):
        with self.lock:
            if self.config.enroll_at is not None:
                old = self.config
                self.config = VisitorConfig(old.enabled, old.owner, old.generation + 1)
                self.status = status

    def finish_enrollment(self, feature, generation):
        with self.lock:
            if self.config.generation != generation or self.config.enroll_at is None:
                return
            self._persist(self.config.enabled, tuple(float(x) for x in feature))
            self.status = "本人已登记，可以开启访客记录"

    def forget_owner(self):
        with self.lock:
            self._persist(False, ())
            self.status = ""

    def save(self, image: np.ndarray, generation: int, stopping=None) -> bool:
        height, width = image.shape[:2]
        if max(width, height) > 300:
            scale = 300 / max(width, height)
            image = cv2.resize(image, (round(width * scale), round(height * scale)), interpolation=cv2.INTER_AREA)
        success, encoded = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 87])
        if not success:
            return False
        with self.lock:
            if (self.suspended or self.config.generation != generation or not self.config.enabled or self.config.enroll_at is not None
                    or (stopping is not None and stopping.is_set())):
                return False
            self.photos.mkdir(parents=True, exist_ok=True)
            name = datetime.now().strftime("%Y-%m-%d_%H-%M-%S") + "_" + uuid4().hex[:8] + ".jpg"
            target = self.photos / name
            temporary = target.with_suffix(".tmp")
            try:
                temporary.write_bytes(encoded.tobytes())
                temporary.replace(target)
            finally:
                temporary.unlink(missing_ok=True)
            self.revision += 1
            self.status = "已保存近距离访客"
            return True

    def recent(self, limit=100):
        if not self.photos.is_dir():
            return []
        with self.lock:
            return sorted((file for file in self.photos.iterdir() if PHOTO_NAME.fullmatch(file.name)),
                          reverse=True)[:limit]

    def delete(self, filename: str):
        if not PHOTO_NAME.fullmatch(filename):
            raise ValueError("访客文件名称无效。")
        with self.lock:
            (self.photos / filename).unlink(missing_ok=True)
            self.revision += 1


class _LastInput(ctypes.Structure):
    _fields_ = [("size", wintypes.UINT), ("stamp", wintypes.DWORD)]


def last_input_at() -> float | None:
    info = _LastInput(ctypes.sizeof(_LastInput), 0)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
        return None
    tick = ctypes.windll.kernel32.GetTickCount() & 0xffffffff
    return perf_counter() - ((tick - info.stamp) & 0xffffffff) / 1000


def _eligible(row, width, height):
    x, y, w, h = row[:4]
    cx, cy = (x + w / 2) / width, (y + h / 2) / height
    eye_span = abs(float(row[4] - row[6]))
    return (row[14] >= .83 and w >= width * .19 and h >= height * .225
            and .21 <= cx <= .79 and .13 <= cy <= .83 and eye_span >= 17
            and abs(float(row[8] - (row[4] + row[6]) / 2)) < eye_span * .57
            and x > -w * .12 and x + w < width + w * .12)


def near_face(bgr, faces):
    height, width = bgr.shape[:2]
    eligible = [] if faces is None else [row for row in faces if _eligible(row, width, height)]
    return eligible[0] if len(eligible) == 1 else None


class VisitorRecorder:
    def __init__(self, log: VisitorLog, model_path: Path, stopping=None):
        self.log = log
        self.model_path = model_path
        self.stopping = stopping
        self.model = None
        self.generation = -1
        self.next_at = 0.0
        self.feature = None
        self.seen_at = -100.0
        self.since = 0.0
        self.samples = []
        self.saved = False

    def _reset(self):
        self.feature = None
        self.samples = []
        self.saved = False

    def _embedding(self, bgr, row):
        if self.model is None:
            weights = np.frombuffer(self.model_path.read_bytes(), dtype=np.uint8)
            self.model = cv2.FaceRecognizerSF.create("onnx", weights, np.empty(0, np.uint8))
        aligned = self.model.alignCrop(bgr, row)
        gray = cv2.cvtColor(aligned, cv2.COLOR_BGR2GRAY)
        if cv2.Laplacian(gray, cv2.CV_32F).var() < 23 or gray.mean() < 24:
            return None
        feature = self.model.feature(aligned).reshape(-1)
        norm = np.linalg.norm(feature)
        return feature / norm if feature.size == 128 and np.isfinite(norm) and norm > 1e-8 else None

    def observe(self, bgr, faces, now: float):
        if self.log.suspended or (self.stopping is not None and self.stopping.is_set()):
            return
        state = self.log.config
        if state.generation != self.generation:
            self.generation = state.generation
            self.next_at = 0
            self._reset()
        if not state.enabled and state.enroll_at is None:
            self.model = None
            return
        if state.enroll_at is not None and now - state.enroll_at > ENROLL_TIMEOUT:
            self.log.cancel_enrollment("未收到清楚的正脸，请靠近一些后重试")
            return
        if now < self.next_at or (state.enroll_at is not None and now < state.enroll_at):
            return
        self.next_at = now + .37
        row = near_face(bgr, faces)
        if row is None:
            if now - self.seen_at > .90:
                self._reset()
            return
        try:
            feature = self._embedding(bgr, row)
            if feature is None:
                return
            self._accept(bgr, row, feature, now, state)
        except (OSError, ValueError, cv2.error) as error:
            self.log.cancel_enrollment("访客模型未能启动")
            self.log.status = f"访客记录待检查 · {str(error).splitlines()[0][:62]}"
            self.next_at = now + 2

    def _accept(self, bgr, row, feature, now, state, *, input_at=None):
        # Use an uncertainty margin below the model's reference matching threshold.
        if state.enroll_at is None and float(np.dot(feature, state.owner)) >= .295:
            self._reset()
            return
        if self.feature is None or now - self.seen_at > .93 or float(np.dot(feature, self.feature)) < .56:
            self._reset()
            self.since = now
        self.feature = feature
        self.seen_at = now
        if state.enroll_at is not None:
            self.samples.append(feature)
            if len(self.samples) >= 3 and now - self.since >= .66:
                combined = np.mean(self.samples, axis=0)
                self.log.finish_enrollment(combined / np.linalg.norm(combined), state.generation)
            return
        if self.saved or now - self.since < NEAR_SECONDS:
            return
        touched_at = last_input_at() if input_at is None else input_at
        if touched_at is None or not self.since + .08 <= touched_at <= now + .25 or now - touched_at > 2.8:
            return
        height, width = bgr.shape[:2]
        x, y, w, h = row[:4]
        l, t = max(0, int(x - .065 * w)), max(0, int(y - .065 * h))
        r, b = min(width, int(x + 1.065 * w)), min(height, int(y + 1.065 * h))
        if r - l > 25 and b - t > 25:
            self.saved = self.log.save(bgr[t:b,l:r], state.generation, self.stopping)

