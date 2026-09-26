# Single / Multi Parity Verification

## Deployments

- Single: `root@10.102.10.6`, `/ops/web/topology`, direct VM at
  `http://10.102.10.7:8481/select/0/prometheus/api/v1/query`.
- Multi: `ubuntu@10.255.171.88`, `/ops/web/topology-multi`, `cs=zwzp`,
  source and credential reference from its captured environment registry.
- All 11 entries in both `code-sha256.json` manifests match each other and the
  local runtime source / built binary. Presentation configuration is preserved
  independently for each deployment.

## Root Causes and Changes

The old single service ran `devices.py`, `lldp.go`, then `snmp.py`. The sample
interfaces were already DOWN in `topology.json`, but their link was absent
from the LLDP-only `links.json`. A DOWN interface may no longer advertise a
neighbor, so LLDP alone is insufficient to retain fault links.

Both services now run `cmd/refresh.py` and the same pipeline: inventory,
interface state / neighbors, then inventory-aware LLDP / description link
resolution. Failure to derive fresh links is reported as a failure and cannot
silently publish an old intermediate file as a successful refresh.

The browser also no longer overrides an explicit DOWN state with positive
historical traffic. Rates can remain nonzero after a port goes DOWN.

## Results

- Both pages: 39 display nodes, 5149 data links, zero unmatched endpoints;
  no browser console errors in the checked sessions.
- Full identity comparison: 22761 ports on each side, no missing or extra
  port identities. All 5149 undirected link endpoint pairs match exactly.
- Snapshot comparison: single at 11:29:33 CST and multi at 11:31:35 CST.
  One operational state differed; all other port states matched.
- Fixed-time comparison at **2026-09-26 11:30:00 CST** for instances
  `10.12.10.66`, `10.12.13.186`, and `10.12.1.3`: all **296** `ifOperStatus`
  samples match through direct VM and the proxy, including sample timestamps.
- Multi's first completed unified run at 11:49:45 CST reported every step
  successful, 321 nodes, zero stale nodes, and 5149 links.
- The known sample, CORE6-14 `FourHundredGigE1/0/62` to Spine8-6
  `FHGigabitEthernet 0/37`, is DOWN at both ends and visible on both pages.

## Separate Observation

`NXDX01201F06-D01-5-7-25GYW-TOR-17` (`10.12.1.3`), port `25GE1/0/18`
(`ifIndex=30`), accounts for the one cross-time state difference. VM records
repeated transitions between `ifOperStatus=1` and `2`, including 11:28 DOWN,
11:29 UP, 11:30 DOWN and 11:31 UP. This needs separate investigation of the
device / monitoring source; this change does not force or suppress its state.

Configuration snapshots are versioned under `single-zwzp/` and `multi/`.
Unredacted configuration archives and verification data remain only in the
Git-ignored `.local-backups/` directory. See `README.md` here for recovery.
