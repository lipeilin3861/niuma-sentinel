"""Local reminder preferences and time-stamped drink history."""

from bisect import bisect_left
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
import json
from math import isfinite
from pathlib import Path
from time import time

from runtime_paths import SETTINGS_ROOT

DATA_PATH = SETTINGS_ROOT / "local_data" / "habits.json"
DIALOG = "dialog"
BUBBLE = "bubble"
STYLE_NAMES = {DIALOG: "中央大窗", BUBBLE: "右下角气泡"}
MONITOR_FLAGS = {"bite": "bite_enabled", "water": "water_active", "eye": "eye_enabled",
                 "break": "break_enabled", "guest": "guest_enabled", "rules": "rules_enabled",
                 "expression": "expression_enabled"}
INTERVALS = (1, 2, 3, 4, 6, 8, 12)
BREAK_MINUTES = (60, 90)
WATER_GOAL = 10
ZERO_BITE_COVERAGE = 2 * 60 * 60
AWARD_HOUR = 18
WATER_AWARD = "water_buffalo"
BITE_AWARD = "hands_off"
BLINK_AWARD = "blink"
GOOFY_AWARD = "goofy_face"
BLINK_GOAL = 30
SECRET_AWARDS = (BLINK_AWARD, GOOFY_AWARD)
AWARD_TITLES = {WATER_AWARD: "合格的水牛", BITE_AWARD: "手指今日平安", BLINK_AWARD: "润滑双眼", GOOFY_AWARD: "鬼脸认证"}
SECRET_DESCRIPTIONS = {
    BLINK_AWARD: "自然眨眼 30 次，牛眼顺滑开工。",
    GOOFY_AWARD: "哨兵刚才差点以为来了个新同事。",
}


@dataclass(frozen=True)
class DayRecord:
    watched_seconds: float = 0
    awards: tuple[str, ...] = ()


@dataclass(frozen=True)
class HabitData:
    bite_enabled: bool = True
    bite_style: str = DIALOG
    water_active: bool = True
    water_style: str = BUBBLE
    water_enabled: bool = False
    water_detection: bool = True
    water_hours: int = 2
    started_at: float = 0
    reminded_at: float = 0
    drinks: tuple[float, ...] = field(default_factory=tuple)
    bites: tuple[float, ...] = field(default_factory=tuple)
    days: dict[str, DayRecord] = field(default_factory=dict)
    eye_enabled: bool = False
    eye_style: str = BUBBLE
    break_enabled: bool = True
    break_minutes: int = BREAK_MINUTES[0]
    break_style: str = BUBBLE
    guest_enabled: bool = True
    guest_style: str = BUBBLE
    rules_enabled: bool = True
    rules_style: str | None = None
    expression_enabled: bool = False
    expression_style: str = BUBBLE
    secret_blinks: int = 0

    def monitor_enabled(self, name: str) -> bool:
        return getattr(self, MONITOR_FLAGS[name])

    def monitor_style(self, name: str) -> str | None:
        return getattr(self, f"{name}_style")

    @property
    def records_drinks(self) -> bool:
        return self.water_active and self.water_detection

    @property
    def unlocked_secrets(self) -> frozenset[str]:
        return frozenset(award for row in self.days.values() for award in row.awards if award in SECRET_AWARDS)

    @property
    def next_at(self) -> float | None:
        if not (self.water_active and self.water_enabled):
            return None
        return max(self.started_at, self.reminded_at, self.drinks[-1] if self.drinks else 0) + self.water_hours * 3600


def valid_style(value) -> bool:
    return isinstance(value, str) and value in STYLE_NAMES


def _valid_time(value) -> bool:
    return type(value) in (int, float) and isfinite(value) and 0 <= value <= 4_102_444_800


def _days(raw) -> dict[str, DayRecord]:
    if not isinstance(raw, dict):
        raise ValueError("成就日期记录无效。")
    days = {}
    for key, value in raw.items():
        if date.fromisoformat(key).isoformat() != key or not isinstance(value, dict):
            raise ValueError("成就日期记录无效。")
        watched = value.get("watched_seconds", 0)
        awards = value.get("awards", [])
        if not _valid_time(watched) or watched > 90_000 or not isinstance(awards, list):
            raise ValueError("当天值班时长或成就记录无效。")
        if any(not isinstance(award, str) or award not in AWARD_TITLES for award in awards):
            raise ValueError("成就名称无效。")
        days[key] = DayRecord(watched, tuple(dict.fromkeys(awards)))
    return days


def _daily_count(stamps, when: datetime) -> int:
    start = when.replace(hour=0, minute=0, second=0, microsecond=0)
    return bisect_left(stamps, (start + timedelta(days=1)).timestamp()) - bisect_left(stamps, start.timestamp())


def _parse(raw) -> HabitData:
    if not isinstance(raw, dict) or raw.get("version") != 1:
        raise ValueError("喝水记录格式需要是版本 1。")
    defaults = HabitData()
    enabled = raw.get("water_enabled", defaults.water_enabled)
    detection = raw.get("water_detection", defaults.water_detection)
    hours = raw.get("water_hours", defaults.water_hours)
    started = raw.get("started_at", 0)
    reminded = raw.get("reminded_at", 0)
    drinks = raw.get("drinks", [])
    bites = raw.get("bites", [])
    break_minutes = raw.get("break_minutes", defaults.break_minutes)
    secret_blinks = raw.get("secret_blinks", defaults.secret_blinks)
    if type(enabled) is not bool or type(detection) is not bool or type(hours) is not int or hours not in INTERVALS:
        raise ValueError("喝水功能的开关或间隔无效。")
    if not _valid_time(started) or not _valid_time(reminded) or (enabled and not started):
        raise ValueError("喝水提醒的计时记录无效。")
    if not isinstance(drinks, list) or not isinstance(bites, list) or not all(_valid_time(stamp) for stamp in (*drinks, *bites)):
        raise ValueError("喝水或啃手历史包含无效时间。")
    if type(break_minutes) is not int or break_minutes not in BREAK_MINUTES:
        raise ValueError("活动提醒设置无效。")
    if type(secret_blinks) is not int or not 0 <= secret_blinks <= BLINK_GOAL:
        raise ValueError("表情彩蛋记录无效。")
    monitors = {key: raw.get(key, getattr(defaults, key)) for key in MONITOR_FLAGS.values()}
    monitors["water_active"] = raw.get("water_active", enabled or detection)
    if any(type(value) is not bool for value in monitors.values()):
        raise ValueError("监测开关无效。")
    styles = {}
    for name in MONITOR_FLAGS:
        field_name = f"{name}_style"
        value = raw.get(field_name, defaults.monitor_style(name))
        if not valid_style(value) and not (name == "rules" and value is None):
            raise ValueError("提醒样式需要是 dialog 或 bubble。")
        styles[field_name] = value
    return HabitData(water_enabled=enabled, water_detection=detection,
                     water_hours=hours, started_at=started, reminded_at=reminded, drinks=tuple(sorted(drinks)),
                     bites=tuple(sorted(bites)), days=_days(raw.get("days", {})), break_minutes=break_minutes,
                     secret_blinks=secret_blinks,
                     **monitors, **styles)


class Habits:
    def __init__(self, path=DATA_PATH):
        self.path = path
        self.data = HabitData()
        self.error = ""
        try:
            raw = json.loads(path.read_text(encoding="utf-8-sig"))
            self.data = _parse(raw)
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as error:
            self.error = str(error)

    def _save(self, data: HabitData) -> None:
        if self.error:
            raise ValueError(f"请检查本地习惯文件。\n{self.error}")
        if data == self.data:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "water_enabled": data.water_enabled,
            "water_detection": data.water_detection,
            "water_hours": data.water_hours,
            "started_at": data.started_at,
            "reminded_at": data.reminded_at,
            "drinks": list(data.drinks),
            "bites": list(data.bites),
            "days": {day: {"watched_seconds": row.watched_seconds, "awards": list(row.awards)}
                     for day, row in data.days.items()},
            "break_minutes": data.break_minutes,
            "secret_blinks": data.secret_blinks,
            **{field_name: data.monitor_enabled(name) for name, field_name in MONITOR_FLAGS.items()},
            **{f"{name}_style": data.monitor_style(name) for name in MONITOR_FLAGS},
        }
        temporary = self.path.with_suffix(".tmp")
        try:
            temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            temporary.replace(self.path)
        finally:
            temporary.unlink(missing_ok=True)
        self.data = data

    def set_bite_style(self, style: str) -> None:
        self.set_monitor_style("bite", style)

    def set_monitor_style(self, name: str, style: str | None) -> None:
        if name not in MONITOR_FLAGS or not (valid_style(style) or (name == "rules" and style is None)):
            raise ValueError("请选择可用的提醒样式。")
        self._save(replace(self.data, **{f"{name}_style": style}))

    def set_monitor_enabled(self, name: str, enabled: bool, now=None) -> None:
        if name not in MONITOR_FLAGS or type(enabled) is not bool:
            raise ValueError("监测开关无效。")
        options = {MONITOR_FLAGS[name]: enabled}
        if name == "water" and enabled and not self.data.water_active:
            if self.data.water_enabled:
                options.update(started_at=time() if now is None else now, reminded_at=0)
            elif not self.data.water_detection:
                options["water_detection"] = True
        self._save(replace(self.data, **options))

    def configure_water(self, *, enabled: bool, hours: int, style: str, detection: bool | None = None, now=None) -> None:
        detection = self.data.water_detection if detection is None else detection
        if type(enabled) is not bool or type(detection) is not bool or type(hours) is not int or hours not in INTERVALS or not valid_style(style):
            raise ValueError("请选择可用的喝水提醒间隔和样式。")
        now = time() if now is None else now
        started = now if enabled and not self.data.water_enabled else self.data.started_at
        reminded = 0 if enabled and not self.data.water_enabled else self.data.reminded_at
        self._save(replace(self.data, water_enabled=enabled, water_detection=detection, water_hours=hours,
                           water_style=style, started_at=started, reminded_at=reminded))

    def record(self, now=None) -> None:
        now = time() if now is None else now
        if not _valid_time(now):
            raise ValueError("记录的时间无效。")
        self._save(replace(self.data, drinks=tuple(sorted((*self.data.drinks, now)))))

    def record_bite(self, now: float) -> None:
        if not _valid_time(now):
            raise ValueError("啃手记录的时间无效。")
        self._save(replace(self.data, bites=tuple(sorted((*self.data.bites, now)))))

    def set_eye_enabled(self, enabled: bool) -> None:
        self.set_monitor_enabled("eye", enabled)

    def configure_break(self, enabled: bool, minutes: int) -> None:
        if minutes not in BREAK_MINUTES:
            raise ValueError("请选择 60 或 90 分钟。")
        self._save(replace(self.data, break_enabled=enabled, break_minutes=minutes))

    def undo_last(self) -> None:
        if self.data.drinks:
            self._save(replace(self.data, drinks=self.data.drinks[:-1]))

    def today_count(self, now=None) -> int:
        return _daily_count(self.data.drinks, datetime.fromtimestamp(time() if now is None else now))

    def today_bites(self, now=None) -> int:
        return _daily_count(self.data.bites, datetime.fromtimestamp(time() if now is None else now))

    def add_watch_time(self, day: str, seconds: float) -> None:
        row = self.data.days.get(day, DayRecord())
        updated = replace(row, watched_seconds=min(90_000, row.watched_seconds + seconds))
        self._save(replace(self.data, days={**self.data.days, day: updated}))

    def collect_awards(self, now: datetime) -> tuple[str, ...]:
        if now.hour < AWARD_HOUR:
            return ()
        key = now.date().isoformat()
        row = self.data.days.get(key, DayRecord())
        eligible = []
        if self.data.water_active and _daily_count(self.data.drinks, now) >= WATER_GOAL:
            eligible.append(WATER_AWARD)
        if self.data.bite_enabled and row.watched_seconds >= ZERO_BITE_COVERAGE and not _daily_count(self.data.bites, now):
            eligible.append(BITE_AWARD)
        earned = tuple(award for award in eligible if award not in row.awards)
        if earned:
            updated = replace(row, awards=(*row.awards, *earned))
            self._save(replace(self.data, days={**self.data.days, key: updated}))
        return earned

    def record_expression(self, award: str, stamp: float) -> bool:
        data = self.data
        if not data.expression_enabled or award not in SECRET_AWARDS or award in data.unlocked_secrets:
            return False
        if award == BLINK_AWARD:
            data = replace(data, secret_blinks=min(BLINK_GOAL, data.secret_blinks + 1))
            if data.secret_blinks < BLINK_GOAL:
                self._save(data)
                return False
        day = datetime.fromtimestamp(stamp).date().isoformat()
        row = data.days.get(day, DayRecord())
        self._save(replace(data, days={**data.days, day: replace(row, awards=(*row.awards, award))}))
        return True

    def remind_due(self, now=None) -> bool:
        due = self.data.next_at
        return due is not None and (time() if now is None else now) >= due

    def mark_reminded(self, now=None) -> None:
        self._save(replace(self.data, reminded_at=time() if now is None else now))


class WorkClock:
    def __init__(self):
        self.elapsed = 0.0
        self._last_tick: float | None = None
        self._last_active: float | None = None
        self._last_reminder = 0.0

    def update(self, now: float, *, active: bool, minutes: int) -> bool:
        delta = 0.0 if self._last_tick is None else now - self._last_tick
        self._last_tick = now
        if delta > 8 or (self._last_active is not None and now - self._last_active >= 300):
            self.elapsed = self._last_reminder = 0.0
            self._last_active = None
        if not active:
            return False
        # Count only seconds bounded by two active samples.
        if self._last_active is not None and 0 <= now - self._last_active <= 3:
            self.elapsed += delta
        self._last_active = now
        if self.elapsed - self._last_reminder >= minutes * 60:
            self._last_reminder = self.elapsed
            return True
        return False

