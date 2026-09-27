# Deployment Configuration Backups

Both modes run the same `server/main.go`, `topology.html`, and
`cmd/refresh.py` code. Do not maintain separate single/multi code copies.

## Runtime Configuration

Both systemd services read `<deployment-root>/topology.env`:

- `TOPOLOGY_MODE=single`: use `CONFIG_DIR`, `DATA_ROOT`, `PROM_QUERY_URL`, and
  optionally `VM_TARGETS_URL`. The page works without a `cs` parameter.
- `TOPOLOGY_MODE=multi`: use `ENV_REGISTRY`. Each registry entry specifies its
  own data source, config/data paths and `secret_ref`; the page selects `?cs=...`.
- Both modes collect devices and interface state, then derive `links.json`
  from that same topology using LLDP and interface descriptions. DOWN links
  must not disappear just because they no longer advertise LLDP neighbors.
- `LLDP_REFRESH_SEC` controls single-mode neighbor refresh. The equivalent
  registry field is `lldp_refresh_sec`. `snmp.timer` controls single-mode cadence.
- Multi mode uses independent `snmp@<code>.service` oneshots and
  `snmp@<code>.timer` units, with the legacy `snmp.service`/`snmp.timer` disabled.
  Each timer checks eligibility every minute; actual collection waits at least
  600 seconds after completion, or at least 900 seconds after a run exceeding
  300 seconds. Concurrency is capped by `TOPOLOGY_REFRESH_WORKERS` (default 2).
  Data-directory locks prevent duplicate writers, and `refresh-schedule.json`
  preserves completion times across restarts.

Use the service files and `topology.env` in `examples/` or `examples/multi/`
when creating a new deployment. Keep the existing node's configuration and
authentication files when upgrading code. Do not copy the repository's default
`config/` directory over a running environment.

## Capture

From the repository root, with the appropriate VPN connected:

```sh
python3 cmd/backup_deployment.py --host root@10.102.10.6 \
  --root /ops/web/topology --profile single-zwzp
python3 cmd/backup_deployment.py --host root@10.111.201.1 \
  --root /ops/web/topology --profile single-yczy
python3 cmd/backup_deployment.py --host ubuntu@10.255.171.88 \
  --root /ops/web/topology-multi --profile multi --sudo
python3 cmd/backup_deployment.py --host root@10.27.3.68 \
  --root /ops/web/topology --profile single-slxz-ali --ssh-config /path/to/jump.conf
```

For the slxz-ali single node, connect through `ubuntu@10.80.192.232` using
your SSH configuration. `--ssh-config` passes that file to `ssh -F`; it does
not change the default SSH configuration or save passwords in the project.

`deployments/<profile>/` contains public config snapshots, icons, systemd
units, optional drop-ins, and a capture manifest. JSON formatting is normalized
and credential values are redacted. Multi-mode snapshots include every
environment's configuration present on that node, not only the default one.

Complete unredacted configuration archives are stored in
`.local-backups/<profile>/` with directory mode `0700` and file mode `0600`.
They include login credentials, source tokens, configuration history and other
non-public configuration files. This directory is ignored by Git. Protect it
as a credential backup; it is intentionally not recoverable from GitHub.

## Recovery

1. Check `manifest.json` for the source host/root and capture time. Use a common
   code release for both deployments, not code copied from another environment.
2. Restore public `config/`, `icons/`, `topology.env`, and service definitions
   to that deployment's paths. Retain its credentials. For full disaster
   recovery, inspect the private archive and restore the missing credential
   files with restrictive permissions; never serve or publish the archive.
3. Run `systemctl daemon-reload`, restart `topology-web.service`, and start
   `snmp.service`. In single mode, enable/start the captured `snmp.timer` after
   collection. In scheduled multi mode, disable both legacy units and enable
   the captured `snmp@<code>.timer` instances listed in `systemd/unit-states.txt`;
   do not run both scheduling mechanisms.
4. Verify `/topology/api/health`, `/topology/api/runtime/status`, and the actual
   sample endpoints in `topology.json` and `links.json`.

Live traffic, timestamps and newly changed port state need not be byte-identical
between collection times. For parity, compare device/port identities and link
sets, then compare status sampled at equivalent times. Never force statuses
to agree or copy one environment's runtime data into the other.
