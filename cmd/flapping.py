"""Collect a scoped, atomic port-flapping snapshot through the configured source."""
import json
import math
import time
from datetime import datetime, timezone
from pathlib import Path

from atomic_json import write_json_atomic
from snmp import base_instance, query_prometheus_result

RULE = "changes(ifOperStatus[20m]) > 5"
WINDOW_SECONDS = 1200


def timestamp(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat().replace("+00:00", "Z")


def load_json(path, fallback):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return fallback


def build_snapshot(series, topology, evaluated_at):
    # Resolve only inventory IP + ifIndex, never a fuzzy name or exporter IP.
    inventory = {}
    for node in topology.get("nodes", []):
        ip = base_instance(str(node.get("ip") or ""))
        if not ip or not node.get("id"):
            continue
        for port in node.get("ports") or []:
            key = (ip, str(port.get("ifIndex", "")))
            inventory[key] = (node, port) if key not in inventory else None
    ports = {}
    unmatched = 0
    for item in series:
        metric = item.get("metric") or {}
        count = float(item["value"][1])
        if not math.isfinite(count) or count < 0:
            raise ValueError("Invalid change count")
        if count <= 5:
            continue
        key = (base_instance(str(metric.get("snmp_target") or metric.get("instance") or "")), str(metric.get("ifIndex", "")))
        match = inventory.get(key)
        if not match:
            unmatched += 1
            continue
        node, port = match
        current = ports.get(key)
        if current and current["changes"] >= count:
            continue
        ports[key] = {
            "device_id": node["id"], "ip": key[0], "ifIndex": key[1],
            "port_name": port.get("ifName") or port.get("ifDescr") or key[1],
            "ifDescr": port.get("ifDescr", ""), "changes": count,
        }
    return {
        "rule": RULE, "window_minutes": 20, "threshold": 5,
        "updated_at": timestamp(evaluated_at), "checked_at": timestamp(time.time()),
        "window_start": timestamp(evaluated_at - WINDOW_SECONDS),
        "window_end": timestamp(evaluated_at), "stale": False,
        "unmatched_series": unmatched,
        "ports": sorted(ports.values(), key=lambda p: (-p["changes"], p["device_id"], p["port_name"])),
    }


def collect(root=Path(".")):
    path = root / "flapping.json"
    try:
        evaluated_at = time.time()
        series, ok = query_prometheus_result(RULE, require_complete=True)
        if not ok:
            raise ValueError("Query failed or incomplete")
        topology = load_json(root / "topology.json", None)
        if not isinstance(topology, dict) or not topology.get("nodes"):
            raise ValueError("Device inventory unavailable")
        if series:
            evaluated_at = float(series[0]["value"][0])
        snapshot = build_snapshot(series, topology, evaluated_at)
    except (ValueError, TypeError, KeyError, IndexError, OverflowError):
        snapshot = load_json(path, {})
        if not isinstance(snapshot, dict):
            snapshot = {}
        snapshot.update(rule=RULE, stale=True, checked_at=timestamp(time.time()),
                        error="Flapping query or inventory unavailable; retaining last successful result")
        write_json_atomic(path, snapshot)
        print("[WARNING] flapping: snapshot unavailable or stale")
        return False
    write_json_atomic(path, snapshot)
    print(f"[INFO] flapping: {len(snapshot['ports'])} ports; {snapshot['unmatched_series']} out-of-inventory series")
    return True


if __name__ == "__main__":
    raise SystemExit(0 if collect() else 1)
