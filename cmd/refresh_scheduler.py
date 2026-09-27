"""Persistent cooldown policy for systemd-managed, one-shot collectors."""
import json
from datetime import datetime, timezone


def positive_seconds(value, fallback, minimum=1):
    try:
        return max(minimum, int(value))
    except (TypeError, ValueError):
        return max(minimum, fallback)


def epoch(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        return 0


def iso(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat()


def load_record(path):
    try:
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def cooldown(spec, schedule):
    interval = positive_seconds(spec.get("topology_refresh_sec"), 600, 600)
    try:
        duration = float(schedule.get("duration_sec") or 0)
    except (TypeError, ValueError):
        duration = 0
    return max(interval, 900) if duration > 300 else interval


def due_at(spec, root):
    schedule = load_record(root / "refresh-schedule.json")
    finished = epoch(schedule.get("finished_at"))
    if not finished:
        finished = epoch(load_record(root / "status.json").get("checked_at"))
    return finished + cooldown(spec, schedule) if finished else 0
