# Independent Environment Refresh Validation

## Root Cause and Configuration

`slxz-ali`, `slxz-zy` and `yczy` had `refresh_enabled: false`; their snapshots
were still from September 24. Only `zwzp` was collected. The old global
oneshot loop also ignored each environment's `topology_refresh_sec`.

The multi deployment at `/ops/web/topology-multi` now runs `snmp.service` as
a persistent scheduler. The old `snmp.timer` is disabled, while the scheduler
is enabled at boot. No topology web service changes were needed.

| Environment | Devices | Wait After Completion |
| --- | ---: | ---: |
| yczy | 13 | 120 seconds |
| slxz-ali | 45 | 180 seconds |
| slxz-zy | 53 | 180 seconds |
| zwzp | 321 | 300 seconds |

`zpzw` stays disabled as a collector because it aliases the `zwzp` data
directory. Two environments may run concurrently; each data directory has
an exclusive writer lock. These intervals exclude actual collection time
and any wait for a concurrency slot. The registry is reloaded each tick.

Configuration, credentials and prior service definitions were backed up
before migration. Runtime rollback archive:
`/ops/web/.topology-multi-backup/independent-refresh-20260926/before.tar.gz`.
Updated public snapshots are in `deployments/multi/`; credentials remain in
private Git-ignored backups. Existing single-environment deployment remains
on its previous oneshot/timer configuration; the common code preserves that
mode when `TOPOLOGY_REFRESH_LOOP` is unset.

## Observed Runs

All times are Asia/Shanghai on 2026-09-26:

- `yczy`: started 19:58:52, first successful status at 19:59:15; scheduler
  recorded completion at 19:59:22. Started again at 20:01:22, exactly its
  configured 120-second wait later, and completed successfully.
- `slxz-ali`: started 19:58:52; successful status at 20:00:28; scheduler
  completion at 20:00:32. Next round started at 20:03:32.
- `slxz-zy`: started 19:59:22 as the first free slot opened; successful
  status at 20:01:13; scheduler completion at 20:01:22.
- `zwzp`: completed a successful old-service round at 19:58:50 before the
  service migration. Its next round became due at 20:03:50 and started at
  20:04:02 when `yczy` freed a slot. `slxz-zy` started another round at
  20:04:22 while this large-cluster collection was still running.
- All three re-enabled environments reported zero stale device snapshots
  and successful topology, links, monitoring and flapping steps.

## User-Reported Link

After the fresh `yczy` collection:

- `25G 汇聚02`, IP `10.111.51.248`, port `100GE1/0/3`, ifIndex `7`:
  the raw `ifOperStatus` query returned **2 (DOWN)**.
- `25G 接入03`, IP `10.111.51.246`, port `100GE2/0/5`, ifIndex `69`:
  the raw `ifOperStatus` query returned **1 (UP)**.
- The previously reported `HundredGigE2/0/5` is now resolved to inventory
  name `100GE2/0/5` in `links.json`. The actual page displays DOWN / UP
  with both endpoints present; neither row direction displays missing.
- This verifies endpoint-name resolution and source metric interpretation,
  not the continued physical correctness of a cached LLDP adjacency.

## Tests

- 40 Python tests passed, including independent cadence, bounded concurrency,
  configuration changes, persisted scheduling, aliases, failures, timeout
  cleanup, writer locking and preserved single-mode behavior.
- 7 JavaScript regression tests passed.
- Chromium verified the reported link on the actual `yczy` page, with no
  page or console errors. The page showed 11 display nodes and 68 links.
