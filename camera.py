"""Read and analyze the latest camera frame on a single background thread."""

from contextlib import ExitStack
from dataclasses import dataclass
from math import dist
from pathlib import Path
from queue import SimpleQueue
from threading import Event, Thread
from time import perf_counter, time

import cv2
import mediapipe as mp
import numpy as np
from cv2_enumerate_cameras import enumerate_cameras
from mediapipe.tasks.python import BaseOptions, vision

from behavior import FINGERS, Action, BehaviorDetector, Hit, Point, face_scale, midpoint
from face_achievements import FaceAchievements
from habit_settings import SECRET_AWARDS, HabitData
from presence import ITEM_FRESHNESS, PresenceMonitor
from vision_items import held_items
from visitor_log import VisitorLog
from local_rules import BITE, EYE_GLASSES, EYE_RUB, Reminder, Rule, RuleMonitor


MODEL_DIR = Path(__file__).resolve().parent / "models"
SAMPLE_RATE = 24
DEFAULT_HOLD = 0.36
FACE_BRIDGE_MS = 430


@dataclass(frozen=True)
class Camera:
    name: str
    index: int
    backend: int
    vendor: int | None


def list_cameras() -> list[Camera]:
    cameras = [
        Camera(device.name, device.index, device.backend, device.vid)
        for device in enumerate_cameras(cv2.CAP_DSHOW)
    ]
    if not cameras:
        cameras = [
            Camera(device.name, device.index, device.backend, device.vid)
            for device in enumerate_cameras(cv2.CAP_MSMF)
        ]

    def priority(camera: Camera) -> tuple[int, str]:
        name = camera.name.casefold()
        if "c270" in name:
            return 0, name
        if camera.vendor == 0x046D or "logi" in name:
            return 1, name
        virtual = any(term in name for term in ("virtual", "ivcam", "obs", "虚拟"))
        return (3 if virtual else 2), name

    return sorted(cameras, key=priority)


@dataclass(frozen=True)
class FrameSample:
    number: int
    rgb: np.ndarray
    hit: Hit | None
    has_face: bool
    bite_events: int
    capture_time: float
    inference_ms: float
    has_hand: bool = False
    face_reused: bool = False
    pending: Hit | None = None
    at_desk: bool = False


Hands = list[tuple[str, list[Point]]]


def hand_points(result, width: int, height: int, x: int = 0, y: int = 0) -> Hands:
    hands: Hands = []
    for index, (labels, landmarks) in enumerate(zip(result.handedness, result.hand_landmarks)):
        side = labels[0].category_name if labels else str(index)
        if any(other_side == side for other_side, _ in hands):
            side = f"{side}-{index}"
        hands.append((side, [(point.x * width + x, point.y * height + y) for point in landmarks]))
    return hands


class LandmarkModels:
    def __init__(self, stack: ExitStack, model_dir: Path):
        mode = vision.RunningMode.VIDEO
        hand_model = (model_dir / "hand_landmarker.task").read_bytes()
        face_options = vision.FaceLandmarkerOptions(
            base_options=BaseOptions(
                model_asset_buffer=(model_dir / "face_landmarker.task").read_bytes()
            ),
            running_mode=mode,
            num_faces=1,
            min_face_detection_confidence=0.44,
            min_face_presence_confidence=0.43,
        )
        hand_options = vision.HandLandmarkerOptions(
            base_options=BaseOptions(model_asset_buffer=hand_model),
            running_mode=mode,
            num_hands=2,
            min_hand_detection_confidence=0.41,
            min_hand_presence_confidence=0.42,
        )
        retry_options = vision.HandLandmarkerOptions(
            base_options=BaseOptions(model_asset_buffer=hand_model),
            running_mode=vision.RunningMode.IMAGE,
            num_hands=2,
            min_hand_detection_confidence=0.33,
            min_hand_presence_confidence=0.36,
        )
        self.face = stack.enter_context(vision.FaceLandmarker.create_from_options(face_options))
        self.hand = stack.enter_context(vision.HandLandmarker.create_from_options(hand_options))
        self.hand_retry = stack.enter_context(vision.HandLandmarker.create_from_options(retry_options))
        self.last_face: list[Point] = []
        self.face_seen_at = -FACE_BRIDGE_MS - 1
        self.retry_at = 0
        self.face_reused = False

    def landmarks(self, rgb: np.ndarray, timestamp_ms: int, *, keep_hands=False, detect_hands=True) -> tuple[list[Point], Hands]:
        height, width = rgb.shape[:2]
        image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        faces = self.face.detect_for_video(image, timestamp_ms).face_landmarks
        if faces:
            self.last_face = [(point.x * width, point.y * height) for point in faces[0]]
            self.face_seen_at = timestamp_ms
        self.face_reused = not faces and timestamp_ms - self.face_seen_at <= FACE_BRIDGE_MS
        if not detect_hands:
            return self.last_face if faces or self.face_reused else [], []
        if not faces and not self.face_reused:
            if keep_hands:
                result = self.hand.detect_for_video(image, timestamp_ms)
                return [], hand_points(result, width, height)
            return [], []

        face = self.last_face
        scale = face_scale(face)
        result = self.hand.detect_for_video(image, timestamp_ms)
        hands = hand_points(result, width, height)
        if scale >= 24 and timestamp_ms >= self.retry_at:
            if not self._near_face_hands(hands, face[2], scale):
                recovered = self._look_near_face(rgb, face[2], scale)
                self.retry_at = timestamp_ms + (0 if recovered else 140)
                if recovered:
                    duplicates = {side for side, _ in recovered}
                    hands = (recovered + [(side, points) for side, points in hands if side not in duplicates])[:2]
        return face, hands

    @staticmethod
    def _near_face_hands(hands: Hands, nose: Point, scale: float) -> Hands:
        return [
            (side, points) for side, points in hands
            if any(dist(points[tip], nose) < 0.82 * scale for tip, _, _ in FINGERS)
        ]

    def _look_near_face(self, rgb: np.ndarray, nose: Point, scale: float) -> Hands:
        """Search a larger view of a small or partially obscured hand around the face."""
        height, width = rgb.shape[:2]
        x, y = nose
        left, right = max(0, int(x - 1.50 * scale)), min(width, int(x + 1.50 * scale))
        top, bottom = max(0, int(y - 1.06 * scale)), min(height, int(y + 1.75 * scale))
        if right - left < 48 or bottom - top < 48:
            return []
        crop = np.ascontiguousarray(rgb[top:bottom, left:right])
        result = self.hand_retry.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=crop))
        hands = hand_points(result, right - left, bottom - top, left, top)
        return self._near_face_hands(hands, nose, scale)


class CameraSession:
    def __init__(self, camera: Camera, bite_hold: float = DEFAULT_HOLD, rules: tuple[Rule, ...] = (),
                 *, water_enabled=True, eye_enabled=False, visitors: VisitorLog | None = None,
                 preferences: HabitData | None = None):
        self.camera = camera
        self.bite_hold = bite_hold
        self.rules = rules
        self.set_preferences(preferences or HabitData(water_detection=water_enabled, eye_enabled=eye_enabled))
        self.drinks: SimpleQueue[float] = SimpleQueue()
        self.reminders: SimpleQueue[tuple[float, Reminder]] = SimpleQueue()
        self.expressions: SimpleQueue[tuple[float, str]] = SimpleQueue()
        self.phase = "正在加载本地视觉模型"
        self.latest: FrameSample | None = None
        self.error: str | None = None
        self.stop_requested = Event()
        self.presence = PresenceMonitor(MODEL_DIR, self.stop_requested, self.drinks, visitors)
        self.worker = Thread(target=self._run, name="camera-inference", daemon=True)

    def set_preferences(self, preferences: HabitData) -> None:
        watch_expressions = preferences.expression_enabled and not preferences.unlocked_secrets.issuperset(SECRET_AWARDS)
        self.monitoring = (preferences, watch_expressions)

    @property
    def eye_enabled(self) -> bool:
        return self.monitoring[0].eye_enabled

    @property
    def water_enabled(self) -> bool:
        return self.monitoring[0].records_drinks

    def start(self) -> None:
        self.worker.start()

    def stop(self) -> None:
        self.stop_requested.set()

    def _run(self) -> None:
        capture = None
        try:
            with ExitStack() as stack:
                models = LandmarkModels(stack, MODEL_DIR)
                if self.stop_requested.is_set():
                    return
                self.phase = "正在连接 " + self.camera.name
                capture = cv2.VideoCapture(self.camera.index, self.camera.backend)
                if not capture.isOpened():
                    self.error = "无法打开摄像头。请检查 Windows 摄像头权限及其他正在使用它的应用。"
                    return
                capture.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
                capture.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
                self.phase = "正在获取摄像头画面"
                self.presence.start()
                self._read_frames(capture, models)
        except FileNotFoundError:
            self.error = "缺少本地模型文件。请检查应用目录中的 models 文件夹。"
        except (OSError, RuntimeError, ValueError, cv2.error) as error:
            self.error = f"摄像头或模型启动出错。{error}"
        finally:
            if capture is not None:
                capture.release()
            self.stop_requested.set()
            if self.presence.worker.ident is not None:
                self.presence.worker.join(timeout=1.5)

    def _read_frames(self, capture: cv2.VideoCapture, models: LandmarkModels) -> None:
        detector = eye_guard = expressions = None
        custom = RuleMonitor()
        frames = 0
        bite_events = 0
        failed_reads = 0
        while not self.stop_requested.is_set():
            loop_started = perf_counter()
            ok, bgr = capture.read()
            captured = perf_counter()
            captured_at = time()
            if self.stop_requested.is_set():
                return
            if not ok or bgr is None:
                failed_reads += 1
                if failed_reads >= 10:
                    self.error = "摄像头没有返回画面。请重新连接摄像头，再点击开启。"
                    return
                self.stop_requested.wait(0.10)
                continue
            failed_reads = 0
            height, width = bgr.shape[:2]
            if width > 640:
                bgr = cv2.resize(bgr, (640, round(height * 640 / width)), interpolation=cv2.INTER_AREA)
            rgb = cv2.cvtColor(cv2.flip(bgr, 1), cv2.COLOR_BGR2RGB)
            settings, watch_expressions = self.monitoring
            rules = self.rules if settings.rules_enabled else ()
            need_hands = settings.bite_enabled or settings.records_drinks or settings.eye_enabled or any(rule.enabled for rule in rules)
            previous = self.presence.latest
            previous_items = previous.items if previous and captured - previous.timestamp <= ITEM_FRESHNESS else ()
            captured_ms = int(captured * 1000)
            occupied = previous.occupied_hands if previous and captured - previous.timestamp <= ITEM_FRESHNESS else ()
            bridge = settings.records_drinks and bool(previous_items or occupied) and captured_ms - models.face_seen_at <= 1450
            if need_hands or settings.break_enabled or watch_expressions:
                face, hands = models.landmarks(rgb, captured_ms, keep_hands=bridge, detect_hands=need_hands)
            else:
                face, hands = [], []
            drinking_face = face or (models.last_face if bridge else [])
            self.presence.submit(rgb, captured, drinking_face, hands, settings.records_drinks, settings.guest_enabled)
            # A visible object in the same hand trumps a finger-only nose or lip gesture.
            held = held_items(drinking_face, hands, previous_items)
            blocking = {item.side for item in held if item.gap <= 1.10}
            blocking.update(occupied)
            free_hands = [(side, points) for side, points in hands if side not in blocking]
            fresh_face = not models.face_reused
            hit = pending = None
            has_face = face_scale(face) >= 24
            if settings.bite_enabled:
                if detector is None:
                    detector = BehaviorDetector((Action.BITE,))
                bite_count = detector.bite_events
                hit, _ = detector.update(face, free_hands, captured, self.bite_hold, fresh_face=fresh_face)
                pending = detector.pending
                if detector.bite_events > bite_count:
                    bite_events += 1
                    self.reminders.put((captured_at, BITE))
            else:
                detector = None
            if settings.eye_enabled:
                if eye_guard is None:
                    eye_guard = BehaviorDetector((Action.RUB_EYE, Action.GLASSES))
                eye_hit, _ = eye_guard.update(face if fresh_face else [], free_hands, captured, self.bite_hold)
                hit = hit or eye_hit
                if eye_guard.eye_event:
                    event = EYE_RUB if eye_guard.eye_event == Action.RUB_EYE else EYE_GLASSES
                    self.reminders.put((captured_at, event))
            else:
                eye_guard = None
            for notice in custom.update(rules, face, free_hands, captured):
                self.reminders.put((captured_at, notice))
            if watch_expressions:
                if expressions is None:
                    expressions = FaceAchievements()
                for award in expressions.update(face if fresh_face else [], captured):
                    self.expressions.put((captured_at, award))
            else:
                expressions = None
            at_desk = False
            if settings.break_enabled and fresh_face and has_face:
                x, y = midpoint(face[33], face[263])
                h, w = rgb.shape[:2]
                at_desk = (w * .1 < x < w * .9 and h * .08 < y < h * .83
                           and abs(face[1][0] - x) < face_scale(face) * .25)
            self.latest = FrameSample(
                frames, rgb, hit, has_face, bite_events, captured, (perf_counter() - captured) * 1000,
                has_hand=bool(hands), face_reused=models.face_reused, pending=pending, at_desk=at_desk,
            )
            frames += 1
            self.stop_requested.wait(max(0.0, 1 / SAMPLE_RATE - (perf_counter() - loop_started)))
