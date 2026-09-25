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
import re
import time
from typing import Dict, List
import gzip
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.parse import urlparse
import ssl
from atomic_json import write_json_atomic

try:
    import requests  # type: ignore
except Exception:
    requests = None
else:
    try:
        import urllib3  # type: ignore
    except Exception:
        urllib3 = None


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
    'up',
]
ACTIVE_UP_QUERY = ""
TARGET_POOL_PREFIX = "snmp_exporter-"

DEVICES_JSON = "devices.json"
DEVICES_META_JSON = "devices-meta.json"
TOPOLOGY_JSON = "topology.json"
REQUESTS_SESSION = None


def env_bool(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def request_headers() -> Dict[str, str]:
    headers: Dict[str, str] = {"Accept-Encoding": "gzip"}
    token = os.environ.get("PROM_BEARER_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    extra = os.environ.get("PROM_HEADERS_JSON", "").strip()
    if extra:
        try:
            parsed = json.loads(extra)
            if isinstance(parsed, dict):
                for key, value in parsed.items():
                    if key and value is not None:
                        headers[str(key)] = str(value)
        except Exception as exc:
            print(f"[WARNING] 解析 PROM_HEADERS_JSON 失败: {exc}")
    return headers


def request_verify():
    return not env_bool("PROM_SKIP_TLS_VERIFY")


def request_timeout() -> int:
    raw = os.environ.get("PROM_REQUEST_TIMEOUT_SEC", "").strip()
    try:
        value = int(raw)
        if value > 0:
            return value
    except Exception:
        pass
    direct = (os.environ.get("PROM_QUERY_URL", "").strip() or os.environ.get("PROM_URL", "").strip())
    if "/query-gateway/" in direct:
        return 30
    return 10


def request_retries() -> int:
    raw = os.environ.get("PROM_REQUEST_RETRIES", "").strip()
    try:
        value = int(raw)
        if value >= 1:
            return value
    except Exception:
        pass
    return 4 if "/query-gateway/" in (os.environ.get("PROM_QUERY_URL", "").strip() or os.environ.get("PROM_URL", "").strip()) else 3


def request_retry_backoff_sec() -> float:
    raw = os.environ.get("PROM_REQUEST_RETRY_BACKOFF_SEC", "").strip()
    try:
        value = float(raw)
        if value > 0:
            return value
    except Exception:
        pass
    return 1.5 if "/query-gateway/" in (os.environ.get("PROM_QUERY_URL", "").strip() or os.environ.get("PROM_URL", "").strip()) else 1.0


def query_gateway_mode() -> bool:
    direct = (os.environ.get("PROM_QUERY_URL", "").strip() or os.environ.get("PROM_URL", "").strip())
    return "/query-gateway/" in direct


def query_error_text(exc: Exception) -> str:
    detail = str(exc)
    response = getattr(exc, "response", None)
    if response is not None:
        try:
            payload = response.json()
            detail += f"; {payload.get('errorType', '')}: {payload.get('error', '')}"
        except Exception:
            pass
    return detail[:600]


def response_too_large_error(exc: Exception) -> bool:
    text = query_error_text(exc).lower()
    return "exceeds 33554432" in text or "response exceeds" in text


def requests_session():
    global REQUESTS_SESSION
    if requests is None:
        return None
    if REQUESTS_SESSION is None:
        REQUESTS_SESSION = requests.Session()
        adapter = requests.adapters.HTTPAdapter(pool_connections=8, pool_maxsize=16)  # type: ignore[attr-defined]
        REQUESTS_SESSION.mount("http://", adapter)
        REQUESTS_SESSION.mount("https://", adapter)
    return REQUESTS_SESSION


def http_get(url: str, **kwargs):
    headers = {**request_headers(), **(kwargs.pop("headers", {}) or {})}
    if requests is not None:
        if not request_verify() and 'urllib3' in globals() and urllib3 is not None:
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        kwargs.setdefault("timeout", request_timeout())
        session = requests_session()
        return session.get(url, headers=headers, verify=request_verify(), **kwargs)  # type: ignore[union-attr]
    params = kwargs.pop("params", None)
    timeout = kwargs.pop("timeout", request_timeout())
    if params:
        query = urlencode(params)
        joiner = "&" if "?" in url else "?"
        url = f"{url}{joiner}{query}"
    request = Request(url, headers=headers, method="GET")
    context = None
    if not request_verify():
        context = ssl._create_unverified_context()
    with urlopen(request, timeout=timeout, context=context) as response:
        body = response.read()

    class CompatResponse:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            payload = self._payload
            if payload[:2] == b"\x1f\x8b":
                payload = gzip.decompress(payload)
            return json.loads(payload.decode("utf-8"))

    return CompatResponse(body)


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
    if "/query-gateway/" in value:
        return ""
    if value.rstrip("/").endswith("/api/v1/targets"):
        return value

    parsed = urlparse(value)
    if parsed.scheme and parsed.hostname:
        return f"{parsed.scheme}://{parsed.hostname}:8429/api/v1/targets"

    return value.rstrip("/") + "/api/v1/targets"


def candidate_targets_urls():
    direct_query_url = os.environ.get("PROM_QUERY_URL", "").strip() or os.environ.get("PROM_URL", "").strip()
    if "/query-gateway/" in direct_query_url:
        explicit = []
        for key in ("VM_TARGETS_URL", "TARGETS_URL"):
            value = to_targets_url(os.environ.get(key, ""))
            if value:
                explicit.append(value)
        return explicit
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


def query_prometheus_result(query: str):
    urls = [ACTIVE_PROM_URL] if ACTIVE_PROM_URL else candidate_prom_urls()
    retry_count = request_retries()
    for prom_url in urls:
        if not prom_url:
            continue
        last_error = None
        for attempt in range(1, retry_count + 1):
            try:
                r = http_get(prom_url, params={"query": query}, timeout=request_timeout())
                r.raise_for_status()
                data = r.json()
                if data.get("status") != "success" or not isinstance(data.get("data", {}).get("result"), list):
                    raise ValueError(f"监控查询返回无效结果: {data.get('error') or data.get('status')}")
                return data["data"]["result"], True
            except Exception as e:
                last_error = e
                if response_too_large_error(e):
                    print(f"[ERROR] 查询结果超过 query-gateway 响应上限，需拆分查询: {prom_url}: {query_error_text(e)}")
                    break
                if attempt < retry_count:
                    print(f"[WARNING] 查询失败，准备重试 {attempt}/{retry_count - 1}: {prom_url}: {query_error_text(e)}")
                    time.sleep(request_retry_backoff_sec() * attempt)
                else:
                    print(f"[ERROR] Prometheus/VictoriaMetrics 查询失败 {prom_url}: {query_error_text(e)}")
        if last_error is None:
            print(f"[ERROR] Prometheus/VictoriaMetrics 查询失败 {prom_url}: 未知错误")
    return [], False


def query_prometheus(query: str):
    data, _ = query_prometheus_result(query)
    return data


def choose_prom_url() -> str:
    global ACTIVE_PROM_URL, ACTIVE_UP_QUERY
    probe_queries = UP_QUERIES
    if query_gateway_mode():
        # query-gateway has a 32MB response limit. Avoid the final broad `up`
        # probe because it may include unrelated targets across the datasource.
        probe_queries = [query for query in UP_QUERIES if query != "up"]
    for prom_url in candidate_prom_urls():
        for probe_query in probe_queries:
            for attempt in range(1, request_retries() + 1):
                try:
                    r = http_get(prom_url, params={"query": probe_query}, timeout=request_timeout())
                    r.raise_for_status()
                    data = r.json()
                    if data.get("data", {}).get("result", []):
                        ACTIVE_PROM_URL = prom_url
                        ACTIVE_UP_QUERY = probe_query
                        print(f"[INFO] 使用监控查询地址: {prom_url}")
                        print(f"[INFO] 使用设备发现查询: {probe_query}")
                        return prom_url
                    break
                except Exception as e:
                    if response_too_large_error(e):
                        print(f"[ERROR] 监控查询地址预检结果超过 query-gateway 响应上限，跳过该探测查询: {probe_query}: {e}")
                        break
                    if attempt < request_retries():
                        print(f"[WARNING] 监控查询地址预检失败，准备重试 {attempt}/{request_retries() - 1}: {prom_url}: {e}")
                        time.sleep(request_retry_backoff_sec() * attempt)
                    else:
                        print(f"[ERROR] Prometheus/VictoriaMetrics 预检失败 {prom_url}: {e}")
    ACTIVE_PROM_URL = ""
    ACTIVE_UP_QUERY = ""
    return ""


def choose_targets_url() -> str:
    global ACTIVE_TARGETS_URL
    for targets_url in candidate_targets_urls():
        if not targets_url:
            continue
        for attempt in range(1, request_retries() + 1):
            try:
                r = http_get(targets_url, timeout=request_timeout())
                r.raise_for_status()
                data = r.json()
                active_targets = data.get("data", {}).get("activeTargets", [])
                if any((item.get("scrapePool") or "").startswith(TARGET_POOL_PREFIX) for item in active_targets):
                    ACTIVE_TARGETS_URL = targets_url
                    print(f"[INFO] 使用 vmagent targets 地址: {targets_url}")
                    return targets_url
                break
            except Exception as e:
                if attempt < request_retries():
                    print(f"[WARNING] vmagent targets 预检失败，准备重试 {attempt}/{request_retries() - 1}: {targets_url}: {e}")
                    time.sleep(request_retry_backoff_sec() * attempt)
                else:
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
    if query_gateway_mode():
        print("[INFO] query-gateway 模式跳过全量 sysName 查询，改用历史名称与按设备 sysName 查询")
        return {}
    result = {}
    for item in query_prometheus("sysName"):
        metric = item.get("metric", {})
        ip = base_instance(metric.get("snmp_target") or metric.get("instance"))
        name = (metric.get("sysName") or "").strip()
        if ip and name:
            result[ip] = name
    return result


def is_ip_like(value: str) -> bool:
    return bool(re.match(r"^\d{1,3}(?:\.\d{1,3}){3}$", value or ""))


def query_sysname_for_ip(ip: str) -> str:
    if not ip or not ACTIVE_PROM_URL:
        return ""
    for query in (f'sysName{{instance="{ip}"}}', f'sysName{{snmp_target="{ip}"}}'):
        items, ok = query_prometheus_result(query)
        if not ok:
            continue
        for item in items:
            name = (item.get("metric", {}).get("sysName") or "").strip()
            if name:
                return name
    return ""


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
        active_targets = []
        for attempt in range(1, request_retries() + 1):
            try:
                r = http_get(ACTIVE_TARGETS_URL, timeout=max(15, request_timeout()))
                r.raise_for_status()
                active_targets = r.json().get("data", {}).get("activeTargets", [])
                break
            except Exception as e:
                if attempt < request_retries():
                    print(f"[WARNING] 读取 vmagent targets 失败，准备重试 {attempt}/{request_retries() - 1}: {ACTIVE_TARGETS_URL}: {e}")
                    time.sleep(request_retry_backoff_sec() * attempt)
                else:
                    raise
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
        if query_gateway_mode() and ip not in sysnames and ip not in history and not (labels.get("device_name") or "").strip():
            name = query_sysname_for_ip(ip)
            if name:
                sysnames[ip] = name
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

        if query_gateway_mode() and ip not in sysnames and ip not in history and not (metric.get("device_name") or "").strip():
            name = query_sysname_for_ip(ip)
            if name:
                sysnames[ip] = name
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
    write_json_atomic(DEVICES_JSON, names)
    write_json_atomic(DEVICES_META_JSON, devices)

    down_count = sum(1 for item in devices if item.get("status") != 0)
    print(f"[INFO] 已更新 devices.json: total={len(devices)}, down={down_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
