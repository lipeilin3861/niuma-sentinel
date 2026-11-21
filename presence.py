"""Scan the shared local camera stream for people, held objects, and optional visitors."""

from dataclasses import dataclass
from pathlib import Path
from queue import Empty, Full, Queue, SimpleQueue
from threading import Event, Thread
from time import perf_counter, time

import cv2
import mediapipe as mp
import numpy as np
from mediapipe.tasks.python import BaseOptions, vision

from vision_items import HANDLED, VESSELS, Box, DrinkTracker, Item, drinking_zones, hand_near_mouth, held_items
from visitor_log import MODEL_NAME, VisitorLog, VisitorRecorder


SCAN_INTERVAL = 0.24
FAST_SCAN_INTERVAL = 0.117
RESULT_FRESHNESS = 1.25
ITEM_FRESHNESS = .60


def second_person(bodies: list[Box], faces: list[Box]) -> bool:
    """Fuse a body and its own face, including body boxes clipped at the shoulders."""
    if len(bodies) >= 2 or len(faces) >= 2:
        return True
    if not bodies or not faces:
        return False
    left, top, right, bottom = bodies[0]
    fx0, fy0, fx1, fy1 = faces[0]
    x, y = (fx0 + fx1) / 2, (fy0 + fy1) / 2
    side_margin = (fx1 - fx0) * 0.5
    top_margin = (fy1 - fy0) * 0.8
    return not (left - side_margin <= x <= right + side_margin and top - top_margin <= y <= bottom)


def _overlap(first: Box, second: Box) -> float:
    l = max(first[0], second[0])
    r = min(first[2], second[2])
    t = max(first[1], second[1])
    b = min(first[3], second[3])
    shared = max(0, r - l) * max(0, b - t)
    area_a = (first[2] - first[0]) * (first[3] - first[1])
    area_b = (second[2] - second[0]) * (second[3] - second[1])
    total = area_a + area_b - shared
    return shared / total if total else 0.0


def _combine(items):
    selected: list[Item] = []
    for item in sorted(items, key=lambda entry: entry.score, reverse=True):
        if any(_overlap(item.box, present.box) > .58 for present in selected):
            continue
        selected.append(item)
    return tuple(selected)


@dataclass(frozen=True)
class Scene:
    extra_person: bool
    bgr: np.ndarray | None
    face_rows: np.ndarray | None
    items: tuple[Item, ...]


class PeopleDetector:
    def __init__(self, model_dir: Path):
        face = np.frombuffer((model_dir / "person_face.onnx").read_bytes(), dtype=np.uint8)
        self.faces = cv2.FaceDetectorYN.create("onnx", face, np.empty(0, np.uint8), (320, 320), 0.76, 0.3, 32)
        options = vision.ObjectDetectorOptions(
            base_options=BaseOptions(model_asset_buffer=(model_dir / "efficientdet_lite0.tflite").read_bytes()),
            running_mode=vision.RunningMode.IMAGE,
            score_threshold=.21,
            category_allowlist=["person", *sorted(HANDLED)],
            max_results=20,
        )
        self.objects = vision.ObjectDetector.create_from_options(options)

    def __enter__(self) -> "PeopleDetector":
        return self

    def __exit__(self, *exc) -> None:
        self.objects.close()

    def _objects(self, rgb, x=0, y=0):
        frame = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        for detection in self.objects.detect(frame).detections:
            box = detection.bounding_box
            if not box.width or not box.height or not detection.categories:
                continue
            best = detection.categories[0]
            left, top = box.origin_x + x, box.origin_y + y
            yield Item(best.category_name, (left, top, left + box.width, top + box.height), best.score)

    def describe(self, rgb, *, find_items=False, find_people=True) -> Scene:
        height, width = rgb.shape[:2]
        bgr, located, faces = None, None, []
        if find_people:
            bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
            self.faces.setInputSize((width, height))
            _, located = self.faces.detect(bgr)
            if located is not None:
                faces = [(x, y, x + w, y + h) for x, y, w, h, *_ in located if min(w, h) >= 18]
        elif not find_items:
            return Scene(False, None, None, ())
        if len(faces) >= 2 and not find_items:
            return Scene(True, bgr, located, ())
        bodies = []
        items = []
        for item in self._objects(rgb):
            l, t, r, b = item.box
            if item.name == "person":
                if find_people and item.score >= .40 and (r - l) * (b - t) >= width * height * .007:
                    bodies.append(item.box)
            elif find_items and self._usable(item):
                items.append(item)
        return Scene(second_person(bodies, faces), bgr, located, _combine(items))

    @staticmethod
    def _usable(item):
        return item.name in HANDLED and item.score >= (.235 if item.name in VESSELS else .39)

    def local_items(self, rgb, bounds):
        l, t, r, b = bounds
        crop = np.ascontiguousarray(rgb[t:b,l:r])
        return tuple(item for item in self._objects(crop, l, t) if self._usable(item))

    def detect(self, rgb: np.ndarray) -> bool:
        return self.describe(rgb).extra_person


class PresenceDebounce:
    def __init__(self):
        self.last_match: float | None = None
        self.hold_until = 0.0

    def update(self, extra_person: bool, timestamp: float) -> bool:
        if extra_person:
            if self.last_match is not None and timestamp - self.last_match <= 0.64:
                self.hold_until = timestamp + 1.05
            self.last_match = timestamp
        else:
            self.last_match = None
        return timestamp < self.hold_until


@dataclass(frozen=True)
class PresenceResult:
    extra_person: bool
    timestamp: float
    items: tuple[Item, ...] = ()
    occupied_hands: tuple[str, ...] = ()


class SceneAnalyzer:
    def __init__(self, detector: PeopleDetector, visitor=None):
        self.detector = detector
        self.visitor = visitor
        self.debounce = PresenceDebounce()
        self.drinking = DrinkTracker()
        self.drinking_enabled = False
        self.next_crop = 0.0

    def update(self, rgb, timestamp, face, hands, water_enabled, guest_enabled=True):
        scene = self.detector.describe(rgb, find_items=bool(face and hands), find_people=guest_enabled)
        items = scene.items
        associated = held_items(face, hands, items)
        # A cropped pass helps on small or translucent containers, but only while a hand is near the face.
        if timestamp >= self.next_crop:
            for bounds in drinking_zones(face, hands, rgb.shape[1], rgb.shape[0]):
                minimum = .32 if water_enabled else .235
                if any(unit.item.name in VESSELS and unit.gap < .78 and unit.item.score >= minimum for unit in associated):
                    break
                self.next_crop = timestamp + (.09 if water_enabled else .44)
                items = _combine((*items, *self.detector.local_items(rgb, bounds)))
                associated = held_items(face, hands, items)
                if not water_enabled:
                    break

        if self.drinking_enabled != water_enabled:
            self.drinking = DrinkTracker()
            self.drinking_enabled = water_enabled
        new_drink = self.drinking.update(face, hands, associated, timestamp) and water_enabled
        if guest_enabled and self.visitor is not None:
            self.visitor.observe(scene.bgr, scene.face_rows, timestamp)
        if not guest_enabled:
            self.debounce.last_match = None
            self.debounce.hold_until = 0.0
        extra = guest_enabled and self.debounce.update(scene.extra_person, timestamp)
        return PresenceResult(extra, timestamp, items, self.drinking.blocked), new_drink


class PresenceMonitor:
    def __init__(self, model_dir: Path, stop_requested: Event, drinks: SimpleQueue | None = None,
                 visitors: VisitorLog | None = None):
        self.model_dir = model_dir
        self.stopping = stop_requested
        self.drinks = drinks
        self.visitors = visitors
        self.frames: Queue = Queue(maxsize=1)
        self.scanned_at = -100.0
        self.latest: PresenceResult | None = None
        self.error: str | None = None
        self.worker = Thread(target=self._run, name="background-vision", daemon=True)

    def start(self) -> None:
        self.worker.start()

    def submit(self, rgb, timestamp, face=(), hands=(), water_enabled=False, guest_enabled=True) -> None:
        if self.error or self.stopping.is_set():
            return
        interval = FAST_SCAN_INTERVAL if water_enabled and hand_near_mouth(face, hands) else SCAN_INTERVAL
        if timestamp - self.scanned_at < interval:
            return
        self.scanned_at = timestamp
        sample = (rgb, timestamp, face, hands, water_enabled, guest_enabled)
        try:
            self.frames.put_nowait(sample)
        except Full:
            try:
                self.frames.get_nowait()
            except Empty:
                pass
            self.frames.put_nowait(sample)

    def _run(self) -> None:
        try:
            with PeopleDetector(self.model_dir) as detector:
                visitor = VisitorRecorder(self.visitors, self.model_dir / MODEL_NAME, self.stopping) if self.visitors is not None else None
                scene = SceneAnalyzer(detector, visitor)
                while not self.stopping.is_set():
                    try:
                        sample = self.frames.get(timeout=0.12)
                    except Empty:
                        continue
                    if self.stopping.is_set():
                        break
                    result, new_drink = scene.update(*sample)
                    self.latest = result
                    if new_drink and self.drinks is not None and not self.stopping.is_set():
                        self.drinks.put(time() - max(0.0, perf_counter() - result.timestamp))
        except (OSError, RuntimeError, ValueError, cv2.error) as error:
            self.error = str(error)

