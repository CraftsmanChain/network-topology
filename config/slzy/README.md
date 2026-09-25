# 算力小镇-智元集群配置

- `group_rules.json`：按现网设备命名规则聚合 `MGT / OOB / public / GC / Obs / Stor` 设备组，减少大规模节点的主视图拥挤。
- `architecture_config.json`：按“带内管理区 / 带外管理区 / 公网区 / GC 区 / 观测区 / 存储区”六个区域布局。
- `topology_config.json`：沿用算力小镇的整体视觉风格，仅保留本集群标题与分组别名。

建议部署路径：

- `CONFIG_DIR=/ops/web/topology/config/slzy`
- `DATA_ROOT=/ops/web/topology/runtime-data/slzy`

采集数据源：

- `PROM_QUERY_URL=http://10.80.192.233:18481/select/0/prometheus/api/v1/query`
- `VM_TARGETS_URL=http://10.80.192.233:18429/api/v1/targets`

说明：

- 当前环境 `ifAlias` 缺失，链路生成依赖 `ifDescr / ifName` 与 LLDP 回退路径，属于脚本已支持的正常场景。
