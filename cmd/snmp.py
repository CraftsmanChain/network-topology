#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
网络设备流量采集脚本
- JSON 字段保留 speed_mbps，单位实际是 Gb
- transmit/receive 单位 bits/s
- 百分比数据计算保留三位小数
"""

import json
import os
import time
import sys
import concurrent.futures as cf
import re
from typing import Dict, List, Tuple, Set
import gzip
from urllib.parse import urlencode
from urllib.request import Request, urlopen
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
    "http://10.27.3.68:8481/select/0/prometheus/api/v1/query",
    "http://10.102.10.6:9090/api/v1/query",
]
ACTIVE_PROM_URL = ""
DEVICES_META_JSON = "devices-meta.json"
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
    return 4 if query_gateway_mode() else 3


def request_retry_backoff_sec() -> float:
    raw = os.environ.get("PROM_REQUEST_RETRY_BACKOFF_SEC", "").strip()
    try:
        value = float(raw)
        if value > 0:
            return value
    except Exception:
        pass
    return 1.5 if query_gateway_mode() else 1.0


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


def collection_workers() -> int:
    raw = os.environ.get("SNMP_COLLECT_WORKERS", "").strip()
    try:
        value = int(raw)
        if value > 0:
            return value
    except Exception:
        pass
    direct = (os.environ.get("PROM_QUERY_URL", "").strip() or os.environ.get("PROM_URL", "").strip())
    if "/query-gateway/" in direct:
        return 2
    return 4


def query_gateway_mode() -> bool:
    direct = (os.environ.get("PROM_QUERY_URL", "").strip() or os.environ.get("PROM_URL", "").strip())
    return "/query-gateway/" in direct


def should_query_lldp_details() -> bool:
    raw = os.environ.get("PROM_QUERY_LLDP_DETAILS", "").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return True


def requests_session():
    global REQUESTS_SESSION
    if requests is None:
        return None
    if REQUESTS_SESSION is None:
        REQUESTS_SESSION = requests.Session()
        adapter = requests.adapters.HTTPAdapter(pool_connections=16, pool_maxsize=32)  # type: ignore[attr-defined]
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


def query_prometheus_result(query: str) -> Tuple[List[Dict], bool]:
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


def choose_prom_url(probe_instance: str = "") -> str:
    global ACTIVE_PROM_URL
    probe_instance = base_instance(probe_instance)
    probe_queries = ["up"]
    if query_gateway_mode():
        probe_queries = []
        if probe_instance:
            probe_queries.extend([
                f'ifHighSpeed{{instance="{probe_instance}"}}',
                f'up{{instance="{probe_instance}"}}',
                f'up{{snmp_target="{probe_instance}"}}',
            ])
        probe_queries.extend([
            'up{job=~"snmp_.*",device_type="网络设备"}',
            'up{job="snmp_exporter"}',
            'up{job=~"snmp_.*"}',
        ])
    for prom_url in candidate_prom_urls():
        retry_count = request_retries()
        for probe_query in probe_queries:
            for attempt in range(1, retry_count + 1):
                try:
                    r = http_get(prom_url, params={"query": probe_query}, timeout=request_timeout())
                    r.raise_for_status()
                    data = r.json()
                    if data.get("data", {}).get("result", []):
                        ACTIVE_PROM_URL = prom_url
                        print(f"[INFO] 使用监控查询地址: {prom_url}")
                        return prom_url
                    break
                except Exception as e:
                    if response_too_large_error(e):
                        print(f"[ERROR] 监控查询地址预检结果超过 query-gateway 响应上限，跳过该探测查询: {probe_query}: {e}")
                        break
                    if attempt < retry_count:
                        print(f"[WARNING] 监控查询地址预检失败，准备重试 {attempt}/{retry_count - 1}: {prom_url}: {e}")
                        time.sleep(request_retry_backoff_sec() * attempt)
                    else:
                        print(f"[ERROR] Prometheus/VictoriaMetrics 预检失败 {prom_url}: {e}")
    ACTIVE_PROM_URL = ""
    return ""


def prometheus_available(probe_instance: str = "") -> bool:
    return bool(choose_prom_url(probe_instance))


def base_instance(value: str) -> str:
    value = (value or "").strip()
    if ":" in value:
        return value.split(":", 1)[0]
    return value


# 从设备名称查询对应的IP地址
def get_device_ip_from_name(device_name):
    """
    通过查询 Prometheus 的 sysName 指标来获取设备名称对应的IP地址
    """
    # 查询 sysName 指标，找到匹配的设备名称
    query = f'sysName{{sysName="{device_name}"}}'
    data = query_prometheus(query)
    
    if data:
        for result in data:
            instance = result.get("metric", {}).get("instance")
            if instance:
                # 提取IP地址（去除端口号）
                ip = instance.split(":")[0]
                return ip
    
    # 如果通过 sysName 查询不到，尝试查询其他相关指标
    query2 = f'lldpRemSysName{{lldpRemSysName="{device_name}"}}'
    data2 = query_prometheus(query2)
    
    if data2:
        for result in data2:
            instance = result.get("metric", {}).get("instance")
            if instance:
                ip = instance.split(":")[0]
                return ip
    
    # 如果都查询不到，返回 None
    return None

def load_devices_inventory():
    try:
        if os.path.exists(DEVICES_META_JSON):
            with open(DEVICES_META_JSON, "r", encoding="utf-8") as f:
                devices_meta = json.load(f)
            if isinstance(devices_meta, list) and devices_meta:
                devices = []
                for item in devices_meta:
                    if not isinstance(item, dict):
                        continue
                    device_id = (item.get("id") or item.get("label") or "").strip()
                    if not device_id:
                        continue
                    ip = base_instance(item.get("ip") or item.get("instance") or item.get("snmp_target"))
                    devices.append({
                        "id": device_id,
                        "label": (item.get("label") or device_id).strip(),
                        "ip": ip,
                        "status": int(item.get("status", 1 if not ip else 0)),
                        "job": (item.get("job") or "").strip(),
                    })
                if devices:
                    return devices

        with open("devices.json", "r", encoding="utf-8") as f:
            devices_list = json.load(f)

        devices = []
        for device_name in devices_list:
            if not isinstance(device_name, str):
                continue
            device_name = device_name.strip()
            if not device_name:
                continue
            ip = get_device_ip_from_name(device_name)
            if ip:
                print(f"[INFO] 找到设备 {device_name} 的IP地址: {ip}")
            else:
                print(f"[WARNING] 无法找到设备 {device_name} 的IP地址")
            devices.append({
                "id": device_name,
                "label": device_name,
                "ip": ip or "",
                "status": 1 if not ip else 0,
                "job": "",
            })

        return devices
    except FileNotFoundError:
        print("[ERROR] devices.json 文件不存在，程序退出")
        sys.exit(1)
    except Exception as e:
        print(f"[ERROR] 读取 devices.json 失败: {e}")
        sys.exit(1)

def get_interface_data(instance, cached_ports=None):
    """获取端口信息和流量利用率"""
    # 获取端口速率
    speed_query = f'ifHighSpeed{{instance="{instance}"}}'
    speed_data, speed_ok = query_prometheus_result(speed_query)
    oper_query = f'ifOperStatus{{instance="{instance}"}}'
    oper_data, oper_ok = query_prometheus_result(oper_query)

    if not speed_ok or not oper_ok or not speed_data or not oper_data:
        return [], False

    cached_by_index = {str(port.get("ifIndex")): port for port in (cached_ports or [])}

    ports = {}
    for m in speed_data:
        metric = m["metric"]
        ifIndex = metric.get("ifIndex")
        if not ifIndex:
            continue
        try:
            # Prometheus 返回 Mbps
            speed_mbps = float(m["value"][1])
            # JSON 输出单位为 Gb
            speed_mbps = speed_mbps / 1000.0
        except:
            speed_mbps = 0.0

        ports[ifIndex] = {
            "ifIndex": ifIndex,
            "ifName": metric.get("ifName", f"if{ifIndex}"),
            "ifDescr": metric.get("ifDescr", f"if{ifIndex}"),
            "ifAlias": metric.get("ifAlias", ""),
        "status": None,
            "speed_mbps": round(speed_mbps, 3),
            "receive": 0.0,
            "transmit": 0.0,
            "receive_percent": 0.0,
            "transmit_percent": 0.0,
            "util_percent": 0.0
        }

    oper_map = {}
    for item in oper_data:
        metric = item.get("metric", {})
        if_index = metric.get("ifIndex")
        if not if_index:
            continue
        try:
            oper_map[if_index] = int(float(item["value"][1]))
        except Exception:
            continue

    # 获取流量 bits/s
    recv_query = f'sum by (ifIndex) (rate(ifHCInOctets{{instance="{instance}"}}[5m]) * 8)'
    trans_query = f'sum by (ifIndex) (rate(ifHCOutOctets{{instance="{instance}"}}[5m]) * 8)'

    recv_data, recv_ok = query_prometheus_result(recv_query)
    trans_data, trans_ok = query_prometheus_result(trans_query)

    recv_map = {item["metric"]["ifIndex"]: float(item["value"][1]) for item in recv_data}
    trans_map = {item["metric"]["ifIndex"]: float(item["value"][1]) for item in trans_data}

    # 填充流量和计算百分比
    missing_status = 0
    for ifIndex, port in list(ports.items()):
        cached = cached_by_index.get(str(ifIndex), {})
        for field in ("lldp_peer_name", "lldp_peer_port"):
            if cached.get(field):
                port[field] = cached[field]
        receive_bps = recv_map.get(ifIndex, cached.get("receive", 0.0) if not recv_ok else 0.0)
        transmit_bps = trans_map.get(ifIndex, cached.get("transmit", 0.0) if not trans_ok else 0.0)
        port["receive"] = receive_bps
        port["transmit"] = transmit_bps
        oper_status = oper_map.get(ifIndex)
        if oper_status in (1, 2):
            port["status"] = 0 if oper_status == 1 else 1
        elif cached.get("status") in (0, 1):
            port["status"] = cached["status"]
        else:
            del ports[ifIndex]
            missing_status += 1
            continue

        # 端口容量 bits/s
        capacity_bps = port["speed_mbps"] * 1_000_000_000  # speed_mbps 实际是 Gb

        if capacity_bps > 0:
            rx_pct = (receive_bps / capacity_bps) * 100
            tx_pct = (transmit_bps / capacity_bps) * 100
        else:
            rx_pct = tx_pct = 0.0

        port["receive_percent"] = round(rx_pct, 3)
        port["transmit_percent"] = round(tx_pct, 3)
        port["util_percent"] = round(max(rx_pct, tx_pct), 3)

    # 采集 LLDP 邻居详情，保证端口详情能直接展示所有已采到的邻居信息。
    port_by_name = {}
    for ifIndex, port in ports.items():
        for name in (port.get("ifName"), port.get("ifDescr")):
            key = port_key(name)
            if key:
                port_by_name[key] = port

    if missing_status:
        print(f"[WARNING] {instance} 有 {missing_status} 个端口缺少 ifOperStatus 且无历史状态，跳过这些端口")

    if not should_query_lldp_details():
        return list(ports.values()), missing_status == 0

    loc_port_query = f'lldpLocPortId{{instance="{instance}"}}'
    rem_name_query = f'lldpRemSysName{{instance="{instance}"}}'
    rem_desc_query = f'lldpRemPortDesc{{instance="{instance}"}}'
    rem_id_query = f'lldpRemPortId{{instance="{instance}"}}'

    loc_port_data, loc_ok = query_prometheus_result(loc_port_query)
    rem_name_data, rem_name_ok = query_prometheus_result(rem_name_query)
    rem_desc_data, rem_desc_ok = query_prometheus_result(rem_desc_query)
    rem_id_data, rem_id_ok = query_prometheus_result(rem_id_query)

    if not all((loc_ok, rem_name_ok, rem_desc_ok, rem_id_ok)):
        print(f"[WARNING] {instance} LLDP 查询不完整，保留缓存邻居，避免拼接部分查询结果")
        return list(ports.values()), missing_status == 0

    if loc_ok and rem_name_ok:
        for port in ports.values():
            port.pop("lldp_peer_name", None)
            port.pop("lldp_peer_port", None)

    loc_name_by_num = {}
    for item in loc_port_data:
        metric = item.get("metric", {})
        local_num = metric.get("lldpLocPortNum")
        if not local_num:
            continue
        local_name = normalize_port_name(decode_hex_label(metric.get("lldpLocPortId", "")))
        if local_name:
            loc_name_by_num[local_num] = local_name

    rem_name_by_num = {}
    for item in rem_name_data:
        metric = item.get("metric", {})
        local_num = metric.get("lldpRemLocalPortNum")
        peer_name = (metric.get("lldpRemSysName") or "").strip()
        if local_num and peer_name:
            rem_name_by_num[local_num] = peer_name

    rem_desc_by_num = {}
    for item in rem_desc_data:
        metric = item.get("metric", {})
        local_num = metric.get("lldpRemLocalPortNum")
        if not local_num:
            continue
        raw_port = normalize_port_name(decode_hex_label(metric.get("lldpRemPortDesc", "")))
        if raw_port:
            rem_desc_by_num[local_num] = raw_port

    rem_id_by_num = {}
    for item in rem_id_data:
        metric = item.get("metric", {})
        local_num = metric.get("lldpRemLocalPortNum")
        if not local_num:
            continue
        raw_port = normalize_port_name(decode_hex_label(metric.get("lldpRemPortId", "")))
        if raw_port:
            rem_id_by_num[local_num] = raw_port

    for local_num, peer_name in rem_name_by_num.items():
        local_name = loc_name_by_num.get(local_num, "")
        port = port_by_name.get(port_key(local_name))
        if port is None:
            port = ports.get(local_num)
        if port is None:
            continue
        port["lldp_peer_name"] = peer_name
        peer_port = rem_desc_by_num.get(local_num, "")
        peer_port_id = rem_id_by_num.get(local_num, "")
        peer_port = select_lldp_peer_port(peer_port, peer_port_id)
        if not peer_port:
            peer_port = cached_by_index.get(str(port.get("ifIndex")), {}).get("lldp_peer_port", "")
        if peer_port:
            port["lldp_peer_port"] = peer_port

    return list(ports.values()), missing_status == 0


def collect_all_interface_data():
    if query_gateway_mode():
        print("[WARNING] query-gateway 模式禁用全量接口采集，避免超过 32MB 响应限制")
        return {}
    speed_data = query_prometheus("ifHighSpeed{}")
    recv_data = query_prometheus('sum by (instance, ifIndex) (rate(ifHCInOctets[5m]) * 8)')
    trans_data = query_prometheus('sum by (instance, ifIndex) (rate(ifHCOutOctets[5m]) * 8)')
    loc_port_data = query_prometheus("lldpLocPortId{}")
    rem_name_data = query_prometheus("lldpRemSysName{}")
    rem_desc_data = query_prometheus("lldpRemPortDesc{}")
    rem_id_data = query_prometheus("lldpRemPortId{}")

    ports_by_instance = {}
    port_by_name_by_instance = {}

    for item in speed_data:
        metric = item.get("metric", {})
        instance = base_instance(metric.get("instance"))
        if not instance:
            continue
        if_index = metric.get("ifIndex")
        if not if_index:
            continue
        try:
            speed_mbps = float(item["value"][1]) / 1000.0
        except Exception:
            speed_mbps = 0.0

        instance_ports = ports_by_instance.setdefault(instance, {})
        port = instance_ports.setdefault(if_index, {
            "ifIndex": if_index,
            "ifName": metric.get("ifName", f"if{if_index}"),
            "ifDescr": metric.get("ifDescr", f"if{if_index}"),
            "ifAlias": metric.get("ifAlias", ""),
            "status": 1,
            "speed_mbps": 0.0,
            "receive": 0.0,
            "transmit": 0.0,
            "receive_percent": 0.0,
            "transmit_percent": 0.0,
            "util_percent": 0.0,
        })
        port["ifName"] = metric.get("ifName", port["ifName"])
        port["ifDescr"] = metric.get("ifDescr", port["ifDescr"])
        port["ifAlias"] = metric.get("ifAlias", port.get("ifAlias", ""))
        port["speed_mbps"] = round(speed_mbps, 3)

        for name in (port.get("ifName"), port.get("ifDescr")):
            key = port_key(name)
            if key:
                port_by_name_by_instance.setdefault(instance, {})[key] = port

    for item in recv_data:
        metric = item.get("metric", {})
        instance = base_instance(metric.get("instance"))
        if_index = metric.get("ifIndex")
        if not instance or not if_index:
            continue
        port = ports_by_instance.get(instance, {}).get(if_index)
        if port is None:
            continue
        try:
            port["receive"] = float(item["value"][1])
        except Exception:
            port["receive"] = 0.0

    for item in trans_data:
        metric = item.get("metric", {})
        instance = base_instance(metric.get("instance"))
        if_index = metric.get("ifIndex")
        if not instance or not if_index:
            continue
        port = ports_by_instance.get(instance, {}).get(if_index)
        if port is None:
            continue
        try:
            port["transmit"] = float(item["value"][1])
        except Exception:
            port["transmit"] = 0.0

    for instance_ports in ports_by_instance.values():
        for port in instance_ports.values():
            receive_bps = port.get("receive", 0.0)
            transmit_bps = port.get("transmit", 0.0)
            port["status"] = 0 if (receive_bps > 0 or transmit_bps > 0) else 1
            capacity_bps = port["speed_mbps"] * 1_000_000_000
            if capacity_bps > 0:
                rx_pct = (receive_bps / capacity_bps) * 100
                tx_pct = (transmit_bps / capacity_bps) * 100
            else:
                rx_pct = tx_pct = 0.0
            port["receive_percent"] = round(rx_pct, 3)
            port["transmit_percent"] = round(tx_pct, 3)
            port["util_percent"] = round(max(rx_pct, tx_pct), 3)

    loc_name_by_num = {}
    for item in loc_port_data:
        metric = item.get("metric", {})
        instance = base_instance(metric.get("instance"))
        local_num = metric.get("lldpLocPortNum")
        if not instance or not local_num:
            continue
        local_name = normalize_port_name(decode_hex_label(metric.get("lldpLocPortId", "")))
        if local_name:
            loc_name_by_num.setdefault(instance, {})[local_num] = local_name

    rem_name_by_num = {}
    for item in rem_name_data:
        metric = item.get("metric", {})
        instance = base_instance(metric.get("instance"))
        local_num = metric.get("lldpRemLocalPortNum")
        peer_name = (metric.get("lldpRemSysName") or "").strip()
        if instance and local_num and peer_name:
            rem_name_by_num.setdefault(instance, {})[local_num] = peer_name

    rem_desc_by_num = {}
    for item in rem_desc_data:
        metric = item.get("metric", {})
        instance = base_instance(metric.get("instance"))
        local_num = metric.get("lldpRemLocalPortNum")
        if not instance or not local_num:
            continue
        raw_port = normalize_port_name(decode_hex_label(metric.get("lldpRemPortDesc", "")))
        if raw_port:
            rem_desc_by_num.setdefault(instance, {})[local_num] = raw_port

    rem_id_by_num = {}
    for item in rem_id_data:
        metric = item.get("metric", {})
        instance = base_instance(metric.get("instance"))
        local_num = metric.get("lldpRemLocalPortNum")
        if not instance or not local_num:
            continue
        raw_port = normalize_port_name(decode_hex_label(metric.get("lldpRemPortId", "")))
        if raw_port:
            rem_id_by_num.setdefault(instance, {})[local_num] = raw_port

    for instance, peers in rem_name_by_num.items():
        instance_ports = ports_by_instance.get(instance, {})
        if not instance_ports:
            continue
        name_index = port_by_name_by_instance.get(instance, {})
        for local_num, peer_name in peers.items():
            local_name = (loc_name_by_num.get(instance) or {}).get(local_num, "")
            port = name_index.get(port_key(local_name))
            if port is None:
                port = instance_ports.get(local_num)
            if port is None:
                continue
            port["lldp_peer_name"] = peer_name
            peer_port = (rem_desc_by_num.get(instance) or {}).get(local_num, "")
            peer_port_id = (rem_id_by_num.get(instance) or {}).get(local_num, "")
            peer_port = select_lldp_peer_port(peer_port, peer_port_id)
            if peer_port:
                port["lldp_peer_port"] = peer_port

    return {
        instance: list(instance_ports.values())
        for instance, instance_ports in ports_by_instance.items()
    }


def normalize_port_name(name: str) -> str:
    name = (name or "").strip()
    if name.lower().endswith("interface"):
        name = name[:-len("interface")].strip()
    numeric_speed_match = re.match(r"^(400GE|100GE|40GE|25GE|10GE)\s*(\d+/\d+(?:/\d+)?)$", name, re.I)
    if numeric_speed_match:
        prefix = numeric_speed_match.group(1).lower()
        suffix = numeric_speed_match.group(2)
        replacements = {
            "400ge": "FourHundredGigE",
            "100ge": "HundredGigE",
            "40ge": "FortyGigabitEthernet",
            "25ge": "TwentyFiveGigE",
            "10ge": "Ten-GigabitEthernet",
        }
        return f"{replacements.get(prefix, numeric_speed_match.group(1))}{suffix}"
    abbrev_match = re.match(r"^(FH|Fo|Te|GE|XGE|Eth)(\d+/\d+(?:/\d+)?)$", name, re.I)
    if abbrev_match:
        prefix = abbrev_match.group(1).lower()
        suffix = abbrev_match.group(2)
        replacements = {
            "fh": "FHGigabitEthernet",
            "fo": "FortyGigabitEthernet",
            "te": "TenGigabitEthernet",
            "ge": "GigabitEthernet",
            "xge": "XGigabitEthernet",
            "eth": "Ethernet",
        }
        return f"{replacements.get(prefix, abbrev_match.group(1))}{suffix}"
    compact = "".join(name.lower().split())
    if compact == "ipmanagementconsole":
        return "Mgmt 0"
    if name.lower() == "eth0":
        return "Ethernet0"
    return name


def port_key(name: str) -> str:
    return "".join((name or "").strip().lower().split())


def normalize_node_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (name or "").strip().lower())


def has_alias_context(value: str) -> bool:
    return bool(re.search(r"(group\d+|nxdx|core|oob|mgmt|gmgt|management)", value or "", re.I))


def is_management_port_name(name: str) -> bool:
    compact = port_key(normalize_port_name(name))
    if not compact:
        return False
    return (
        compact.startswith("meth")
        or compact.startswith("m-gigabitethernet")
        or compact.startswith("mgmt")
        or compact == "eth0"
        or compact == "ethernet0"
        or compact == "managementport"
        or compact == "ipmanagementconsole"
        or compact.startswith("console")
    )


def is_management_node_name(name: str) -> bool:
    compact = port_key(name)
    if not compact:
        return False
    return any(token in compact for token in ("oob", "mgmt", "gmgt", "management"))


def is_non_graph_port_name(name: str) -> bool:
    normalized = normalize_port_name(name)
    compact = port_key(normalized)
    if not compact:
        return True
    if compact.startswith("link-to-") or compact.startswith("linkto"):
        return True
    if compact.startswith(("loopback", "lo", "null", "nu", "vlan", "vl", "tunnel", "register-tunnel", "virtual-if")):
        return True
    if "duidie" in compact or "xintiao" in compact or "heartbeat" in compact:
        return True
    hex_groups = compact.split(":")
    if len(hex_groups) == 6 and all(len(part) == 2 and all(ch in "0123456789abcdef" for ch in part) for part in hex_groups):
        return True
    return False


def looks_like_port_name(name: str) -> bool:
    compact = port_key(normalize_port_name(name))
    if not compact:
        return False
    prefixes = (
        "fourhundredgige",
        "fhgigabitethernet",
        "hundredgige",
        "hundredgigabitethernet",
        "fiftygige",
        "fiftygigabitethernet",
        "fortygige",
        "fortygigabitethernet",
        "twentyfivegige",
        "twentyfivegigabitethernet",
        "tengige",
        "tengigabitethernet",
        "ten-gigabitethernet",
        "xgigabitethernet",
        "gigabitethernet",
        "ethernet",
        "eth-trunk",
        "port-channel",
        "bundle-ether",
        "mgmt",
        "m-gigabitethernet",
        "meth",
        "console",
    )
    return compact.startswith(prefixes)


def decode_hex_label(value: str) -> str:
    text = (value or "").strip()
    if not text.lower().startswith("0x"):
        return text
    payload = text[2:]
    if len(payload) % 2 != 0:
        return text
    try:
        return bytes.fromhex(payload).decode("utf-8", errors="ignore").strip("\x00").strip()
    except Exception:
        return text


def alias_name_keys(value: str) -> Set[str]:
    raw = (value or "").strip()
    if not raw:
        return set()
    keys = {normalize_node_name(raw)}
    parts = [part for part in re.split(r"[_\-]+", raw) if part]
    for width in (2, 3):
        if len(parts) >= width:
            suffix = "-".join(parts[-width:])
            if has_alias_context(suffix):
                keys.add(normalize_node_name(suffix))
    if "_" in raw:
        suffix = raw.rsplit("_", 1)[-1]
        if has_alias_context(suffix):
            keys.add(normalize_node_name(suffix))
    return {item for item in keys if item}


def build_node_name_lookup(nodes: List[Dict]) -> Dict[str, Set[str]]:
    lookup: Dict[str, Set[str]] = {}
    for node in nodes:
        node_id = str(node.get("id") or "").strip()
        if not node_id:
            continue
        for candidate in (node_id, node.get("label") or ""):
            for key in alias_name_keys(str(candidate)):
                lookup.setdefault(key, set()).add(node_id)
    return lookup


def resolve_alias_target_name(value: str, known_nodes: Set[str], lookup: Dict[str, Set[str]]) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    if raw in known_nodes:
        return raw
    for key in alias_name_keys(raw):
        matched = lookup.get(key, set())
        if len(matched) == 1:
            return next(iter(matched))
    return ""


def alias_candidate_score(target: str, target_port: str, known_nodes: Set[str], lookup: Dict[str, Set[str]]) -> Tuple[int, int, int]:
    score = 0
    if resolve_alias_target_name(target, known_nodes, lookup):
        score += 100
    if is_non_graph_port_name(target_port):
        score -= 100
    if re.search(r"[_-](nxdx|group|pod|core|spine|leaf|agg|tor|oob)", target_port, re.I):
        score -= 20
    return score, -len(target_port), -len(target)


def parse_link_alias(alias: str, known_nodes: Set[str] = None, lookup: Dict[str, Set[str]] = None) -> Tuple[str, str]:
    alias = (alias or "").strip()
    prefix = "Link-to-"
    if not alias.startswith(prefix):
        return "", ""
    known_nodes = known_nodes or set()
    lookup = lookup or {}

    payload = decode_hex_label(alias[len(prefix):].strip())
    if not payload:
        return "", ""

    candidates: List[Tuple[str, str]] = []

    if "_" in payload:
        left, right = payload.rsplit("_", 1)
        if looks_like_port_name(right):
            candidates.append((left, right))
        if looks_like_port_name(left):
            candidates.append((right, left))

    dash_parts = [part for part in payload.split("-") if part]
    if len(dash_parts) > 1:
        for idx in range(1, len(dash_parts)):
            left = "-".join(dash_parts[:idx])
            right = "-".join(dash_parts[idx:])
            if looks_like_port_name(right):
                candidates.append((left, right))
            if looks_like_port_name(left):
                candidates.append((right, left))

    deduped = []
    seen_candidates = set()
    for target, target_port in candidates:
        target = target.strip()
        target_port = normalize_port_name(target_port)
        if target and target_port and looks_like_port_name(target_port):
            key = (target, target_port)
            if key not in seen_candidates:
                seen_candidates.add(key)
                deduped.append(key)
    deduped.sort(key=lambda item: alias_candidate_score(item[0], item[1], known_nodes, lookup), reverse=True)
    if deduped:
        return deduped[0]
    return "", ""


def link_key(source: str, source_port: str, target: str, target_port: str) -> str:
    left = f"{source}\x00{port_key(source_port)}"
    right = f"{target}\x00{port_key(target_port)}"
    if left <= right:
        return f"{left}\x01{right}"
    return f"{right}\x01{left}"


def interface_key(name: str) -> str:
    # Expand vendor abbreviations without changing slot/breakout numbering.
    return port_key(normalize_port_name(name))


def select_lldp_peer_port(description: str, port_id: str) -> str:
    # PortDesc can be arbitrary or repeated text; a named PortId is authoritative.
    if port_id and (looks_like_port_name(port_id) or not description or description.startswith("Link-to-")):
        return port_id
    return description


def interface_name(port: Dict) -> str:
    return str(port.get("ifDescr") or port.get("ifName") or f"if{port.get('ifIndex', '')}").strip()


def build_links_from_topology(topology: Dict, port_aliases: Dict = None) -> Tuple[List[Dict], List[Dict]]:
    nodes = topology.get("nodes", []) if isinstance(topology, dict) else []
    known_nodes = {node.get("id", "") for node in nodes if node.get("id")}
    node_lookup = build_node_name_lookup(nodes)
    raw_links = []
    filtered_links = []
    seen = set()
    inventory = {}
    descriptions = {}
    reciprocal = {}
    peers = {}
    for node in nodes:
        node_id = node.get("id", "")
        index = inventory.setdefault(node_id, {})
        for port in node.get("ports", []):
            alias_key = interface_key(port.get("ifAlias"))
            if alias_key:
                descriptions.setdefault(node_id, {}).setdefault(alias_key, []).append(port)
            for name in (port.get("ifName"), port.get("ifDescr")):
                key = interface_key(name)
                if key:
                    candidates = index.setdefault(key, [])
                    if not any(candidate is port for candidate in candidates):
                        candidates.append(port)
            target = (port.get("lldp_peer_name") or "").strip()
            target_port = normalize_port_name(port.get("lldp_peer_port") or "")
            if not target or not target_port:
                target, target_port = parse_link_alias(port.get("ifAlias", ""), known_nodes, node_lookup)
            target = resolve_alias_target_name(target, known_nodes, node_lookup) or target
            peers[id(port)] = (target, target_port)
            if target and target_port:
                reciprocal.setdefault((node_id, target, interface_key(target_port)), []).append(port)

    def unique_port(node_id, name):
        candidates = inventory.get(node_id, {}).get(interface_key(name), [])
        return candidates[0] if len(candidates) == 1 else None

    def resolve_port(source, source_port, target, reported):
        match = unique_port(target, reported)
        if match is not None:
            return interface_name(match), "inventory"
        aliases = (port_aliases or {}).get(target, {})
        for alias, actual in aliases.items():
            if interface_key(alias) == interface_key(reported):
                match = unique_port(target, actual)
                if match is not None:
                    return interface_name(match), "configured-alias"
                return reported, "unresolved"
        # A reverse reference must identify this exact source interface, uniquely.
        candidates = []
        for name in (source_port.get("ifName"), source_port.get("ifDescr")):
            for candidate in reciprocal.get((target, source, interface_key(name)), []):
                if not any(existing is candidate for existing in candidates):
                    candidates.append(candidate)
        if len(candidates) == 1:
            return interface_name(candidates[0]), "reciprocal"
        candidates = descriptions.get(target, {}).get(interface_key(reported), [])
        if len(candidates) == 1:
            return interface_name(candidates[0]), "unique-description"
        return reported, "unresolved"

    for node in nodes:
        source = node.get("id", "")
        if not source:
            continue
        for port in node.get("ports", []):
            target, target_port = peers[id(port)]
            if not target or not target_port:
                continue
            resolved_target = resolve_alias_target_name(target, known_nodes, node_lookup) or target

            source_port = interface_name(port)
            if not source_port:
                continue
            reported_port = target_port
            target_port, resolution = resolve_port(source, port, resolved_target, reported_port)

            key = link_key(source, source_port, resolved_target, target_port)
            if key in seen:
                continue
            seen.add(key)

            link = {
                "source": source,
                "sourcePort": source_port,
                "target": resolved_target,
                "targetPort": target_port
            }
            if reported_port != target_port:
                link["targetPortReported"] = reported_port
                link["targetPortResolution"] = resolution
            raw_links.append(link)
            if (
                resolved_target in known_nodes
                and not is_non_graph_port_name(source_port)
                and not is_non_graph_port_name(target_port)
            ):
                filtered_links.append(link)

    return raw_links, filtered_links


def write_links_from_topology(
    topology: Dict,
    raw_path: str = "links-alias-raw.json",
    filtered_path: str = "links-alias.json",
) -> bool:
    config_dir = os.environ.get("CONFIG_DIR") or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config")
    aliases_path = os.path.join(config_dir, "port_aliases.json")
    port_aliases = {}
    if os.path.exists(aliases_path):
        with open(aliases_path, encoding="utf-8") as handle:
            port_aliases = json.load(handle).get("aliases", {})
    raw_links, filtered_links = build_links_from_topology(topology, port_aliases)
    if not raw_links:
        print(f"[WARNING] 未从 topology.json 的端口别名解析到链路，未更新 {raw_path} 与 {filtered_path}")
        return False

    write_json_atomic(raw_path, raw_links)

    if filtered_links:
        write_json_atomic(filtered_path, filtered_links)
    else:
        print(f"[WARNING] 端口别名链路过滤后为空，未更新 {filtered_path}")

    print(f"[INFO] 已基于 topology.json 端口别名更新链路到 {raw_path}/{filtered_path}: raw={len(raw_links)}, filtered={len(filtered_links)}")
    return True


def load_topology_json(path="topology.json") -> Dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_existing_nodes(path="topology.json") -> Dict[str, Dict]:
    if not os.path.exists(path):
        return {}
    try:
        payload = load_topology_json(path)
    except Exception:
        return {}
    return {
        str(node.get("id") or "").strip(): node
        for node in payload.get("nodes", []) if isinstance(node, dict) and str(node.get("id") or "").strip()
    }


def build_node(device: Dict, ports: List[Dict], status: int) -> Dict:
    device_id = device.get("id", "")
    return {
        "id": device_id,
        "label": device.get("label") or device_id,
        "ip": device.get("ip", ""),
        "model": "Prometheus Device",
        "status": status,
        "transmit": sum(p["transmit"] for p in ports),
        "receive": sum(p["receive"] for p in ports),
        "transmit_percent": round(sum(p["transmit_percent"] for p in ports) / len(ports), 3) if ports else 0.0,
        "receive_percent": round(sum(p["receive_percent"] for p in ports) / len(ports), 3) if ports else 0.0,
        "ports": ports,
    }


def build_cached_node(device: Dict, cached_node: Dict) -> Dict:
    node = dict(cached_node or {})
    device_id = device.get("id", "")
    node["id"] = device_id
    node["label"] = device.get("label") or node.get("label") or device_id
    node["ip"] = device.get("ip", "") or node.get("ip", "")
    if device.get("status") in (0, 1):
        node["status"] = device["status"]
    node.setdefault("model", "Prometheus Device")
    node["collection_stale"] = True
    return node


def write_topology(result: Dict):
    write_json_atomic("topology.json", result)
    print("[INFO] 数据已写入 topology.json")


def collect_device_node(device: Dict, cached_nodes: Dict[str, Dict]) -> Dict:
    name = device["id"]
    ip = device.get("ip", "")
    base_status = int(device.get("status", 1 if not ip else 0))
    if not ip:
        print(f"[WARNING] 设备 {name} 未找到IP地址，标记为 DOWN")
        cached_node = cached_nodes.get(name)
        if cached_node and cached_node.get("ports"):
            print(f"[WARNING] 设备 {name} 本轮无 IP，沿用上一版 topology.json 快照")
            return build_cached_node(device, cached_node)
        node = build_node(device, [], base_status)
        node["collection_stale"] = True
        return node

    print(f"[INFO] 正在处理设备: {name} ({ip})")
    cached_node = cached_nodes.get(name)
    ports, ports_complete = get_interface_data(ip, (cached_node or {}).get("ports", []))
    if (not ports or not ports_complete) and cached_node and cached_node.get("ports"):
        print(f"[WARNING] 设备 {name} 本轮端口采集不完整，沿用上一版 topology.json 快照")
        return build_cached_node(device, cached_node)

    node = build_node(device, ports, base_status)
    if not ports_complete:
        node["collection_stale"] = True
    return node


def main():
    devices = load_devices_inventory()
    cached_nodes = load_existing_nodes()
    probe_instance = ""
    for device in devices:
        probe_instance = device.get("ip", "")
        if probe_instance:
            break
    if not prometheus_available(probe_instance):
        print("[ERROR] 监控查询不可用，保留上一版 topology.json")
        return 1

    result = {"nodes": []}
    workers = collection_workers()
    if workers <= 1:
        result["nodes"] = [collect_device_node(device, cached_nodes) for device in devices]
    else:
        with cf.ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(collect_device_node, device, cached_nodes) for device in devices]
            for future in futures:
                try:
                    result["nodes"].append(future.result())
                except Exception as exc:
                    print(f"[ERROR] 设备采集任务异常: {exc}")

    if not result["nodes"]:
        print("[WARNING] 未采集到任何设备数据，未更新 topology.json")
        return 1

    stale_count = sum(bool(node.get("collection_stale")) for node in result["nodes"])
    if stale_count == len(result["nodes"]):
        print("[ERROR] 所有设备数据均未采集成功，保留上一版 topology.json")
        return 1
    if stale_count:
        print(f"[WARNING] {stale_count}/{len(result['nodes'])} 台设备沿用历史状态")

    write_topology(result)
    return 0


if __name__ == "__main__":
    start = time.time()
    if "--links-from-topology" in sys.argv:
        write_links_from_topology(load_topology_json())
    else:
        exit_code = main()
    print(f"[INFO] 执行耗时: {time.time() - start:.2f}s")
    if "--links-from-topology" not in sys.argv:
        raise SystemExit(exit_code)
