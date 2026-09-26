#!/usr/bin/env python3
"""Capture recoverable private configs and export a credential-free Git snapshot."""

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import subprocess
import tarfile
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parents[1]
CONFIG_NAMES = {
    "topology_config.json", "architecture_config.json", "group_rules.json",
    "positions.json", "link_overrides.json", "port_aliases.json", "environments.json",
}
SERVICE_NAMES = {"snmp.service", "snmp.timer", "topology-web.service"}
SENSITIVE = re.compile(r"password|passwd|token|authorization|credential|community|secret", re.I)
REMOTE_CAPTURE = r'''
import hashlib, io, json, os, sys, tarfile
root = os.path.abspath(sys.argv[1])
with tarfile.open(fileobj=sys.stdout.buffer, mode="w|gz") as archive:
    for name in ("config", "icons", "topology.env", ".env"):
        path = os.path.join(root, name)
        if os.path.exists(path):
            archive.add(path, arcname=name)
    for name in ("snmp.service", "snmp.timer", "topology-web.service"):
        for suffix in ("", ".d"):
            path = "/etc/systemd/system/" + name + suffix
            if os.path.exists(path):
                archive.add(path, arcname="systemd/" + name + suffix)
    hashes = {}
    for name in ("cmd/refresh.py", "cmd/multi_env_refresh.py", "cmd/snmp.py", "cmd/devices.py", "cmd/atomic_json.py", "cmd/lldp.go", "topology.html", "config.html", "login.html", "server/main.go", "server/topology-web"):
        path = os.path.join(root, name)
        if os.path.isfile(path):
            with open(path, "rb") as source:
                hashes[name] = hashlib.sha256(source.read()).hexdigest()
    data = (json.dumps(hashes, indent=2) + "\n").encode()
    info = tarfile.TarInfo("code-sha256.json")
    info.size = len(data)
    archive.addfile(info, io.BytesIO(data))
'''


def redact_urls(text):
    def replace(match):
        url = urlsplit(match.group())
        host = url.netloc.rsplit('@', 1)[-1]
        query = urlencode([(key, '<redacted>' if SENSITIVE.search(key) else value) for key, value in parse_qsl(url.query, keep_blank_values=True)])
        return urlunsplit((url.scheme, host, url.path, query, url.fragment))
    return re.sub(r'https?://[^\s"\']+', replace, text)


def redact_json(value):
    if isinstance(value, dict):
        return {
            key: "<restore-from-private-backup>" if SENSITIVE.search(key) and not key.endswith("_ref")
            else redact_json(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_json(item) for item in value]
    if isinstance(value, str):
        return redact_urls(value)
    return value


def redact_environment(text):
    lines = []
    for line in text.splitlines():
        # EnvironmentFile and *_FILE are paths, not credential values.
        settings = re.findall(r'([A-Za-z_][A-Za-z0-9_]*)=', line)
        if any(SENSITIVE.search(setting) and not setting.endswith(("_FILE", "_REF")) for setting in settings) or re.search(r'--(?:password|token|secret|community)(?:=|\s)', line, re.I):
            lines.append("# Credential setting omitted; restore from private backup.")
        else:
            lines.append(redact_urls(line))
    return "\n".join(lines) + "\n"


def export_archive(archive_path, destination):
    exported = {}
    with tarfile.open(archive_path, "r:gz") as archive:
        for member in archive:
            path = PurePosixPath(member.name)
            if not member.isfile() or path.is_absolute() or any(part.startswith('.') for part in path.parts):
                continue
            is_config = path.parts[0] == "config" and path.name in CONFIG_NAMES and not any(part.startswith('.') for part in path.parts)
            is_service = len(path.parts) == 2 and path.parts[0] == "systemd" and path.name in SERVICE_NAMES
            is_override = len(path.parts) == 3 and path.parts[0] == "systemd" and path.parts[1] in {name + '.d' for name in SERVICE_NAMES} and path.suffix == '.conf'
            is_icon = path.parts[0] == "icons" and path.suffix.lower() in {".png", ".jpg", ".jpeg", ".svg", ".webp"}
            if not (is_config or is_service or is_override or is_icon or str(path) in {"topology.env", "code-sha256.json"}):
                continue
            raw = archive.extractfile(member).read()
            if is_config or str(path) == "code-sha256.json":
                output = (json.dumps(redact_json(json.loads(raw)), ensure_ascii=False, indent=2) + "\n").encode()
            elif is_icon:
                output = raw
            else:
                output = redact_environment(raw.decode()).encode()
            target = destination.joinpath(*path.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(output)
            exported[str(path)] = hashlib.sha256(output).hexdigest()
    return exported


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True, help="SSH destination, e.g. root@10.102.10.6")
    parser.add_argument("--root", required=True, help="Remote deployment root")
    parser.add_argument("--profile", required=True, help="Local deployment snapshot name")
    parser.add_argument("--sudo", action="store_true", help="Read remote configs through passwordless sudo")
    args = parser.parse_args()
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", args.profile):
        parser.error("profile must be a lowercase name without path separators")
    if not args.root.startswith('/') or args.host.startswith('-'):
        parser.error("root must be absolute and host must not be an option")
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    private_dir = ROOT / ".local-backups" / args.profile
    private_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(ROOT / ".local-backups", 0o700)
    os.chmod(private_dir, 0o700)
    archive_path = private_dir / (timestamp + ".tar.gz")
    command = (["sudo", "-n"] if args.sudo else []) + ["python3", "-c", REMOTE_CAPTURE, args.root]
    fd = os.open(archive_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as output:
        subprocess.run(["ssh", args.host, shlex.join(command)], stdout=output, check=True)
    destination = ROOT / "deployments" / args.profile
    exported = export_archive(archive_path, destination)
    manifest = {
        "host": args.host, "root": args.root, "captured_at_utc": timestamp,
        "private_archive": str(archive_path.relative_to(ROOT)),
        "files_sha256": exported,
    }
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Captured {len(exported)} public config files to {destination}")
    print(f"Private recovery archive (0600, ignored by Git): {archive_path}")


if __name__ == "__main__":
    main()
