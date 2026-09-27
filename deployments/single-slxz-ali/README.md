# Slxz Ali Single Environment

- Jump host: `ubuntu@10.80.192.232`
- Target: `root@10.27.3.68`
- Production URL: `http://10.27.3.68:8181/topology/`
- Root/data directory: `/ops/web/topology`
- Display/auth configuration: `/ops/web/topology/config`
- Active service configuration: `/ops/web/topology/topology.env`
- VictoriaMetrics: `http://10.27.3.68:8481/select/0/prometheus/api/v1/query`
- vmagent targets: `http://10.27.3.68:8429/api/v1/targets`

The node runs the common code with `TOPOLOGY_MODE=single` and
`SINGLE_ENV_CODE=slxz-ali`. Data-source URLs were retained from the previous
service. Credentials are not stored in this snapshot; use the private archive
for recovery of authentication and other non-public configuration.

`topology-web.service` runs `server/topology-web`. `snmp.service` runs the
shared `cmd/refresh.py` pipeline with a 30-minute timeout and directory lock.
The old separate Go LLDP writer is no longer part of the service. The existing
`snmp.timer` still waits five minutes after completion. The multi-environment
10/15-minute cooldown policy was not applied to this single deployment.

## September 27, 2026 Update

- Deployed the common application/collector source from `8b35dc5`, plus the
  backup utility's SSH-config support added by this deployment change.
- Production Web health returned HTTP 200; Web service and timer are active.
- First collection completed at 11:47:55 CST: 45 devices, 5163 ports, zero
  stale nodes. Device/SNMP/LLDP/link/line-monitor/flapping steps all succeeded.
- Device and port inventory counts were unchanged. Derived link records
  changed from 1156 to 1175 using the shared collection/resolution pipeline.
- Display configuration, authentication and icon file hashes were unchanged.
- Port 8281's `topology-poc.service` was not modified or restarted; its PID
  remained 2220. Production data files were refreshed as expected.
- Deployed frontend, refresh entrypoint, SNMP collector and Web binary hashes
  match the local artifacts.
- 43 Python tests, 11 JavaScript tests and server Go tests passed locally.
- No page interaction checks were performed; those are left to the user.

## Recovery Archives

- Node code/config: `/ops/web/.topology-backup/single-slxz-ali-20260927/before.tar.gz`.
- Node pre-update data: `/ops/web/.topology-backup/single-slxz-ali-20260927/data-before.tar.gz`.
- Local before: `.local-backups/single-slxz-ali/20260927T034319Z.tar.gz`.
- Local after: `.local-backups/single-slxz-ali/20260927T034856Z.tar.gz`.

For rollback, stop the timer, wait for any collector to finish, restore the
previous code and unit files, reload systemd, restart Web and resume the
timer. Only restore runtime data if a data rollback is explicitly intended.
Do not run the legacy and shared collectors concurrently.

Use `cmd/backup_deployment.py --ssh-config /path/to/jump.conf` for subsequent
captures through the jump host. The temporary local SSH connection used for
this rollout does not alter the user's default SSH configuration.
