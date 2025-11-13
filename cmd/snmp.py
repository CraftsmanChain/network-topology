#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
网络设备流量采集脚本
- JSON 字段保留 speed_mbps，单位实际是 Gb
- transmit/receive 单位 bits/s
- 百分比数据计算保留三位小数
"""

import json
import requests
import time
import sys

PROM_URL = "http://10.102.10.6:9090/api/v1/query"

def query_prometheus(query: str):
    try:
        r = requests.get(PROM_URL, params={"query": query}, timeout=10)
        r.raise_for_status()
        data = r.json()
        return data.get("data", {}).get("result", [])
    except Exception as e:
        print(f"[ERROR] Prometheus 查询失败: {e}")
        return []

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

# 从 devices.json 读取设备列表
def load_devices_from_json():
    try:
        with open("devices.json", "r", encoding="utf-8") as f:
            devices_list = json.load(f)
        
        # 将设备列表转换为名称到IP的映射
        devices_map = {}
        for device_name in devices_list:
            # 查询设备名称对应的IP地址
            ip = get_device_ip_from_name(device_name)
            if ip:
                devices_map[device_name] = ip
                print(f"[INFO] 找到设备 {device_name} 的IP地址: {ip}")
            else:
                print(f"[WARNING] 无法找到设备 {device_name} 的IP地址")
                devices_map[device_name] = ""  # 保持空字符串
        
        return devices_map
    except FileNotFoundError:
        print("[ERROR] devices.json 文件不存在，程序退出")
        sys.exit(1)
    except Exception as e:
        print(f"[ERROR] 读取 devices.json 失败: {e}")
        sys.exit(1)

# 加载设备列表
DEVICES = load_devices_from_json()


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


def main():
    result = {"nodes": []}

    for name, ip in DEVICES.items():
        if not ip:
            print(f"[WARNING] 跳过设备 {name}，未找到IP地址")
            continue
            
        print(f"[INFO] 正在处理设备: {name} ({ip})")
        
        # 所有设备都使用相同的逻辑处理
        node_status = get_device_status(ip)
        ports = get_interface_data(ip)

        if not ports:
            node_status = 1

        avg_recv_pct = sum(p["receive_percent"] for p in ports) / len(ports) if ports else 0.0
        avg_trans_pct = sum(p["transmit_percent"] for p in ports) / len(ports) if ports else 0.0

        node = {
            "id": name,
            "label": name,
            "ip": ip,
            "model": "Prometheus Device",
            "status": node_status,
            "transmit": sum(p["transmit"] for p in ports),
            "receive": sum(p["receive"] for p in ports),
            "transmit_percent": round(avg_trans_pct, 3),
            "receive_percent": round(avg_recv_pct, 3),
            "ports": ports
        }

        result["nodes"].append(node)

    with open("topology.json", "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    print("[INFO] 数据已写入 topology.json")


if __name__ == "__main__":
    start = time.time()
    main()
    print(f"[INFO] 执行耗时: {time.time() - start:.2f}s")
