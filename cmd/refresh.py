#!/usr/bin/env python3
"""Run the configured single- or multi-environment data refresh."""

import os
import subprocess
import sys
from pathlib import Path

from multi_env_refresh import DEFAULT_REGISTRY, lldp_command, run_once


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

    data_root = Path(os.environ.get("DATA_ROOT", str(ROOT)))
    data_root.mkdir(parents=True, exist_ok=True)
    for command in (
        [sys.executable, str(ROOT / "cmd" / "devices.py")],
        lldp_command(),
        [sys.executable, str(ROOT / "cmd" / "snmp.py")],
    ):
        if subprocess.run(command, cwd=data_root, check=False).returncode != 0:
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
