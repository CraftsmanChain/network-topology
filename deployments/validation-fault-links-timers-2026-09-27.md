# Fault Links and Systemd Timers Validation

## Scope

- Deployment: `ubuntu@10.255.171.88:/ops/web/topology-multi`, port 8181.
- Development branch: `fault-links-systemd-timers`.
- Single-environment node was not changed in this deployment.

## Frontend

- Normal and faulty aggregate paths now occupy separate SVG layers. Fault paths and their underlays render after all normal paths, before device icons. Selected paths retain the topmost focus layer.
- The fault metric counts unique device-port pairs from the raw effective links. Reverse LLDP records and aliases resolving to the same interface are deduplicated. Different port pairs remain separate even when grouped into one display edge; intra-group links are included.
- Hidden management ports are excluded. A missing endpoint alone does not imply DOWN. An actual DOWN endpoint still marks its port pair as faulty even when its peer is missing.
- Fault details use the same exact pair list as the metric, without adding inferred interfaces. Expand/minimize covers both the pair table and DOWN port table.
- Chromium verified all four deployed environments with zero page/console errors, matching metric and detail row counts, and working expand/minimize. A fully overlapping normal/fault path test verified that the fault path wins hit testing without selection.

Snapshot counts during deployment (not permanent expected values):

| Environment | Fault port pairs | Fault display edges |
| --- | ---: | ---: |
| yczy | 19 | 6 |
| zwzp | 9 | 8 |
| slxz-ali | 0 | 0 |
| slxz-zy | 4 | 1 |

Display edges may also include synthetic line-monitor summaries; those summaries without a device-port pair are not counted as physical fault links.

## Collection

- The previous Python scheduler was drained before replacement. No active writer was terminated during migration.
- Enabled `snmp@yczy.timer`, `snmp@slxz-ali.timer`, `snmp@slxz-zy.timer`, and `snmp@zwzp.timer`; the legacy `snmp.service` and `snmp.timer` are no longer enabled.
- Each timer invokes a oneshot unit to check eligibility every minute. A check is not a data collection and does not change data timestamps.
- Minimum cooldown is 600 seconds after completion, or 900 seconds after a run taking more than 300 seconds. Configured longer waits remain effective. Failure does not bypass the minimum cooldown.
- Current waits: yczy/slxz-ali/slxz-zy 600 seconds, zwzp 900 seconds. The disabled zpzw alias remains disabled.
- Directory locks prevent concurrent writers, including manual runs and aliases. Shared slot locks limit collection to two environments. Child processes inherit locks so a killed parent cannot create overlapping writers.
- `TimeoutStartSec=30min`, `KillMode=control-group`; termination cleans up child processes and records failure. Hard-kill recovery conservatively waits through the configured timeout boundary plus cooldown.
- Existing snapshot expiry thresholds were increased to match the longer intervals, without changing their data timestamps or device/port states.
- Verified timer enablement, successful cooldown checks, the absence of the previous daemon, and matching deployed/local source hashes. Web service remained active.
- Existing snapshots for all four environments reported zero stale nodes at migration.
- Real first timer-driven runs completed successfully (all collection steps true, zero stale nodes): slxz-ali waited 639.65 seconds and collected for 39.27 seconds; slxz-zy waited 604.04 seconds and collected for 88.40 seconds; yczy waited 604.22 seconds and collected for 13.70 seconds. Completion times were 10:43:35, 10:45:29, and 10:45:14 CST respectively.
- zwzp's preceding run took 510.52 seconds and finished at 10:33:36 CST. Its new timer correctly skipped collection until the 10:48:36 CST eligibility boundary, enforcing the 900-second cooldown. A complete new zwzp cycle was not awaited for this validation.

## Automated Tests

- 42 Python unit tests passed, including cooldown boundaries, failed runs, restart persistence, locks, bounded concurrency, alias/disabled environment handling, and child lock inheritance.
- 11 JavaScript tests passed, including physical-pair deduplication, hidden management ports, neutral missing endpoints, exact detail tables, and SVG layer order.
- `go test ./...` in `server/` passed.
- Target-host `systemd-analyze verify` accepted the new templates; it also reported pre-existing warnings in unrelated host units.

## Recovery

- Before-change archive on node: `/ops/web/.topology-multi-backup/systemd-timers-20260927/before.tar.gz`.
- Private local archives: `.local-backups/multi/20260927T023110Z.tar.gz` (before) and `.local-backups/multi/20260927T024036Z.tar.gz` (after), excluded from Git.
- Sanitized configuration, icons, service templates, enabled timer instances, and code hashes are in `deployments/multi/`.
- To roll back, first stop the new timers, let their current collectors finish, restore code/configuration and prior units from the archive, reload systemd, then re-enable the previous service. Never enable both schedulers together.
