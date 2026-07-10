## zp Config Backup

- `topology_config.json`: `10.102.10.6` 当前正式管理后台使用中的全局配置备份。
- `group_rules.json`: 线上当前分组规则备份。
- `architecture_config.json`: 当前正式架构视图配置备份，按“智谱集群 / 以太网”拆分。
- `snmp.service` / `snmp.timer`: 线上更新前的 systemd unit 备份。
- `vmagent.service` / `snmp_exporter.service` / `topology-web.service`: 线上监控与图谱服务配置备份。
- `prometheus.yml`: 当前 vmagent 抓取配置备份。
- `snmp_public.yml` / `snmp_private.yml` / `snmp_supermicro.yml` / `snmp_lldp.yml`: 当前 snmp_exporter 配置备份。

## Notes

- 当前仓库按单一架构视图维护，经典视图专用的 `positions.json` 已不再纳入 git。
- 本次对 `zp` 集群不修改现有 VM 数据源，`snmp.service` 继续使用 `10.102.10.7` 的查询与 targets 接口。
- 脚本与定时任务更新到当前仓库版本，数据链路为 `devices.py -> lldp.go -> snmp.py`。
