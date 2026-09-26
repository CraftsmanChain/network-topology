# Flapping Ports and Device Search Validation

## Single Environment

- Deployment: `root@10.102.10.6:/ops/web/topology`.
- URL: `http://10.102.10.6:8181/topology/`.
- `topology-web.service` and `snmp.timer` active after upgrade.
- Full `snmp.service` refresh completed successfully at 2026-09-26 14:32:05
  Asia/Shanghai; all collection steps, including `flapping`, succeeded.
- Existing configuration was preserved. Updated sanitized configuration and
  deployed code hashes are in `deployments/single-zwzp/`.
- Previous runtime code backup:
  `/ops/web/.topology-backups/flapping-search-20260926/code-before.tar.gz`.

## Source Verification

The generated snapshot was compared against a direct VM query using the
snapshot's own evaluation timestamp, not a later moving window:

- Rule: `changes(ifOperStatus[20m]) > 5`.
- Window: 2026-09-26 14:12:04 through 14:32:04 Asia/Shanghai.
- Switch: `NXDX01201F06-D01-5-7-25GYW-TOR-17`, IP `10.12.1.3`.
- Interface: `25GE1/0/18`, ifIndex `30`.
- Changes: **18**, identical in VM and `flapping.json`.
- One matching port, no out-of-inventory matching series.
- The earlier 14:09:37 through 14:29:37 window returned 17 changes. This is
  expected for a moving window; timestamps are displayed alongside the count.

## Tests

- Python: 30 tests passed, including threshold, inventory joins, duplicate
  series, failed/partial queries, and successful empty result handling.
- JavaScript: 7 tests passed, including device name/IP/alias search, management
  filtering, stale snapshots, and existing DOWN/missing port semantics.
- Go: environment isolation test passed for single mode, two multi-mode
  environments, missing snapshots and unknown environment codes.
- Chromium on the deployed page: name/IP search, keyboard selection, device
  details, flapping details, full-column view, minimize, and device drill-down
  passed; no page or console errors.
- Header layout checked at widths 1920, 1280, 768, 390 and 320; controls did not
  overlap or overflow the viewport. Actual topology still uses its existing
  independently scrollable large canvas.

## Multi Environment

The shared collector, frontend and backend implement both deployment modes.
The `10.255.171.88` deployment has **not** been updated for this feature yet;
the current VPN only reaches the single environment. Its backup manifest
continues to describe the previously deployed version. Deploy these same code
files after switching VPN, preserving that node's registry and credentials.
