# Slxz Zhiyuan Single Environment

- Jump host: `ubuntu@10.80.192.232`
- Target: `root@10.80.192.233`
- URL: `http://10.80.192.233:8181/topology/`
- Deployment root: `/ops/web/topology`
- Display/auth configuration: `/ops/web/topology/config/slzy`
- Runtime data: `/ops/web/topology/runtime-data/slzy`
- Active service configuration: `/ops/web/topology/topology.env`
- VictoriaMetrics: `http://10.80.192.233:18481/select/0/prometheus/api/v1/query`
- vmagent targets: `http://10.80.192.233:18429/api/v1/targets`

The deployment uses the common code with `TOPOLOGY_MODE=single` and
`SINGLE_ENV_CODE=slxz-zy`. Data-source URLs and display/auth configuration
were retained. Credentials are not stored in this public snapshot.

`topology-web.service` runs `server/topology-web`. This node retains its
existing collector unit names: `topology-snmp.service` and
`topology-snmp.timer`. The service now runs `cmd/refresh.py` as a oneshot,
with directory locking and a 30-minute timeout, instead of separate devices,
LLDP binary and SNMP commands. Its timer still waits five minutes after
completion. No duplicate `snmp.service` or `snmp.timer` was installed.
The multi-environment 10/15-minute cooldown policy was not applied here.

## September 27, 2026 Update

- Deployed common application/collector source from `299f4c1`, plus the
  backup utility support for the existing `topology-snmp` unit names.
- Web health returned HTTP 200; Web service and collection timer are active.
- First collection completed at 11:59:26 CST: 53 devices, 5977 ports, zero
  stale nodes. All device/SNMP/LLDP/link/line-monitor/flapping steps succeeded.
- Display/auth configuration and icon hashes were unchanged during rollout.
- Deployed frontend, refresh entrypoint, collector and Web binary hashes
  match the local artifacts. Unrelated status collectors were not changed.
- 43 Python tests, 11 JavaScript tests and server Go tests passed locally.
- Page interaction checks were left to the user.

## Link Snapshot Check

The old independent LLDP output had 711 unique port-pair records. Of these,
463 referenced a device name outside the current 53-device inventory. The
remaining 248 inventory-internal port pairs are all present in the new output;
no original inventory-internal connection was lost.

The shared pipeline generated 258 records: the retained 248 plus 10 added
by the common resolution logic. Device IDs and the 5977-port inventory were
unchanged. Ports carrying LLDP peer fields increased from 909 to 946; neighbor
collection remains available in port data even when the peer is outside the
topology inventory. This is a snapshot comparison, not physical cabling
verification. Six new-output records did not match both endpoint names by
the audit's exact normalized ifName/ifDescr/ifIndex comparison; unresolved
endpoints must not be interpreted as DOWN solely because they are missing.

## Recovery

- Node code/config: `/ops/web/.topology-backup/single-slxz-zy-20260927/before.tar.gz`.
- Node data snapshot: `/ops/web/.topology-backup/single-slxz-zy-20260927/data-before.tar.gz`.
- Local before: `.local-backups/single-slxz-zy/20260927T035550Z.tar.gz`.
- Local after: `.local-backups/single-slxz-zy/20260927T040212Z.tar.gz`.

Stop `topology-snmp.timer` and wait for its running collector before restoring
code and unit files. Reload systemd, restart Web and resume that same timer.
Only restore runtime data if a data rollback is explicitly intended. Never
run legacy and shared collectors concurrently.

For subsequent backups, use `cmd/backup_deployment.py --ssh-config` with your
own jump-host configuration. Temporary rollout SSH configuration does not
alter the user's default SSH settings and contains no stored passwords.
