#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import gzip
import json
import ssl
import sys
from typing import Dict, List, Optional, Tuple
import urllib.parse
import urllib.request


REQUIRED_METRICS = {
    "devices": [
        "up",
        'up{job=~"snmp.*"}',
        "sysName",
    ],
    "topology": [
        "ifHighSpeed",
        "ifOperStatus",
        "ifHCInOctets",
        "ifHCOutOctets",
        "ifAlias",
    ],
    "lldp": [
        "lldpLocPortId",
        "lldpRemSysName",
        "lldpRemPortId",
    ],
}


def build_query_url(base_url: str, query: str) -> str:
    normalized = base_url.rstrip("/")
    if normalized.endswith("/api/v1/query"):
        query_base = normalized
    elif normalized.endswith("/api/v1"):
        query_base = normalized + "/query"
    elif normalized.endswith("/prometheus"):
        # VictoriaMetrics cluster style, e.g. /select/0/prometheus
        query_base = normalized + "/api/v1/query"
    else:
        # Prometheus root or query-gateway datasource base URL
        query_base = normalized + "/api/v1/query"
    return query_base + "?" + urllib.parse.urlencode({"query": query})


def fetch_json(url: str, token: str, query: str, insecure: bool) -> dict:
    full = build_query_url(url, query)
    headers = {"Accept-Encoding": "gzip"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(full, headers=headers, method="GET")
    context = ssl._create_unverified_context() if insecure else None
    with urllib.request.urlopen(request, context=context, timeout=30) as response:
        body = response.read()
    if body[:2] == b"\x1f\x8b":
        body = gzip.decompress(body)
    return json.loads(body.decode("utf-8"))


def query_count(url: str, token: str, expr: str, insecure: bool) -> Tuple[Optional[int], List[Dict], Optional[str]]:
    try:
        result = fetch_json(url, token, expr, insecure).get("data", {}).get("result", [])
        samples = [item.get("metric", {}) for item in result[:5]]
        if expr.startswith("count("):
            if not result:
                return 0, samples, None
            try:
                return int(float(result[0]["value"][1])), samples, None
            except Exception:
                return None, samples, None
        return len(result), samples, None
    except Exception as exc:
        return None, [], str(exc)


def main() -> int:
    parser = argparse.ArgumentParser(description="test required topology metrics from a Prometheus-compatible query API")
    parser.add_argument(
        "--base-url",
        required=True,
        help="query base URL: query-gateway datasource base, Prometheus root/api path, or VictoriaMetrics /select/.../prometheus path",
    )
    parser.add_argument("--token", default="", help="bearer token, optional for direct local Prometheus/VM query")
    parser.add_argument("--name", default="env", help="environment name for display")
    parser.add_argument("--insecure", action="store_true", help="skip TLS verification")
    parser.add_argument("--json", action="store_true", help="print JSON report")
    args = parser.parse_args()

    report = {
        "env": args.name,
        "base_url": args.base_url,
        "stages": {},
        "missing_metrics": [],
        "failed_metrics": [],
        "errors": [],
    }

    for stage, metrics in REQUIRED_METRICS.items():
        stage_rows = []
        for expr in metrics:
            probe = f"count({expr})" if not expr.startswith("count(") else expr
            count, samples, error = query_count(args.base_url, args.token, probe, args.insecure)
            row = {
                "expr": expr,
                "probe": probe,
                "count": count,
                "ok": bool(count and count > 0),
                "samples": samples,
                "error": error,
            }
            stage_rows.append(row)
            if error:
                report["failed_metrics"].append(expr)
                report["errors"].append({"expr": expr, "error": error})
            elif not row["ok"]:
                report["missing_metrics"].append(expr)
        report["stages"][stage] = stage_rows

    if args.json:
        json.dump(report, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return 0

    print(f"=== {args.name} ===")
    for stage, rows in report["stages"].items():
        print(f"[{stage}]")
        for row in rows:
            status = "OK" if row["ok"] else "MISSING"
            detail = f"count={row['count']}" if row["error"] is None else f"error={row['error']}"
            print(f"  - {row['expr']}: {status} ({detail})")
        print()

    if report["missing_metrics"] or report["failed_metrics"]:
        print("缺失指标:")
        for expr in report["missing_metrics"]:
            print(f"  - {expr}")
        if report["failed_metrics"]:
            print("查询失败:")
            for expr in report["failed_metrics"]:
                print(f"  - {expr}")
    else:
        print("关键指标完整")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
