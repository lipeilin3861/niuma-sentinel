"""Editable local rules over the face and hand landmarks the app already calculates."""

from dataclasses import dataclass
import json
from math import dist, isfinite
from pathlib import Path
import re

from behavior import FINGERS, Point, face_scale, midpoint
from habit_settings import BUBBLE, valid_style
from runtime_paths import ROOT, SETTINGS_ROOT


DEFAULT_RULES = ROOT / "rules" / "defaults.json"
RULES_PATH = SETTINGS_ROOT / "rules" / "rules.json"
TARGETS = {
    "eyes": ((33, 133), (263, 362)),
    "left_eye": ((263, 362),),
    "right_eye": ((33, 133),),
    "mouth": ((13, 14),),
    "nose": ((1, 2),),
}


@dataclass(frozen=True)
class Reminder:
    id: str
    label: str
    title: str
    message: str = ""
    style: str | None = None


BITE = Reminder("bite", "防啃手", "手放下来")
WATER = Reminder("water", "喝水提醒", "喝口水吧", "到你设定的间隔了，喝口水歇一下。")
DRINK_DONE = Reminder("@drink_done", "喝水已记录", "喝得漂亮", style=BUBBLE)
ACHIEVEMENT = Reminder("@achievement", "今日成就", "今日成就")
FACE_AWARD = Reminder("@face_award", "隐藏成就", "隐藏成就", style=BUBBLE)
STRETCH = Reminder("@stretch", "活动提醒", "站起来动一动", style=BUBBLE)
GUEST = Reminder("@guest", "老板来了", "有人入镜", "哨兵在画面里看见了第二个人。", BUBBLE)
EYE_GLASSES = Reminder("@glasses", "扶眼镜 / 揉眼", "镜框已经站稳了", style=BUBBLE)
EYE_RUB = Reminder("@eye_rub", "扶眼镜 / 揉眼", "手离眼睛远一点", style=BUBBLE)
GENTLE_REMINDERS = frozenset((WATER.id, DRINK_DONE.id, ACHIEVEMENT.id, FACE_AWARD.id, STRETCH.id))


@dataclass(frozen=True)
class Rule:
    reminder: Reminder
    enabled: bool
    target: str
    radius: float
    hold_seconds: float


def _number(value, low, high):
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and isfinite(value)
        and low <= value <= high
    )


def validate_rules(document) -> tuple[Rule, ...]:
    if not isinstance(document, dict) or document.get("version") != 1 or not isinstance(document.get("rules"), list):
        raise ValueError("规则文件需要 version 为 1，并包含 rules 列表。")
    found = set()
    rules = []
    for number, raw in enumerate(document["rules"], 1):
        if not isinstance(raw, dict):
            raise ValueError(f"第 {number} 条规则需要是一个对象。")
        id_value = raw.get("id")
        if (not isinstance(id_value, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,35}", id_value)
                or id_value in found or id_value in (BITE.id, WATER.id, "posture")):
            raise ValueError(f"第 {number} 条规则的 id 无效或重名。")
        found.add(id_value)
        target = raw.get("target")
        if not isinstance(target, str) or target not in TARGETS:
            raise ValueError(f"规则 {id_value} 的 target 不受支持。")
        radius, duration = raw.get("radius"), raw.get("hold_seconds")
        if not _number(radius, .05, .60) or not _number(duration, .15, 8):
            raise ValueError(f"规则 {id_value} 需要有效的 radius 和 hold_seconds。")
        enabled = raw.get("enabled")
        if not isinstance(enabled, bool):
            raise ValueError(f"规则 {id_value} 的 enabled 需要是 true 或 false。")
        for key, maximum in (("label", 16), ("title", 20), ("message", 95)):
            value = raw.get(key)
            if not isinstance(value, str) or not value.strip() or not 1 <= len(value) <= maximum or "\n" in value:
                raise ValueError(f"规则 {id_value} 的 {key} 需要是 1 至 {maximum} 字的单行文字。")
        style = raw.get("style", BUBBLE)
        if not valid_style(style):
            raise ValueError(f"规则 {id_value} 的 style 需要是 bubble 或 dialog。")
        reminder = Reminder(id_value, raw["label"], raw["title"], raw["message"], style)
        rules.append(Rule(reminder, enabled, target, float(radius), float(duration)))
    return tuple(rules)


def load_rule_document(path=RULES_PATH):
    try:
        document = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        if path != RULES_PATH or path == DEFAULT_RULES:
            raise
        defaults = DEFAULT_RULES.read_bytes()
        validate_rules(json.loads(defaults.decode("utf-8-sig")))
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open("xb") as stream:
                stream.write(defaults)
        except FileExistsError:
            pass
        document = json.loads(path.read_text(encoding="utf-8-sig"))
    return document, validate_rules(document)


def write_rule_document(document, path=RULES_PATH) -> tuple[Rule, ...]:
    rules = validate_rules(document)
    temporary = path.with_suffix(".tmp")
    try:
        temporary.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return rules


@dataclass
class _Track:
    since: float | None = None
    last_seen: float = -100.0
    fired: bool = False
    last_alert: float = -100.0


class RuleMonitor:
    def __init__(self):
        self._rules: tuple[Rule, ...] | None = None
        self._track: dict[str, _Track] = {}

    def update(self, rules: tuple[Rule, ...], face: list[Point], hands, now: float) -> list[Reminder]:
        if rules is not self._rules:
            self._rules = rules
            self._track = {rule.reminder.id: _Track() for rule in rules if rule.enabled}
        if not self._track:
            return []
        scale = face_scale(face)
        tips = [points[tip] for _, points in hands for tip, _, _ in FINGERS] if scale >= 24 else []
        alerts = []
        for rule in rules:
            if not rule.enabled:
                continue
            track = self._track[rule.reminder.id]
            near = False
            if tips:
                centers = [midpoint(face[left], face[right]) for left, right in TARGETS[rule.target]]
                radius = scale * rule.radius
                near = any(dist(tip, center) <= radius for center in centers for tip in tips)
            if near:
                if track.since is None or now - track.last_seen > .27:
                    track.since = now
                track.last_seen = now
                if not track.fired and now - track.since >= rule.hold_seconds and now - track.last_alert >= 7:
                    track.fired = True
                    track.last_alert = now
                    alerts.append(rule.reminder)
            elif now - track.last_seen > .27:
                track.since, track.fired = None, False
        return alerts

