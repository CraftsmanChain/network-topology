#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
同步拓扑设备清单。

- devices.json: 兼容现有前端/脚本，输出设备 ID 数组
- devices-meta.json: 保存设备 ID、IP、job、up 状态等元数据

设备基线优先以 vmagent targets 中的 SNMP 抓取目标为准，回退到监控中的
网络设备 up 指标。这样即使某个设备当前无 sysName / ifIndex / LLDP
数据，也能继续出现在拓扑中并被标记为 DOWN。
"""

import json
import os
from typing import Dict, List
from urllib.parse import urlparse

import requests


DEFAULT_PROM_URLS = [
    "http://10.102.10.7:8481/select/0/prometheus/api/v1/query",
    "http://10.27.3.68:8481/select/0/prometheus/api/v1/query",
    "http://10.102.10.6:9090/api/v1/query",
]
ACTIVE_PROM_URL = ""
DEFAULT_TARGETS_URLS = [
    "http://10.102.10.7:8429/api/v1/targets",
    "http://10.102.10.6:8429/api/v1/targets",
]
ACTIVE_TARGETS_URL = ""
UP_QUERIES = [
    'up{job=~"snmp_.*",device_type="网络设备"}',
    'up{job="snmp_exporter"}',
    'up{job=~"snmp_.*"}',
]
ACTIVE_UP_QUERY = ""
TARGET_POOL_PREFIX = "snmp_exporter-"

DEVICES_JSON = "devices.json"
DEVICES_META_JSON = "devices-meta.json"
TOPOLOGY_JSON = "topology.json"


def candidate_prom_urls():
    for key in ("PROM_QUERY_URL", "PROM_URL"):
        value = os.environ.get(key, "").strip()
        if value:
            if not value.rstrip("/").endswith("/api/v1/query"):
                value = value.rstrip("/") + "/api/v1/query"
            return [value]
    return DEFAULT_PROM_URLS


def to_targets_url(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    if value.rstrip("/").endswith("/api/v1/targets"):
        return value

    parsed = urlparse(value)
    if parsed.scheme and parsed.hostname:
        return f"{parsed.scheme}://{parsed.hostname}:8429/api/v1/targets"

    return value.rstrip("/") + "/api/v1/targets"


def candidate_targets_urls():
    seen = set()
    urls: List[str] = []

    for key in ("VM_TARGETS_URL", "TARGETS_URL"):
        value = to_targets_url(os.environ.get(key, ""))
        if value and value not in seen:
            seen.add(value)
            urls.append(value)

    for value in candidate_prom_urls():
        targets_url = to_targets_url(value)
        if targets_url and targets_url not in seen:
            seen.add(targets_url)
            urls.append(targets_url)

    for value in DEFAULT_TARGETS_URLS:
        if value and value not in seen:
            seen.add(value)
            urls.append(value)

    return urls


def query_prometheus(query: str):
    urls = [ACTIVE_PROM_URL] if ACTIVE_PROM_URL else candidate_prom_urls()
    for prom_url in urls:
        if not prom_url:
            continue
        try:
            r = requests.get(prom_url, params={"query": query}, timeout=10)
            r.raise_for_status()
            data = r.json()
            return data.get("data", {}).get("result", [])
        except Exception as e:
            print(f"[ERROR] Prometheus/VictoriaMetrics 查询失败 {prom_url}: {e}")
    return []


def choose_prom_url() -> str:
    global ACTIVE_PROM_URL, ACTIVE_UP_QUERY
    for prom_url in candidate_prom_urls():
        for probe_query in UP_QUERIES:
            try:
                r = requests.get(prom_url, params={"query": probe_query}, timeout=10)
                r.raise_for_status()
                data = r.json()
                if data.get("data", {}).get("result", []):
                    ACTIVE_PROM_URL = prom_url
                    ACTIVE_UP_QUERY = probe_query
                    print(f"[INFO] 使用监控查询地址: {prom_url}")
                    print(f"[INFO] 使用设备发现查询: {probe_query}")
                    return prom_url
            except Exception as e:
                print(f"[ERROR] Prometheus/VictoriaMetrics 预检失败 {prom_url}: {e}")
    ACTIVE_PROM_URL = ""
    ACTIVE_UP_QUERY = ""
    return ""


def choose_targets_url() -> str:
    global ACTIVE_TARGETS_URL
    for targets_url in candidate_targets_urls():
        if not targets_url:
            continue
        try:
            r = requests.get(targets_url, timeout=10)
            r.raise_for_status()
            data = r.json()
            active_targets = data.get("data", {}).get("activeTargets", [])
            if any((item.get("scrapePool") or "").startswith(TARGET_POOL_PREFIX) for item in active_targets):
                ACTIVE_TARGETS_URL = targets_url
                print(f"[INFO] 使用 vmagent targets 地址: {targets_url}")
                return targets_url
        except Exception as e:
            print(f"[ERROR] vmagent targets 预检失败 {targets_url}: {e}")
    ACTIVE_TARGETS_URL = ""
    return ""


def base_instance(value: str) -> str:
    value = (value or "").strip()
    if ":" in value:
        return value.split(":", 1)[0]
    return value


def load_json(path: str, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def historical_name_map() -> Dict[str, str]:
    history: Dict[str, str] = {}

    devices_meta = load_json(DEVICES_META_JSON, [])
    if isinstance(devices_meta, list):
        for item in devices_meta:
            if not isinstance(item, dict):
                continue
            ip = base_instance(item.get("ip") or item.get("instance") or item.get("snmp_target"))
            node_id = (item.get("id") or "").strip()
            if ip and node_id:
                history[ip] = node_id

    topology = load_json(TOPOLOGY_JSON, {})
    for node in topology.get("nodes", []) if isinstance(topology, dict) else []:
        if not isinstance(node, dict):
            continue
        ip = base_instance(node.get("ip"))
        node_id = (node.get("id") or "").strip()
        if ip and node_id and ip not in history:
            history[ip] = node_id

    return history


def sysname_map() -> Dict[str, str]:
    result = {}
    for item in query_prometheus("sysName"):
        metric = item.get("metric", {})
        ip = base_instance(metric.get("snmp_target") or metric.get("instance"))
        name = (metric.get("sysName") or "").strip()
        if ip and name:
            result[ip] = name
    return result


def choose_device_id(ip: str, metric: Dict, sysnames: Dict[str, str], history: Dict[str, str]) -> str:
    return (
        sysnames.get(ip)
        or history.get(ip)
        or (metric.get("device_name") or "").strip()
        or ip
    )


def build_target_inventory(sysnames: Dict[str, str], history: Dict[str, str]) -> List[Dict]:
    if not ACTIVE_TARGETS_URL:
        return []

    try:
        r = requests.get(ACTIVE_TARGETS_URL, timeout=15)
        r.raise_for_status()
        active_targets = r.json().get("data", {}).get("activeTargets", [])
    except Exception as e:
        print(f"[ERROR] 读取 vmagent targets 失败 {ACTIVE_TARGETS_URL}: {e}")
        return []

    devices_by_ip: Dict[str, Dict] = {}
    for item in active_targets:
        scrape_pool = (item.get("scrapePool") or "").strip()
        if not scrape_pool.startswith(TARGET_POOL_PREFIX):
            continue

        labels = item.get("labels") or {}
        ip = base_instance(labels.get("instance") or labels.get("snmp_target"))
        if not ip:
            scrape_url = item.get("scrapeUrl", "")
            ip = base_instance(urlparse(scrape_url).query.split("target=", 1)[-1] if "target=" in scrape_url else "")
        if not ip:
            continue

        health = (item.get("health") or "").strip().lower()
        current_id = choose_device_id(ip, labels, sysnames, history)
        existing = devices_by_ip.get(ip)
        record = {
            "id": current_id,
            "label": current_id,
            "ip": ip,
            "instance": ip,
            "snmp_target": ip,
            "job": (labels.get("job") or scrape_pool).strip(),
            "device_name": (labels.get("device_name") or "").strip(),
            "device_type": (labels.get("device_type") or "").strip(),
            "project": (labels.get("project") or "").strip(),
            "status": 0 if health == "up" else 1,
            "scrape_pools": [scrape_pool],
        }

        if not existing:
            devices_by_ip[ip] = record
            continue

        existing["status"] = min(existing.get("status", 1), record["status"])
        existing_pools = set(existing.get("scrape_pools") or [])
        existing_pools.add(scrape_pool)
        existing["scrape_pools"] = sorted(existing_pools)

        if not existing.get("id") or existing["id"] == ip:
            existing["id"] = current_id
            existing["label"] = current_id

        for key in ("job", "device_name", "device_type", "project"):
            if existing.get(key) in ("", None) and record.get(key) not in ("", None):
                existing[key] = record[key]

    devices = list(devices_by_ip.values())
    devices.sort(key=lambda item: item["id"])
    return devices


def build_metric_inventory(sysnames: Dict[str, str], history: Dict[str, str]) -> List[Dict]:
    up_query = ACTIVE_UP_QUERY or UP_QUERIES[0]
    items = query_prometheus(up_query)
    devices_by_ip: Dict[str, Dict] = {}

    for item in items:
        metric = item.get("metric", {})
        ip = base_instance(metric.get("snmp_target") or metric.get("instance"))
        if not ip:
            continue

        value = item.get("value", [None, "0"])
        is_up = False
        try:
            is_up = float(value[1]) > 0
        except Exception:
            is_up = False

        current_id = choose_device_id(ip, metric, sysnames, history)
        existing = devices_by_ip.get(ip)
        if existing and existing.get("id") and not current_id:
            current_id = existing["id"]

        record = {
            "id": current_id,
            "label": current_id,
            "ip": ip,
            "instance": base_instance(metric.get("instance")),
            "snmp_target": base_instance(metric.get("snmp_target")),
            "job": metric.get("job", ""),
            "device_name": (metric.get("device_name") or "").strip(),
            "device_type": (metric.get("device_type") or "").strip(),
            "project": (metric.get("project") or "").strip(),
            "status": 0 if is_up else 1,
        }

        if not existing:
            devices_by_ip[ip] = record
            continue

        # 优先保留 sysName / 历史稳定 ID，其次用更完整的非空字段覆盖。
        if history.get(ip):
            record["id"] = history[ip]
            record["label"] = history[ip]
        elif sysnames.get(ip):
            record["id"] = sysnames[ip]
            record["label"] = sysnames[ip]
        else:
            record["id"] = existing.get("id") or record["id"]
            record["label"] = record["id"]

        for key, value in existing.items():
            if record.get(key) in ("", None) and value not in ("", None):
                record[key] = value

        # 同一 IP 若出现多条 up，保留 UP 状态。
        if existing.get("status") == 0:
            record["status"] = 0

        devices_by_ip[ip] = record

    devices = list(devices_by_ip.values())
    devices.sort(key=lambda item: item["id"])
    return devices


def build_device_inventory() -> List[Dict]:
    sysnames = sysname_map() if ACTIVE_PROM_URL else {}
    history = historical_name_map()

    target_devices = build_target_inventory(sysnames, history)
    if target_devices:
        return target_devices

    if not ACTIVE_PROM_URL:
        return []

    return build_metric_inventory(sysnames, history)


def main():
    prom_ready = bool(choose_prom_url())
    targets_ready = bool(choose_targets_url())
    if not prom_ready and not targets_ready:
        print("[ERROR] Prometheus/VictoriaMetrics 与 vmagent targets 当前均不可达，未更新 devices.json")
        return 1

    devices = build_device_inventory()
    if not devices:
        print("[ERROR] 未发现任何网络设备，未更新 devices.json")
        return 1

    names = [item["id"] for item in devices if item.get("id")]
    with open(DEVICES_JSON, "w", encoding="utf-8") as f:
        json.dump(names, f, indent=2, ensure_ascii=False)
    with open(DEVICES_META_JSON, "w", encoding="utf-8") as f:
        json.dump(devices, f, indent=2, ensure_ascii=False)

    down_count = sum(1 for item in devices if item.get("status") != 0)
    print(f"[INFO] 已更新 devices.json: total={len(devices)}, down={down_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
