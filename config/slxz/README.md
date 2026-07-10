## slxz Config Backup

- `architecture_config.json` / `group_rules.json` / `topology_config.json`: `10.27.3.68` 当前管理后台仍在使用的正式配置备份。
- `positions.json` / `link_overrides.json`: 历史经典视图相关配置备份，仅保留用于回溯，不再由当前后台维护。
- `prometheus.yml`: vmagent scrape config backup. The `snmp_exporter-lldp` job uses `__param_auth`.
- `snmp_public.yml`: shared auth and public SNMP modules loaded by `snmp_exporter` on port `9116`.
- `snmp_private.yml`: private SNMP modules backup.
- `snmp_supermicro.yml`: Supermicro SNMP modules backup.
- `snmp_topology.yml`: existing shared topology module backup.
- `snmp_lldp.yml`: validated LLDP-only module file for the shared `9116` exporter. It intentionally contains only `lldp_info`; auth is inherited from `snmp_public.yml`.
- `snmp.service` / `snmp.timer`: 当前正式采集任务备份。`snmp.service` 已固定走本机 `10.27.3.68` 的 `PROM_QUERY_URL` 与 `VM_TARGETS_URL`，不再探测 `10.102.10.7`。
- `vmagent.service` / `snmp_exporter.service` / `topology-web.service`: 当前正式 systemd 服务配置备份。`topology-web.service` 已收口为固定二进制 `/ops/web/topology/server/topology-web` 启动，避免 `go run` 产生的缓存二进制残留占用 `8181`。

## Restore Notes

- Validate `snmp_exporter` config before restart:

```bash
/usr/local/exporters/snmp_exporter/snmp_exporter --config.file=/usr/local/exporters/snmp_exporter/snmp_\*.yml --dry-run
```

- Restart `snmp_exporter` after the dry run passes:

```bash
systemctl restart snmp_exporter
```

- Validate `vmagent` config before reload:

```bash
/data/local/vm/bin/vmagent-prod -promscrape.config=/data/local/vm/cfg/prometheus.yml -promscrape.config.dryRun
```

- Reload `vmagent` without restart:

```bash
kill -HUP $(pgrep -f '/data/local/vm/bin/vmagent-prod -promscrape.config=/data/local/vm/cfg/prometheus.yml')
```
