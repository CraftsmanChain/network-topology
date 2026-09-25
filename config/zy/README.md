# 银川-智元环境配置

- `group_rules.json`：留空，按实际监控设备直接展示，不做设备组聚合。
- `architecture_config.json`：按“安全出口区 / 带外管理区 / 业务互联区”三块布局。
- `topology_config.json`：定义设备别名、防火墙图标、专线监控，以及固定展示的备用防火墙。

建议部署路径：

- `CONFIG_DIR=/ops/web/topology/config/zy`
- `DATA_ROOT=/ops/web/topology/runtime-data/zy`

采集数据源：

- `PROM_QUERY_URL=http://127.0.0.1:9090/api/v1/query`
- `VM_TARGETS_URL=http://127.0.0.1:9090/api/v1/targets`
- `ENV_REGISTRY=/ops/web/topology/config/zy/environment.json`

说明：

- 单环境以实时监控数据为准生成 `devices.json`、`topology.json`、`links.json`、`prometheus.json`。
- `snmp.service` 通过 `cmd/multi_env_refresh.py` 单环境注册表生成运行时数据，并同步写入 `status.json`。
- 防火墙主设备保留监控数据，防火墙备设备通过 `synthetic_devices` 固定展示，防火墙端口状态统一按配置覆盖为 `UP`。
