"""Interpret hand and face landmarks in the same mirrored image coordinates."""

from collections import deque
from dataclasses import dataclass, replace
from enum import StrEnum
from math import copysign, dist


Point = tuple[float, float]
FINGERS = ((4, 3, 2), (8, 7, 5), (12, 11, 9), (16, 15, 13), (20, 19, 17))


class Action(StrEnum):
    BITE = "bite"
    NOSE = "nose"
    GLASSES = "glasses"
    RUB_EYE = "rub_eye"


@dataclass(frozen=True)
class ActionText:
    name: str
    description: str
    color: str


ACTION_TEXT = {
    Action.BITE: ActionText("疑似啃手", "指尖或末端指节在唇边停留。", "#E6F6AE"),
    Action.NOSE: ActionText("疑似抠鼻", "手指在鼻孔附近停留。", "#FFC8A7"),
    Action.GLASSES: ActionText("疑似扶眼镜", "手指在镜框常见位置做出推扶动作。", "#A8E5EF"),
    Action.RUB_EYE: ActionText("疑似揉眼", "手指在眼周反复移动。", "#A8E5EF"),
}


@dataclass(frozen=True)
class Hit:
    action: Action
    tip: Point
    target: Point
    scale: float
    score: float
    contact_confirmed: bool = True
    clear: bool = False
    hand: str = ""


def midpoint(a: Point, b: Point) -> Point:
    return ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)


def proximity(offset: Point, radius_x: float, radius_y: float) -> float:
    """Squared distance in an ellipse. A score below one is inside it."""
    return (offset[0] / radius_x) ** 2 + (offset[1] / radius_y) ** 2


def face_scale(face: list[Point]) -> float:
    if not face:
        return 0.0
    return max(dist(face[33], face[263]), 0.56 * dist(face[10], face[152]))


def eye_apertures(face) -> tuple[float, float]:
    return (dist(face[159], face[145]) / max(1, dist(face[33], face[133])),
            dist(face[386], face[374]) / max(1, dist(face[263], face[362])))


@dataclass(frozen=True)
class FaceRegions:
    scale: float
    horizontal: Point
    lips: Point
    lip_width: float
    lip_opening: float
    profile: float
    nose: Point
    nose_width: float
    eyewear: tuple[Point, Point, Point]
    eyes: tuple[Point, Point]
    eye_openings: tuple[float, float]

    @classmethod
    def from_landmarks(cls, face: list[Point], scale: float) -> "FaceRegions":
        left, right = sorted((face[33], face[263]))
        eye_span = dist(left, right)
        ux, uy = ((right[0] - left[0]) / eye_span, (right[1] - left[1]) / eye_span) if eye_span else (1.0, 0.0)
        extension = 0.075 * scale
        eye_x, eye_y = midpoint(left, right)
        nose_x, nose_y = face[1]
        turn = ((nose_x - eye_x) * ux + (nose_y - eye_y) * uy) / scale
        profile = copysign(min(1.0, max(0.0, (abs(turn) - 0.16) / 0.28)), turn)
        return cls(
            scale,
            (ux, uy),
            midpoint(face[13], face[14]),
            dist(face[61], face[291]) / scale,
            abs((face[14][1] - face[13][1]) * ux - (face[14][0] - face[13][0]) * uy) / scale,
            profile,
            midpoint(midpoint(face[98], face[327]), face[2]),
            dist(face[98], face[327]) / scale,
            (
                (left[0] - ux * extension, left[1] - uy * extension),
                (right[0] + ux * extension, right[1] + uy * extension),
                midpoint(face[168], face[6]),
            ),
            (midpoint(face[33], face[133]), midpoint(face[263], face[362])),
            eye_apertures(face),
        )

    def offset(self, point: Point, center: Point) -> Point:
        x, y = (point[0] - center[0]) / self.scale, (point[1] - center[1]) / self.scale
        ux, uy = self.horizontal
        return x * ux + y * uy, -x * uy + y * ux

    def lip_score(self, point: Point) -> float:
        x, y = self.offset(point, self.lips)
        width = min(0.43, max(0.24, self.lip_width * 0.60 + 0.06))
        # Keep the lower edge tight to avoid reacting to a hand under the chin.
        height = (0.10 if y >= 0 else 0.12) + min(0.05, self.lip_opening * 0.4)
        if x * self.profile > 0:
            # A side view often hides the fingertip at the forward edge of the lips.
            profile = abs(self.profile)
            x = copysign(max(0.0, abs(x) - 0.16 * profile), x)
            width += 0.18 * profile
            height += 0.045 * profile
        return proximity((x, y), width, height)

    def nose_score(self, point: Point) -> float:
        width = min(0.23, max(0.142, self.nose_width * 0.66))
        return proximity(self.offset(point, self.nose), width, 0.105)


@dataclass(frozen=True)
class ActionTiming:
    hold: float
    clear_hold: float | None = None
    linger: float = 0.0


NOSE_TIMING = ActionTiming(hold=0.24, clear_hold=0.17)
GLASSES_TIMING = ActionTiming(hold=0.075, linger=0.48)
EYE_RUB_TIMING = ActionTiming(hold=0.07, linger=0.48)


class ActionTrack:
    """Confirm observations of an action over time and preserve a brief drop in tracking."""

    def __init__(self):
        self.observed = 0.0
        self.last_hit: float | None = None
        self.last_presence: float | None = None
        self.active = False
        self.clear_observed = 0.0
        self.clear_frames = 0
        self.previous_clear: Hit | None = None
        self.visible_hit: Hit | None = None

    def update(
        self, hit: Hit | None, now: float, timing: ActionTiming, fresh_face: bool,
    ) -> tuple[Hit | None, bool]:
        gap = float("inf") if self.last_hit is None else now - self.last_hit
        if self.last_presence is None or now - self.last_presence >= 0.52:
            self.active = False
        if hit is not None:
            # Ambiguous observations may keep an episode open after a confirmed contact.
            self.last_presence = now
        if gap >= 0.32:
            self.observed = 0.0
        if hit is None or not hit.contact_confirmed:
            self._clear_run()
            if self.visible_hit is not None and gap < timing.linger:
                return self.visible_hit, False
            self.visible_hit = None
            return None, False

        if gap < 0.32:
            # Missed frames preserve earlier evidence without counting the entire gap as contact.
            self.observed += min(gap, 0.095)
        self.last_hit = now
        if hit.clear and fresh_face:
            self._record_clear(hit, gap)
        else:
            self._clear_run()
        if self.active:
            self.visible_hit = hit
            return hit, False
        clear_hold = timing.clear_hold
        clear_enough = (
            clear_hold is not None and self.clear_frames >= 3 and self.clear_observed >= clear_hold
        )
        if clear_enough or self.observed >= timing.hold:
            self.active = True
            self.visible_hit = hit
            return hit, True
        return None, False

    def _clear_run(self) -> None:
        self.previous_clear = None
        self.clear_frames = 0
        self.clear_observed = 0.0

    def _record_clear(self, hit: Hit, gap: float) -> None:
        previous = self.previous_clear
        if previous is None or previous.hand != hit.hand or gap >= 0.13:
            self._clear_run()
        else:
            self.clear_observed += min(gap, 0.095)
        self.previous_clear = hit
        self.clear_frames += 1


class BehaviorDetector:
    def __init__(self, actions: tuple[Action, ...] = tuple(Action)):
        self.tracks = {action: ActionTrack() for action in actions}
        self.eye_history: dict[tuple[str, int, int], deque[tuple[float, Point]]] = {}
        self.rub_history: dict[tuple[str, int, int], deque[tuple[float, Point]]] = {}
        self.last_eye_at = -100.0
        self.eye_event: Action | None = None
        self.bite_events = 0
        self.pending: Hit | None = None
        self.bite_timing = ActionTiming(hold=0)

    def update(
        self,
        face: list[Point],
        hands: list[tuple[str, list[Point]]],
        now: float,
        bite_hold: float,
        *,
        fresh_face: bool = True,
    ) -> tuple[Hit | None, bool]:
        self.eye_event = None
        scale = face_scale(face)
        in_view = scale >= 24
        if in_view:
            candidates = self._candidates(FaceRegions.from_landmarks(face, scale), hands, now)
        else:
            candidates = {}
            self.eye_history.clear()
            self.rub_history.clear()

        if self.bite_timing.hold != bite_hold:
            clear_hold = max(0.145, bite_hold * 0.50) if bite_hold < 0.50 else None
            self.bite_timing = ActionTiming(bite_hold, clear_hold)
        timings = {
            Action.BITE: self.bite_timing, Action.NOSE: NOSE_TIMING, Action.GLASSES: GLASSES_TIMING,
            Action.RUB_EYE: EYE_RUB_TIMING,
        }
        self.pending = next((candidates[action] for action in self.tracks if action in candidates), None)
        current = None
        for action, track in self.tracks.items():
            hit, new = track.update(candidates.get(action), now, timings[action], fresh_face)
            current = current or hit
            if action == Action.BITE and new:
                self.bite_events += 1
            if action in (Action.RUB_EYE, Action.GLASSES) and new and fresh_face and now - self.last_eye_at >= 7.0:
                self.eye_event = action
                self.last_eye_at = now
        return current if in_view else None, in_view

    def _candidates(
        self,
        regions: FaceRegions,
        hands: list[tuple[str, list[Point]]],
        now: float,
    ) -> dict[Action, Hit]:
        for history, lifetime in ((self.eye_history, .42), (self.rub_history, .63)):
            for key, trail in list(history.items()):
                while trail and now - trail[0][0] > lifetime:
                    trail.popleft()
                if not trail:
                    del history[key]
        best: dict[Action, Hit] = {}
        scale = regions.scale
        for hand_id, hand in hands:
            hand_hits: dict[Action, Hit] = {}
            pinched = 0.035 * scale <= dist(hand[4], hand[8]) <= 0.28 * scale
            for tip_index, distal_index, base_index in FINGERS:
                tip, base = hand[tip_index], hand[base_index]
                if Action.RUB_EYE in self.tracks and tip_index in (8, 12, 16):
                    eye_rub = self._rub_eye_hit(regions, hand_id, tip_index, tip, base, now)
                    if eye_rub is not None:
                        self._keep_best(hand_hits, eye_rub)
                if Action.GLASSES in self.tracks and tip_index in (4, 8, 12):
                    glasses = self._glasses_hit(
                        regions, hand_id, hand, tip_index, base, pinched and tip_index in (4, 8), now
                    )
                    if glasses is not None:
                        # A moving pinch near nearly closed eyes is often a rub at the inner corners.
                        if (Action.RUB_EYE in self.tracks and max(regions.eye_openings) <= .225
                                and min(dist(tip, eye) for eye in regions.eyes) <= .33 * scale):
                            glasses = replace(glasses, action=Action.RUB_EYE, hand=hand_id)
                        self._keep_best(hand_hits, glasses)
                if Action.BITE not in self.tracks and Action.NOSE not in self.tracks:
                    continue
                joint = hand[distal_index]
                distal = midpoint(tip, joint)
                x, y = regions.offset(tip, regions.lips)
                joint_x, joint_y = regions.offset(joint, regions.lips)
                _, base_y = regions.offset(base, regions.lips)
                bent = dist(base, tip) + 0.014 * scale < dist(base, joint) + dist(joint, tip)
                straight_across_lips = (
                    not bent and y < -0.15 and base_y > 0.28 and abs(x) < 0.19
                    and abs(joint_x) < 0.18 and abs(joint_y) < 0.115
                )
                for point, penalty in ((tip, 0.0), (distal, 0.10)):
                    lip_score = regions.lip_score(point) + penalty
                    nose_score = regions.nose_score(point) + penalty
                    if lip_score < 1 and lip_score <= nose_score:
                        if dist(point, regions.lips) <= dist(base, regions.lips) + 0.02 * scale:
                            central_tip = (
                                penalty == 0 and tip_index in (4, 8, 12)
                                and abs(x) < 0.22 and abs(y) < 0.072 and lip_score < 0.54
                                and bent
                            )
                            contact = not straight_across_lips and (regions.lip_opening >= 0.024 or central_tip)
                            clear = (
                                penalty == 0 and abs(x) < 0.20 and abs(y) < 0.067
                                and regions.lip_opening >= 0.038
                                and dist(tip, regions.lips) + 0.024 * scale < dist(joint, regions.lips)
                                and dist(base, regions.lips) > 0.25 * scale
                            )
                            self._keep_best(
                                hand_hits, Hit(Action.BITE, point, regions.lips, scale, lip_score, contact, clear, hand_id)
                            )
                    if tip_index != 4 and nose_score < 1 and nose_score < lip_score:
                        if dist(point, regions.nose) < dist(base, regions.nose):
                            contact = not straight_across_lips
                            clear = contact and penalty == 0 and nose_score < 0.46 and tip_index in (8, 12)
                            self._keep_best(
                                hand_hits, Hit(Action.NOSE, point, regions.nose, scale, nose_score, contact, clear, hand_id)
                            )
            mouth, nose = hand_hits.get(Action.BITE), hand_hits.get(Action.NOSE)
            if mouth and nose and nose.contact_confirmed and nose.score < 0.78 and nose.score <= mouth.score + 0.10:
                # One hand can have a curled finger near the lips while its index finger is at a nostril.
                del hand_hits[Action.BITE]
            for hit in hand_hits.values():
                self._keep_best(best, hit)
        return best

    def _rub_eye_hit(self, regions: FaceRegions, hand: str, finger: int, tip: Point, base: Point,
                     now: float) -> Hit | None:
        target = min(regions.eyes, key=lambda eye: dist(tip, eye))
        offset = regions.offset(tip, target)
        score = proximity(offset, .17, .125)
        key = hand, finger, regions.eyes.index(target)
        if score >= 1.0 or dist(tip, target) + .025 * regions.scale >= dist(base, target):
            self.rub_history.pop(key, None)
            return None
        trail = self.rub_history.setdefault(key, deque())
        if trail and now - trail[-1][0] > .16:
            trail.clear()
        trail.append((now, offset))
        if len(trail) < 4 or now - trail[0][0] < .16:
            return None
        for axis in (0, 1):
            values = [point[axis] for _, point in trail]
            extent = max(values) - min(values)
            travel = sum(abs(to - start) for start, to in zip(values, values[1:]))
            if extent >= .052 and travel >= .115 and travel - abs(values[0] - values[-1]) >= .077:
                return Hit(Action.RUB_EYE, tip, target, regions.scale, score, hand=hand)
        return None

    def _glasses_hit(
        self, regions: FaceRegions, hand_id: str, hand: list[Point], tip_index: int, base: Point,
        pinched: bool, now: float,
    ) -> Hit | None:
        match = None
        tip = hand[tip_index]
        for region, target in enumerate(regions.eyewear):
            offset = regions.offset(tip, target)
            score = proximity(offset, 0.22, 0.22)
            if score >= 1.50 or dist(tip, target) >= dist(base, target):
                continue
            trail = self.eye_history.setdefault((hand_id, tip_index, region), deque())
            if pinched:
                grip_partner = hand[8 if tip_index == 4 else 4]
                grip = proximity(regions.offset(grip_partner, target), 0.22, 0.22) < 1.25
            else:
                grip = False
            other_fingers_near = sum(
                proximity(regions.offset(hand[other], target), 0.22, 0.22) < 1.10
                for other, _, _ in FINGERS if other != tip_index
            )
            lifted = (grip or (other_fingers_near == 0 and score < 0.78)) and any(
                now - moment >= 0.045 and before[1] - offset[1] >= 0.038 for moment, before in trail
            )
            adjusted = grip and any(
                now - moment >= 0.055
                and (abs(before[0] - offset[0]) >= 0.047 or before[1] - offset[1] >= 0.025)
                for moment, before in trail
            )
            trail.append((now, offset))
            if score < 0.96 and (lifted or adjusted) and (match is None or score < match.score):
                match = Hit(Action.GLASSES, tip, target, regions.scale, score)
        return match

    @staticmethod
    def _keep_best(hits: dict[Action, Hit], hit: Hit) -> None:
        previous = hits.get(hit.action)
        quality = (not hit.contact_confirmed, not hit.clear, hit.score)
        if previous is None or quality < (not previous.contact_confirmed, not previous.clear, previous.score):
            hits[hit.action] = hit
