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
import requests
import time
import sys
from typing import Dict, List, Tuple

DEFAULT_PROM_URLS = [
    "http://10.27.3.68:8481/select/0/prometheus/api/v1/query",
    "http://10.102.10.6:9090/api/v1/query",
]
ACTIVE_PROM_URL = ""
DEVICES_META_JSON = "devices-meta.json"


def candidate_prom_urls():
    for key in ("PROM_QUERY_URL", "PROM_URL"):
        value = os.environ.get(key, "").strip()
        if value:
            if not value.rstrip("/").endswith("/api/v1/query"):
                value = value.rstrip("/") + "/api/v1/query"
            return [value]
    return DEFAULT_PROM_URLS


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
    global ACTIVE_PROM_URL
    for prom_url in candidate_prom_urls():
        try:
            r = requests.get(prom_url, params={"query": "up"}, timeout=10)
            r.raise_for_status()
            data = r.json()
            if data.get("data", {}).get("result", []):
                ACTIVE_PROM_URL = prom_url
                print(f"[INFO] 使用监控查询地址: {prom_url}")
                return prom_url
        except Exception as e:
            print(f"[ERROR] Prometheus/VictoriaMetrics 预检失败 {prom_url}: {e}")
    ACTIVE_PROM_URL = ""
    return ""


def prometheus_available() -> bool:
    return bool(choose_prom_url())


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

def get_device_status(instance):
    """如果设备完全无数据则 status=1"""
    query = f'ifOperStatus{{instance="{instance}"}}'
    data = query_prometheus(query)
    return 1 if not data else 0


def get_interface_data(instance):
    """获取端口信息和流量利用率"""
    # 获取端口速率
    speed_query = f'ifHighSpeed{{instance="{instance}"}}'
    speed_data = query_prometheus(speed_query)

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
            "status": 1,
            "speed_mbps": round(speed_mbps, 3),
            "receive": 0.0,
            "transmit": 0.0,
            "receive_percent": 0.0,
            "transmit_percent": 0.0,
            "util_percent": 0.0
        }

    # 获取流量 bits/s
    recv_query = f'sum by (ifIndex) (rate(ifHCInOctets{{instance="{instance}"}}[5m]) * 8)'
    trans_query = f'sum by (ifIndex) (rate(ifHCOutOctets{{instance="{instance}"}}[5m]) * 8)'

    recv_data = query_prometheus(recv_query)
    trans_data = query_prometheus(trans_query)

    recv_map = {item["metric"]["ifIndex"]: float(item["value"][1]) for item in recv_data}
    trans_map = {item["metric"]["ifIndex"]: float(item["value"][1]) for item in trans_data}

    # 填充流量和计算百分比
    for ifIndex, port in ports.items():
        receive_bps = recv_map.get(ifIndex, 0.0)
        transmit_bps = trans_map.get(ifIndex, 0.0)
        port["receive"] = receive_bps
        port["transmit"] = transmit_bps
        port["status"] = 0 if (receive_bps > 0 or transmit_bps > 0) else 1

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

    return list(ports.values())


def normalize_port_name(name: str) -> str:
    name = (name or "").strip()
    if name.lower() == "eth0":
        return "Ethernet0"
    return name


def parse_link_alias(alias: str) -> Tuple[str, str]:
    """
    解析端口别名中的链路信息。
    典型格式：Link-to-<对端设备>_<对端端口>
    设备名可能包含下划线，因此从右侧切分最后一个下划线。
    """
    alias = (alias or "").strip()
    prefix = "Link-to-"
    if not alias.startswith(prefix):
        return "", ""

    payload = alias[len(prefix):].strip()
    if "_" not in payload:
        return "", ""

    target, target_port = payload.rsplit("_", 1)
    return target.strip(), normalize_port_name(target_port)


def link_key(source: str, source_port: str, target: str, target_port: str) -> str:
    left = f"{source}\x00{source_port}"
    right = f"{target}\x00{target_port}"
    if left <= right:
        return f"{left}\x01{right}"
    return f"{right}\x01{left}"


def build_links_from_topology(topology: Dict) -> Tuple[List[Dict], List[Dict]]:
    nodes = topology.get("nodes", []) if isinstance(topology, dict) else []
    known_devices = {node.get("id") for node in nodes if node.get("id")}
    raw_links = []
    filtered_links = []
    seen = set()

    for node in nodes:
        source = node.get("id", "")
        if not source:
            continue
        for port in node.get("ports", []):
            target, target_port = parse_link_alias(port.get("ifAlias", ""))
            if not target or not target_port:
                continue

            source_port = normalize_port_name(port.get("ifDescr") or port.get("ifName") or f"if{port.get('ifIndex', '')}")
            if not source_port:
                continue

            key = link_key(source, source_port, target, target_port)
            if key in seen:
                continue
            seen.add(key)

            link = {
                "source": source,
                "sourcePort": source_port,
                "target": target,
                "targetPort": target_port
            }
            raw_links.append(link)
            if target in known_devices:
                filtered_links.append(link)

    return raw_links, filtered_links


def write_links_from_topology(
    topology: Dict,
    raw_path: str = "links-alias-raw.json",
    filtered_path: str = "links-alias.json",
) -> bool:
    raw_links, filtered_links = build_links_from_topology(topology)
    if not raw_links:
        print(f"[WARNING] 未从 topology.json 的端口别名解析到链路，未更新 {raw_path} 与 {filtered_path}")
        return False

    with open(raw_path, "w", encoding="utf-8") as f:
        json.dump(raw_links, f, indent=2, ensure_ascii=False)

    if filtered_links:
        with open(filtered_path, "w", encoding="utf-8") as f:
            json.dump(filtered_links, f, indent=2, ensure_ascii=False)
    else:
        print(f"[WARNING] 端口别名链路过滤后为空，未更新 {filtered_path}")

    print(f"[INFO] 已基于 topology.json 端口别名更新链路到 {raw_path}/{filtered_path}: raw={len(raw_links)}, filtered={len(filtered_links)}")
    return True


def load_topology_json(path="topology.json") -> Dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


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


def write_topology(result: Dict):
    with open("topology.json", "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    print("[INFO] 数据已写入 topology.json")


def main():
    devices = load_devices_inventory()
    if not prometheus_available():
        print("[WARNING] Prometheus 当前不可达或无 up 指标，按 devices.json 基线将节点标记为 DOWN")
        result = {"nodes": [build_node(device, [], 1) for device in devices]}
        if result["nodes"]:
            write_topology(result)
        return

    result = {"nodes": []}
    for device in devices:
        name = device["id"]
        ip = device.get("ip", "")
        base_status = int(device.get("status", 1 if not ip else 0))
        if not ip:
            print(f"[WARNING] 设备 {name} 未找到IP地址，标记为 DOWN")
            result["nodes"].append(build_node(device, [], 1))
            continue

        print(f"[INFO] 正在处理设备: {name} ({ip})")
        runtime_status = get_device_status(ip)
        ports = get_interface_data(ip)
        node_status = 1 if base_status != 0 or runtime_status != 0 or not ports else 0
        result["nodes"].append(build_node(device, ports, node_status))

    if not result["nodes"]:
        print("[WARNING] 未采集到任何设备数据，未更新 topology.json")
        return

    write_topology(result)


if __name__ == "__main__":
    start = time.time()
    if "--links-from-topology" in sys.argv:
        write_links_from_topology(load_topology_json())
    else:
        main()
    print(f"[INFO] 执行耗时: {time.time() - start:.2f}s")
