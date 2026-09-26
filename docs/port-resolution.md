# Port Identity and Missing Data

`topology.json` contains collected interface inventory and operational state.
In gateway deployments, `snmp.py --links-from-topology` derives
`links-alias-raw.json` and `links-alias.json`; `multi_env_refresh.py` publishes
the filtered result as `links.json`. The architecture page fetches
`topology.json` and `links.json` for the selected `cs` environment.

## Resolution Rules

1. Prefer the actual target interface's `ifName` / `ifDescr`. Expand known
   abbreviations such as `25GE` without dropping slot or breakout numbers.
2. Apply an optional exact, device-scoped correction from
   `$CONFIG_DIR/port_aliases.json`. The replacement must exist in inventory.
3. Accept a unique reverse neighbor reference to the exact source interface.
4. Accept an interface description only if it uniquely identifies a port.
5. Otherwise retain the reported endpoint as unresolved. Never infer DOWN or
   guess by a shared final port number.

Resolved links use actual inventory names. When the reported name changes,
`targetPortReported` and `targetPortResolution` record the original name and
resolution method. Resolution does not modify inventory or operational state.
Reciprocal references and configured corrections are identity evidence, not
independent confirmation of current physical cabling.

LLDP collection prefers an interface-named PortId over PortDesc, which may be
free text shared by multiple interfaces. Partial LLDP query failures retain
cached neighbors rather than combining incomplete query results.

## Optional Configuration

```json
{
  "aliases": {
    "switch-b": {
      "FourHundredGigE1/0/37": "FHGigabitEthernet 0/37"
    }
  }
}
```

This file is isolated per environment through `CONFIG_DIR`. Omit it when no
verified name corrections are needed. It is not a port state override.
The `config/zp` example handles a known stale vendor/slot name in the CORE6-14
to Spine8-6 interface descriptions. Correcting the device descriptions is
preferable long term; remove the mapping once the source data is consistent.

## Verification

```sh
python3 -m unittest discover -s cmd -p 'test_*.py'
node --test cmd/test_topology_ports.js
```

Compare both endpoints in generated links with `ifName` / `ifDescr` in the
same topology snapshot. Check `ifOperStatus` for each actual interface through
the configured monitoring source. A missing endpoint is unknown, not DOWN.
The details table renders missing values neutrally and does not substitute
zero utilization for unavailable measurements. Duplicate descriptions cannot
overwrite exact interface identities in the browser's index.
