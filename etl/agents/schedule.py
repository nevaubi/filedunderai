"""Five-field cron schedules evaluated in the user's time zone (minute hour day-of-month month day-of-week).

Supports `*`, lists, ranges, steps (`*/15`, `1-5`, `0,30`), day-of-week 0-7 (0 and 7 are Sunday) and the usual
cron rule that when both day-of-month and day-of-week are restricted, either may match. `next_after` works in local
wall-clock time and converts to UTC, so "7:00 every Monday" stays 7:00 across daylight-saving changes; a local time
skipped by a spring-forward change runs at the first valid minute after it.
"""
import datetime as dt
from zoneinfo import ZoneInfo

FIELDS = (("minute", 0, 59), ("hour", 0, 23), ("dom", 1, 31), ("month", 1, 12), ("dow", 0, 7))
DOW_NAMES = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]

PRESETS = {
    "hourly": "0 * * * *",
    "daily": "0 7 * * *",
    "weekdays": "0 7 * * 1-5",
    "weekly": "0 7 * * 1",
    "monthly": "0 7 1 * *",
}


class CronError(ValueError):
    pass


def _field(text, lo, hi):
    out = set()
    for part in text.split(","):
        part = part.strip()
        if not part:
            raise CronError("empty cron field")
        step = 1
        if "/" in part:
            part, s = part.split("/", 1)
            if not s.isdigit() or int(s) < 1:
                raise CronError(f"bad step {s!r}")
            step = int(s)
        if part == "*":
            a, b = lo, hi
        elif "-" in part:
            x, y = part.split("-", 1)
            if not (x.isdigit() and y.isdigit()):
                raise CronError(f"bad range {part!r}")
            a, b = int(x), int(y)
        elif part.isdigit():
            a = b = int(part)
            if step > 1:
                b = hi
        else:
            raise CronError(f"bad cron value {part!r}")
        if a < lo or b > hi or a > b:
            raise CronError(f"{part!r} outside {lo}-{hi}")
        out.update(range(a, b + 1, step))
    return out


def parse(expr):
    expr = PRESETS.get(str(expr).strip().lower(), str(expr).strip())
    parts = expr.split()
    if len(parts) != 5:
        raise CronError("cron needs five fields: minute hour day-of-month month day-of-week")
    sets = [_field(p, lo, hi) for p, (_, lo, hi) in zip(parts, FIELDS)]
    dow = {d % 7 for d in sets[4]}
    return {"minute": sorted(sets[0]), "hour": sorted(sets[1]), "dom": sets[2], "month": sets[3], "dow": dow,
            "dom_any": parts[2] == "*", "dow_any": parts[4] == "*", "expr": expr}


def _day_ok(c, d):
    if d.month not in c["month"]:
        return False
    dom_ok = d.day in c["dom"]
    dow_ok = ((d.weekday() + 1) % 7) in c["dow"]
    if c["dom_any"] and c["dow_any"]:
        return True
    if c["dom_any"]:
        return dow_ok
    if c["dow_any"]:
        return dom_ok
    return dom_ok or dow_ok


def next_after(expr, after_utc, tz="America/Chicago"):
    """First run strictly after `after_utc` (aware datetime or ISO string). Returns an aware UTC datetime."""
    c = parse(expr)
    if isinstance(after_utc, str):
        after_utc = dt.datetime.fromisoformat(after_utc.replace("Z", "+00:00"))
    if after_utc.tzinfo is None:
        after_utc = after_utc.replace(tzinfo=dt.timezone.utc)
    zone = ZoneInfo(tz or "America/Chicago")
    local = after_utc.astimezone(zone)
    start = local.replace(second=0, microsecond=0, tzinfo=None) + dt.timedelta(minutes=1)
    day = start.date()
    for _ in range(366 * 5):
        if _day_ok(c, day):
            for h in c["hour"]:
                for m in c["minute"]:
                    wall = dt.datetime.combine(day, dt.time(h, m))
                    if wall < start:
                        continue
                    aware = wall.replace(tzinfo=zone)
                    utc = aware.astimezone(dt.timezone.utc)
                    # A wall time inside a spring-forward gap maps to a later instant; keep it if still after.
                    if utc > after_utc:
                        return utc
        day += dt.timedelta(days=1)
    raise CronError("schedule never runs")


def iso(d):
    return d.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def describe(expr):
    """Short plain-English reading of common shapes, else the expression itself."""
    try:
        c = parse(expr)
    except CronError:
        return str(expr)
    if len(c["hour"]) == 24 and c["minute"] == [0]:
        return "Every hour"
    if len(c["hour"]) == 1 and len(c["minute"]) == 1:
        t = dt.time(c["hour"][0], c["minute"][0]).strftime("%-I:%M %p")
        if c["dom_any"] and c["dow_any"]:
            return f"Daily at {t}"
        if c["dom_any"] and c["dow"] == {1, 2, 3, 4, 5}:
            return f"Weekdays at {t}"
        if c["dom_any"]:
            return f"{', '.join(DOW_NAMES[d] for d in sorted(c['dow']))} at {t}"
        if c["dow_any"] and len(c["dom"]) == 1:
            n = next(iter(c["dom"]))
            suf = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
            return f"Monthly on the {n}{suf} at {t}"
    return c["expr"]
