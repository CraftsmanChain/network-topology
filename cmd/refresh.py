#!/usr/bin/env python3
"""Run the configured single- or multi-environment data refresh."""

import os
from pathlib import Path

from multi_env_refresh import DEFAULT_REGISTRY, refresh_environment, run_once


ROOT = Path(__file__).resolve().parents[1]


def main():
    mode = os.environ.get("TOPOLOGY_MODE", "auto").strip().lower()
    registry = Path(os.environ.get("ENV_REGISTRY", str(DEFAULT_REGISTRY)))
    if mode not in {"auto", "single", "multi"}:
        print(f"[ERROR] Unsupported TOPOLOGY_MODE={mode}", flush=True)
        return 2
    if mode == "multi" or (mode == "auto" and registry.is_file()):
        if not registry.is_file():
            print(f"[ERROR] Multi-environment registry missing: {registry}", flush=True)
            return 2
        return 0 if run_once() else 1

    spec = {
        "code": os.environ.get("SINGLE_ENV_CODE", "single"),
        "name": os.environ.get("SINGLE_ENV_NAME", "single"),
        "config_dir": os.environ.get("CONFIG_DIR", str(ROOT / "config")),
        "data_root": os.environ.get("DATA_ROOT", str(ROOT)),
        "prom_query_url": os.environ.get("PROM_QUERY_URL") or os.environ.get("PROM_URL", ""),
        "vm_targets_url": os.environ.get("VM_TARGETS_URL", ""),
        "prom_skip_tls_verify": os.environ.get("PROM_SKIP_TLS_VERIFY", "0"),
        "secret_ref": "single",
        "topology_error_after_sec": int(os.environ.get("TOPOLOGY_ERROR_AFTER_SEC", "900")),
        "monitor_error_after_sec": int(os.environ.get("MONITOR_ERROR_AFTER_SEC", "180")),
    }
    if "LLDP_REFRESH_SEC" in os.environ:
        spec["lldp_refresh_sec"] = int(os.environ["LLDP_REFRESH_SEC"])
    secrets = {"single": os.environ.get("PROM_BEARER_TOKEN", "")}
    return 0 if refresh_environment(spec, secrets, ROOT) else 1


if __name__ == "__main__":
    raise SystemExit(main())
