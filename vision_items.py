"""Match COCO objects to a hand and confirm a drink from a short visual sequence."""

from dataclasses import dataclass, replace
from math import hypot

from behavior import Point, face_scale, midpoint


Box = tuple[float, float, float, float]
PALM = (0, 5, 9, 13, 17)
VESSELS = frozenset(("cup", "bottle", "wine glass"))
HANDLED = VESSELS | frozenset((
    "cell phone", "remote", "toothbrush", "fork", "spoon", "banana", "apple",
    "orange", "sandwich", "hot dog", "donut", "cake",
))


@dataclass(frozen=True)
class Item:
    name: str
    box: Box
    score: float


@dataclass(frozen=True)
class Held:
    item: Item
    side: str
    grip: Point
    gap: float
    at_mouth: bool
    hand_gap: float


def _gap(point: Point, box: Box) -> float:
    x, y = point
    l, t, r, b = box
    return hypot(max(l - x, 0, x - r), max(t - y, 0, y - b))


def _palm(points) -> Point:
    return tuple(sum(points[index][axis] for index in PALM) / len(PALM) for axis in range(2))


def hand_near_mouth(face, hands):
    if not face or not hands:
        return False
    scale = face_scale(face)
    x, y = midpoint(face[13], face[14])
    return scale >= 24 and any(hypot(px - x, py - y) < 2.25 * scale
                               for px, py in (_palm(points) for _, points in hands))


def _contact(item: Item, side: str, grip: Point, lips: Point, scale: float) -> Held:
    l, t, r, b = item.box
    distance = _gap(lips, item.box) / scale
    near_rim = (l - .26 * scale <= lips[0] <= r + .26 * scale
                and t - .25 * scale <= lips[1] <= b + .09 * scale)
    below_nose = (t + b) / 2 >= lips[1] - .16 * scale
    tilted = item.name in VESSELS and (r - l) >= .83 * (b - t) and b >= lips[1] - .035 * scale
    hand_gap = hypot(grip[0] - lips[0], grip[1] - lips[1]) / scale
    return Held(item, side, grip, distance, near_rim and (below_nose or tilted), hand_gap)


def held_items(face, hands, items) -> tuple[Held, ...]:
    if not face or not hands or not items:
        return ()
    scale = face_scale(face)
    if scale < 24:
        return ()
    lips = midpoint(face[13], face[14])
    found = []
    for item in items:
        l, t, r, b = item.box
        if min(r - l, b - t) < .16 * scale or _gap(lips, item.box) > 3.0 * scale:
            continue
        options = []
        for side, points in hands:
            grip = _palm(points)
            palm_gap = _gap(grip, item.box)
            finger_gap = min(_gap(points[index], item.box) for index in range(4, 21))
            if item.name in VESSELS and palm_gap > .28 * scale and _gap(lips, item.box) > .78 * scale:
                continue
            if palm_gap <= .48 * scale and finger_gap <= .20 * scale:
                options.append((palm_gap + .5 * finger_gap, side, grip))
        if options:
            _, side, grip = min(options)
            found.append(_contact(item, side, grip, lips, scale))
    return tuple(found)


def drinking_zones(face, hands, width: int, height: int):
    if not hand_near_mouth(face, hands):
        return
    scale = face_scale(face)
    lx, ly = midpoint(face[13], face[14])
    x, y = min((_palm(points) for _, points in hands), key=lambda point: hypot(point[0] - lx, point[1] - ly))
    size = 2.70 if hypot(lx - x, ly - y) > 1.10 * scale else 2.25
    centers = ((x, y - min(.65 * scale, max(.12 * scale, y - ly + .08 * scale)), size),
               ((x + lx) * .5, y + (ly - y) * .25, 2.25))
    for cx, cy, extent in centers:
        half = extent * scale * .5
        left, right = max(0, round(cx - half)), min(width, round(cx + half))
        top, bottom = max(0, round(cy - half)), min(height, round(cy + half))
        if min(right - left, bottom - top) >= 55 and (right - left) * (bottom - top) <= width * height * .79:
            yield left, top, right, bottom


class DrinkTracker:
    def __init__(self):
        self.side = ""
        self.near_since = 0.0
        self.near_at = -100.0
        self.samples = 0
        self.real_contact = 0
        self.last_real: dict[str, tuple[Held, float]] = {}
        self.approach: dict[str, tuple[Held, float]] = {}
        self.latched = False
        self.away_since: float | None = None
        self.release_hold = .31
        self.blocked: tuple[str, ...] = ()

    def _project(self, face, hands, now, visible_holds=()) -> list[Held]:
        if not face:
            return []
        scale = face_scale(face)
        if scale < 24:
            return []
        lips = midpoint(face[13], face[14])
        results = []
        for side, points in hands:
            previous = self.last_real.get(side)
            if not previous or now - previous[1] > .92:
                continue
            if any(held.side == side and held.item.name not in VESSELS
                   and held.gap < .48 and held.item.score > .53 for held in visible_holds):
                continue
            held, _ = previous
            grip = _palm(points)
            dx, dy = grip[0] - held.grip[0], grip[1] - held.grip[1]
            if hypot(dx, dy) >= 1.30 * scale:
                continue
            l, t, r, b = held.item.box
            shifted = replace(held.item, box=(l + dx, t + dy, r + dx, b + dy))
            contact = _contact(shifted, side, grip, lips, scale)
            lifted = (self._tracked_lift(contact, now)
                      and lips[1] - .14 * scale <= grip[1] <= lips[1] + .72 * scale)
            if contact.at_mouth or lifted:
                results.append(contact)
        return results

    def _tracked_lift(self, candidate: Held, now: float) -> bool:
        approach = self.approach.get(candidate.side)
        return (approach is not None and now - approach[1] <= 1.15 and approach[0].item.score >= .32
                and candidate.hand_gap <= .71 and approach[0].hand_gap - candidate.hand_gap >= .30)

    def update(self, face, hands, held, now: float) -> bool:
        vessels = [candidate for candidate in held if candidate.item.name in VESSELS]
        by_side = {}
        for candidate in sorted(vessels, key=lambda held: (held.gap, -held.item.score)):
            by_side.setdefault(candidate.side, candidate)
        for side, candidate in by_side.items():
            self.last_real[side] = candidate, now
            if not candidate.at_mouth and .22 <= candidate.gap <= 1.18 and candidate.hand_gap >= .90:
                previous = self.approach.get(side)
                if previous is None or now - previous[1] > 1.15 or candidate.item.score >= previous[0].item.score:
                    self.approach[side] = candidate, now
        # Visible containers that have moved away take precedence over a short occlusion estimate.
        visible_sides = {candidate.side for candidate in vessels}
        choices = [(candidate, True) for candidate in vessels if candidate.at_mouth]
        if not choices:
            choices = [(candidate, False) for candidate in self._project(face, hands, now, held)
                       if candidate.side not in visible_sides]
        self.blocked = tuple({candidate.side for candidate in held if candidate.gap <= 1.10}
                             | {candidate.side for candidate, _ in choices})
        if self.latched and not choices and self.side not in visible_sides and face and now - self.near_since < 2.55:
            scale = face_scale(face)
            lips = midpoint(face[13], face[14])
            near_without_cup = any(side == self.side and hypot(grip[0]-lips[0], grip[1]-lips[1]) < .85 * scale
                                   for side, grip in ((side, _palm(points)) for side, points in hands))
            if near_without_cup:
                self.blocked = tuple({*self.blocked, self.side})
                self.away_since = None
                return False
        if not choices:
            if self.away_since is None:
                self.away_since = now
            if now - self.away_since >= self.release_hold:
                self.latched = False
                self.samples = self.real_contact = 0
            return False

        self.away_since = None
        candidate, exact = min(choices, key=lambda pair: (pair[0].gap, -pair[0].item.score))
        if self.latched:
            self.near_at = now
            return False
        if candidate.side != self.side or now - self.near_at > .56 or not self.samples:
            self.side = candidate.side
            self.near_since = now
            self.samples = self.real_contact = 0
        self.samples += 1
        self.real_contact += int(exact)
        self.near_at = now
        approach = self.approach.get(candidate.side)
        from_away = approach is not None and now - approach[1] < 2.8
        # Two independently sampled frames can establish a lifted cup. A cup already
        # near the mouth needs a longer positive sequence.
        dwell = .125 if from_away else .34
        strong_enough = self.real_contact >= (1 if from_away else 2) or self._tracked_lift(candidate, now)
        if self.samples >= 2 and now - self.near_since >= dwell and strong_enough:
            self.latched = True
            return True
        return False

