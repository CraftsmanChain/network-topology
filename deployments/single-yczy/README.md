# Yinchuan Zhiyuan Single Environment

- Host: `root@10.111.201.1`
- URL: `http://10.111.201.1:8181/topology/`
- Root: `/ops/web/topology`
- Active configuration: `/ops/web/topology/topology.env`
- Display/auth configuration: `/ops/web/topology/config/zy`
- Runtime data: `/ops/web/topology/runtime-data/zy`
- Source: local Prometheus at `http://127.0.0.1:9090`.

The deployment uses the common code with `TOPOLOGY_MODE=single`,
`SINGLE_ENV_CODE=yczy`, and no `cs` parameter. The retained
`config/zy/environment.json` is the legacy registry; it is backed up for
recovery but is no longer the active service configuration.

`topology-web.service` runs `server/topology-web`. `snmp.service` runs
`cmd/refresh.py` as a oneshot with a 30-minute timeout and directory lock.
The existing `snmp.timer` still waits five minutes after completion.
The multi-environment 10/15-minute cooldown policy was not applied here.

Source, group, architecture, authentication and icon files were preserved.
Monitor snapshot expiry was increased to 900 seconds to cover the existing
five-minute collection cadence. Authentication/source secrets are only in
the ignored private archives under `.local-backups/single-yczy/`.

## September 27, 2026 Update

- Deployed common application/collector source from `9b60751`, with the
  updated deployment-backup utility from this change.
- Web health endpoint returned HTTP 200; Web service and timer are active.
- First shared-pipeline collection completed at 11:03:06 CST: 13 devices,
  zero stale nodes, and all devices/SNMP/LLDP/links/line-monitor/flapping
  steps successful.
- Configuration, authentication and icon hashes were unchanged during rollout.
- 42 Python tests, 11 JavaScript tests and server Go tests passed locally.
- Page interactions were not tested, as requested; user validation remains.

## Recovery Archives

- Node: `/ops/web/.topology-backup/single-yczy-20260927/before.tar.gz`.
- Local before: `.local-backups/single-yczy/20260927T025927Z.tar.gz`.
- Local after: `.local-backups/single-yczy/20260927T030329Z.tar.gz`.

For rollback, stop the timer, wait for any collector to finish, restore the
previous application and service files, reload systemd, restart Web and
resume the original timer. Keep live data unless a data rollback is also
explicitly intended. The prior root-level `topology-web` binary remains on
the node but is no longer the active executable.
