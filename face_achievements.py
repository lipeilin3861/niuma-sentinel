"""Recognize two local expressions from the face landmarks already in memory."""

from math import dist
from statistics import median

from behavior import eye_apertures, face_scale, midpoint
from habit_settings import BLINK_AWARD, GOOFY_AWARD


class FaceAchievements:
    def __init__(self):
        self.calibration = []
        self.neutral: tuple[float, ...] | None = None
        self.last_sample = -100.0
        self.closed_at: float | None = None
        self.was_open = False
        self.goofy_at: float | None = None
        self.goofy_fired = False

    @staticmethod
    def _measure(face):
        scale = face_scale(face)
        if scale < 33:
            return None
        left, right = midpoint(face[33], face[133]), midpoint(face[263], face[362])
        center = midpoint(left, right)
        if abs(face[1][0] - center[0]) > .21 * scale or abs(left[1] - right[1]) > .23 * scale:
            return None
        eye_left, eye_right = eye_apertures(face)
        brows = ((left[1] - face[105][1]) + (right[1] - face[334][1])) / (2 * scale)
        mouth = dist(face[13], face[14]) / max(1.0, dist(face[61], face[291]))
        return eye_left, eye_right, brows, mouth

    def update(self, face, now: float) -> tuple[str, ...]:
        sample = self._measure(face)
        if sample is None or now - self.last_sample > .19:
            self.was_open = False
            self.closed_at = self.goofy_at = None
        if sample is None:
            return ()
        self.last_sample = now
        left, right, brows, mouth = sample
        if self.neutral is None:
            if .14 < min(left, right) and max(left, right) < .55 and mouth < .23:
                self.calibration.append((left, right, brows))
                if len(self.calibration) >= 20:
                    self.neutral = tuple(median(values) for values in zip(*self.calibration))
            return ()

        base_left, base_right, base_brows = self.neutral
        openings = (left / base_left, right / base_right)
        detected = []
        if max(openings) < .57 and self.was_open:
            if self.closed_at is None:
                self.closed_at = now
        if self.closed_at is not None and now - self.closed_at > .48:
            self.closed_at = None
            self.was_open = False
        if min(openings) >= .76:
            if self.closed_at is not None and .035 <= now - self.closed_at <= .48:
                detected.append(BLINK_AWARD)
            self.was_open = True
            self.closed_at = None

        eyes = min(openings)
        raised_brows = eyes >= 1.04 and brows - base_brows >= .026
        # A person who normally opens their eyes wide may have less room to raise the brow.
        wide_mouth_and_eyes = mouth >= .53 and eyes >= 1.02 and min(left, right) >= .35
        expressive = mouth >= .40 and (raised_brows or eyes >= 1.24 or wide_mouth_and_eyes)
        if expressive and not self.goofy_fired:
            if self.goofy_at is None:
                self.goofy_at = now
            elif now - self.goofy_at >= .67:
                self.goofy_fired = True
                detected.append(GOOFY_AWARD)
        else:
            self.goofy_at = None
        return tuple(detected)
