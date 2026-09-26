#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from atomic_json import write_json_atomic


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REGISTRY = ROOT / "config" / "environments.json"
DEFAULT_SECRETS = ROOT / "config" / ".env_sources.local.json"
DEFAULT_LOOP_INTERVAL = 60


def utc_now():
    return datetime.now(timezone.utc)


def iso_now():
    return utc_now().isoformat()


def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return default


def dump_json(path, data):
    write_json_atomic(path, data)


def resolve_path(base_dir: Path, value: str) -> Path:
    raw = str(value or "").strip()
    if not raw:
        return base_dir
    path = Path(raw)
    if path.is_absolute():
        return path
    return (base_dir / path).resolve()


def read_registry():
    registry_path = Path(os.environ.get("ENV_REGISTRY", str(DEFAULT_REGISTRY))).resolve()
    registry = load_json(registry_path, {"default": "", "environments": []})
    return registry_path, registry


def read_secrets():
    secrets_path = Path(os.environ.get("ENV_SOURCES_FILE", str(DEFAULT_SECRETS))).resolve()
    return load_json(secrets_path, {})


def file_mtime_iso(path: Path):
    if not path.exists():
        return None
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()


def bool_env(value):
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def query_gateway_mode(env: dict) -> bool:
    value = str(env.get("PROM_QUERY_URL") or env.get("PROM_URL") or "").strip()
    return "/query-gateway/" in value


def lldp_refresh_due(spec: dict, data_root: Path) -> bool:
    interval = int(spec.get("lldp_refresh_sec", 3600))
    if interval <= 0:
        return True
    marker = load_json(data_root / "lldp-refresh.json", {})
    checked_at = marker.get("checked_at") if isinstance(marker, dict) else None
    if not checked_at:
        return True
    try:
        last = datetime.fromisoformat(checked_at)
        return (utc_now() - last).total_seconds() >= interval
    except (TypeError, ValueError):
        return True


def refresh_enabled(spec: dict) -> bool:
    value = spec.get("refresh_enabled")
    if value is None:
        return True
    if isinstance(value, bool):
        return value
    return bool_env(value)


def build_env_vars(spec: dict, token: str, config_dir: Path):
    env = os.environ.copy()
    env["PROM_QUERY_URL"] = str(spec.get("prom_query_url") or "").strip()
    env["CONFIG_DIR"] = str(config_dir)
    vm_targets_url = str(spec.get("vm_targets_url") or "").strip()
    env["VM_TARGETS_URL"] = vm_targets_url
    env.pop("PROM_BEARER_TOKEN", None)
    if token:
        env["PROM_BEARER_TOKEN"] = token
    env["PROM_SKIP_TLS_VERIFY"] = "1" if bool_env(spec.get("prom_skip_tls_verify")) else "0"
    return env


def run_step(label: str, command, cwd: Path, env: dict):
    process = subprocess.Popen(
        command,
        cwd=str(cwd),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    if process.stdout:
        for line in process.stdout:
            line = line.rstrip()
            if line:
                print(f"[{label}] {line}", flush=True)
    return process.wait() == 0


def load_topology_config(config_dir: Path):
    return load_json(config_dir / "topology_config.json", {})


def port_is_up(port: dict) -> bool:
    status = port.get("status")
    if status is not None:
        try:
            return int(status) == 0
        except Exception:
            pass
    return False


def normalize_ports(value):
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [item.strip() for item in value.split("|") if item.strip()]
    return []


def find_port(node: dict, name: str):
    lookup = str(name or "").strip().lower()
    for port in node.get("ports", []) or []:
        candidates = [
            port.get("ifName"),
            port.get("ifDescr"),
            port.get("ifAlias"),
            port.get("ifIndex"),
        ]
        if any(str(item or "").strip().lower() == lookup for item in candidates):
            return port
    return None


def build_line_monitor_json(spec: dict, config_dir: Path, data_root: Path):
    topo_cfg = load_topology_config(config_dir)
    monitor_cfg = (((topo_cfg or {}).get("features") or {}).get("line_monitors") or {})
    groups = monitor_cfg.get("groups") or []
    if monitor_cfg.get("enabled") is not True or not groups:
        payload = {"lines": [], "last_updated": iso_now()}
        dump_json(data_root / "prometheus.json", payload)
        return True

    topology = load_json(data_root / "topology.json", {"nodes": []})
    node_by_id = {
        str(node.get("id") or "").strip(): node
        for node in topology.get("nodes", []) if isinstance(node, dict)
    }
    lines = []
    updated_at = iso_now()
    for group in groups:
        for item in group.get("items") or []:
            device_id = str(item.get("device_id") or group.get("device_id") or "").strip()
            node = node_by_id.get(device_id, {})
            ports = normalize_ports(item.get("ports") or group.get("ports"))
            port_states = []
            for name in ports:
                port = find_port(node, name)
                port_states.append({
                    "name": name,
                    "status": 0 if (port and port_is_up(port)) else 1,
                    "status_text": "UP" if (port and port_is_up(port)) else "DOWN",
                })
            is_up = bool(port_states) and all(entry["status"] == 0 for entry in port_states)
            lines.append({
                "id": item.get("id") or item.get("source_id") or item.get("label"),
                "name": item.get("label") or item.get("id") or "未命名专线",
                "display_name": item.get("label") or item.get("id") or "未命名专线",
                "status": 0 if is_up else 1,
                "status_text": "UP" if is_up else "DOWN",
                "device_id": device_id,
                "ports": ports,
                "port_states": port_states,
                "updated_at": updated_at,
            })
    dump_json(data_root / "prometheus.json", {"lines": lines, "last_updated": updated_at})
    return True


def ensure_links(data_root: Path, env: dict, force=False):
    links_path = data_root / "links.json"
    topology_path = data_root / "topology.json"
    links_mtime = links_path.stat().st_mtime if links_path.exists() else 0
    topology_mtime = topology_path.stat().st_mtime if topology_path.exists() else 0
    topology_is_newer = topology_mtime > links_mtime
    if links_path.exists():
        payload = load_json(links_path, None)
        if not force and isinstance(payload, list) and payload and not topology_is_newer:
            return True
    if topology_path.exists():
        if not run_step("links-from-topology", [sys.executable, str(ROOT / "cmd" / "snmp.py"), "--links-from-topology"], data_root, env):
            return False
        payload = load_json(data_root / "links-alias.json", None)
        if isinstance(payload, list) and payload:
            dump_json(links_path, payload)
            return True
    print("[ERROR] no fresh derived links; retain existing links.json without marking success")
    return False


def refresh_environment(spec: dict, secrets: dict, registry_dir: Path):
    code = str(spec.get("code") or "").strip()
    if not code:
        return False
    config_dir = resolve_path(registry_dir, spec.get("config_dir"))
    data_root = resolve_path(registry_dir, spec.get("data_root"))
    data_root.mkdir(parents=True, exist_ok=True)
    token = str(secrets.get(spec.get("secret_ref"), "") or "").strip()
    env = build_env_vars(spec, token, config_dir)
    gateway = query_gateway_mode(env)
    lldp_spec = {"lldp_refresh_sec": 3600 if gateway else 0, **spec}
    refresh_lldp = lldp_refresh_due(lldp_spec, data_root)
    env["PROM_QUERY_LLDP_DETAILS"] = "1" if refresh_lldp else "0"

    print(f"[INFO] refresh env={code} config={config_dir} data={data_root} lldp={refresh_lldp}")
    devices_ok = run_step(f"{code}-devices", [sys.executable, str(ROOT / "cmd" / "devices.py")], data_root, env)
    snmp_ok = devices_ok and run_step(f"{code}-snmp", [sys.executable, str(ROOT / "cmd" / "snmp.py")], data_root, env)
    if snmp_ok and refresh_lldp:
        dump_json(data_root / "lldp-refresh.json", {"checked_at": iso_now()})
    topology_ok = devices_ok and snmp_ok

    # Both direct and gateway sources use inventory/LLDP/alias resolution. A
    # separate LLDP-only writer would drop DOWN links whose neighbors vanished.
    lldp_ok = snmp_ok
    links_ok = snmp_ok and ensure_links(data_root, env, force=True)
    monitor_ok = snmp_ok and build_line_monitor_json(spec, config_dir, data_root)
    monitor_required = ((((load_topology_config(config_dir) or {}).get("features") or {}).get("line_monitors") or {}).get("enabled") is True)
    topology = load_json(data_root / "topology.json", {})
    nodes = topology.get("nodes", []) if isinstance(topology, dict) else []
    stale_nodes = sum(bool(node.get("collection_stale")) for node in nodes if isinstance(node, dict))

    status = {
        "env": code,
        "name": spec.get("name") or code,
        "checked_at": iso_now(),
        "datasets": {
            "topology": {
                "required": True,
                "updated_at": file_mtime_iso(data_root / "topology.json"),
                "error_after_sec": int(spec.get("topology_error_after_sec") or 900),
            },
            "links": {
                "required": True,
                "updated_at": file_mtime_iso(data_root / "links.json"),
                "error_after_sec": int(spec.get("topology_error_after_sec") or 900),
            },
            "prometheus": {
                "required": monitor_required,
                "updated_at": file_mtime_iso(data_root / "prometheus.json"),
                "error_after_sec": int(spec.get("monitor_error_after_sec") or 180),
            },
        },
        "steps": {
            "devices": devices_ok,
            "snmp": snmp_ok,
            "lldp": lldp_ok,
            "links": links_ok,
            "prometheus": monitor_ok,
        },
        "quality": {"stale_nodes": stale_nodes, "total_nodes": len(nodes)},
    }
    dump_json(data_root / "status.json", status)
    return topology_ok and links_ok and monitor_ok


def normalize_env_filters(values):
    selected = set()
    for value in values or []:
        for item in str(value or "").split(","):
            code = item.strip()
            if code:
                selected.add(code)
    return selected


def run_once(selected_envs=None):
    registry_path, registry = read_registry()
    secrets = read_secrets()
    registry_dir = registry_path.parent
    results = []
    selected = normalize_env_filters(selected_envs)
    for spec in registry.get("environments") or []:
        code = str(spec.get("code") or "").strip() or "<unknown>"
        if selected and code not in selected:
            print(f"[INFO] skip env={code} filtered")
            continue
        if not refresh_enabled(spec):
            print(f"[INFO] skip env={code} refresh_enabled=false")
            results.append(True)
            continue
        results.append(refresh_environment(spec, secrets, registry_dir))
    return all(results) if results else True


def main():
    parser = argparse.ArgumentParser(description="refresh topology caches for multiple environments")
    parser.add_argument("--loop", action="store_true", help="run continuously")
    parser.add_argument("--interval", type=int, default=DEFAULT_LOOP_INTERVAL, help="loop interval seconds")
    parser.add_argument("--env", action="append", default=[], help="only refresh the specified environment code, can be repeated or comma-separated")
    args = parser.parse_args()

    while True:
        ok = run_once(args.env)
        if not args.loop:
            return 0 if ok else 1
        time.sleep(max(10, int(args.interval)))


if __name__ == "__main__":
    raise SystemExit(main())
