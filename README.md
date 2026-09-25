# 集群拓扑监控（web）

当前正式版本：`v2.0.0`

- 静态前端配合轻量后端，展示网络/集群架构拓扑。
- 默认入口为架构视图，同时保留登录与配置后台用于维护架构相关配置。
- 数据可从 Prometheus 或 VictoriaMetrics 查询接口获取指标，生成设备、端口与链路数据；架构视图默认 6 分钟刷新。

![架构拓扑视图](docs/architecture-view.png)

## 目录结构
- `server/` 后端服务（Gin），提供静态资源、登录鉴权与配置 API
- `cmd/` 数据采集脚本：
  - `cmd/devices.py` 优先基于 `vmagent /api/v1/targets` 同步设备基线，并回退到监控中的网络设备 `up` 指标生成 `devices.json` 与 `devices-meta.json`
  - `cmd/lldp.go` 基于 LLDP 指标生成 `links-raw.json` 与 `links.json`
  - `cmd/snmp.py` 基于 SNMP/流量指标生成 `topology.json`
- `config/` 配置文件与示例
  - `group_rules.json`、`architecture_config.json`、`topology_config.json`
- `icons/` 设备与组图标资源
- `examples/` 采集任务的 systemd 定时示例
- 根目录下前端页面：`topology.html`、`topology-modern.html`、`config.html`、`login.html`、`debug.html`、`ethernet.html`
- 数据文件（前端只读）：`devices.json`、`links.json`、`topology.json`
- 调试/辅助数据：`devices-meta.json`、`links-raw.json`、`links-alias-raw.json`、`links-alias.json`

## 快速开始
- 依赖：
  - Go ≥ 1.20
  - Python ≥ 3.8，且安装 `requests`
- 启动后端：
  - `go run server/main.go`
  - 浏览器访问 `http://localhost:8181/topology/`
- 可用环境变量：
  - `PORT` 默认 `8181`
  - `WEB_ROOT` 静态资源根目录，默认 `..`（项目根）
  - `CONFIG_DIR` 配置目录，默认 `../config`
  - `DATA_ROOT` 动态数据目录，默认等于 `WEB_ROOT`，可用于测试项目复用现有数据文件
  - `TOPOLOGY_MODE=single|multi` 指定单环境或多环境；`multi` 读取 `ENV_REGISTRY`（默认 `config/environments.json`）

## 前端页面
- `/topology/` 与 `/topology/topology.html` 默认进入架构拓扑视图，按区域、集群、角色聚合展示链路。
- `/topology/modern.html` 与 `/topology/topology-modern.html` 兼容保留，也进入同一个架构视图。
- `/topology/config.html` 为配置后台，`/topology/login.html` 为登录页。

## 配置文件说明
- `config/group_rules.json` 分组与样式覆盖规则。
- `config/architecture_config.json` 架构视图规则，配置分区、集群、角色、布局行与刷新周期；缺失时退化为单分区、单集群、单角色。
- `config/topology_config.json` 全局样式、布局与交互参数。

## 数据源与采集
- `devices.json` 设备名列表，优先以 `vmagent /api/v1/targets` 的 SNMP 抓取目标为准；不可用时回退到监控中的网络设备 `up` 指标。
- `devices-meta.json` 设备元数据，包含 IP、job、当前 up/down 状态等。
- `links.json` 设备间连线（正式链路，优先由端口别名从当前 `topology.json` 同步生成，LLDP 采集保留为补充/调试来源）。
- `links-raw.json` 原始链路（调试用）。
- `topology.json` 设备端口速率与流量聚合（由 SNMP 指标从 Prometheus/VictoriaMetrics 查询生成）。
- 采集脚本：
  - 运行设备清单同步：
    - `python3 cmd/devices.py`
    - 输出更新 `devices.json` 与 `devices-meta.json`
    - 可通过 `VM_TARGETS_URL` 指定 `vmagent /api/v1/targets` 地址，通过 `PROM_QUERY_URL` 指定 Prometheus/VictoriaMetrics 查询地址
  - 运行节点/端口采集：
    - `python3 cmd/snmp.py`
    - 输出更新 `topology.json`，并基于端口别名同步更新 `links-raw.json` 与 `links.json`
  - 运行 LLDP 链路采集：
    - `go run cmd/lldp.go`
    - 输出 `links-raw.json`（原始）与过滤后的 `links.json`
  - 如需只从现有 `topology.json` 的端口别名重建链路，可运行：
    - `python3 cmd/snmp.py --links-from-topology`
    - 输出 `links-raw.json` 与 `links.json`
- 可参考 `examples/snmp.service` 与 `examples/snmp.timer` 将采集任务以 systemd 定时运行。

## 后端 API
- `GET /topology/api/health` 服务健康检查
- `POST /topology/api/login` 登录，读取 `config/.users.local` 生成 JWT
- `GET /topology/api/config/:name` 读取配置
  - `name ∈ {group_rules, architecture, topology_config}`
- `PUT /topology/api/config/:name` 写入配置（需 `Bearer`）
- `GET /topology/api/history/:name` 查看配置历史
- `POST /topology/api/history/:name/backup` 备份当前配置
- `POST /topology/api/history/:name/rollback` 回滚指定历史版本
- 静态与数据：
  - `/topology/icons` 仅映射到 `WEB_ROOT/icons`；配置目录和本地凭据文件不通过静态路由开放
  - `/topology/` 与 `/topology/topology.html` 进入架构拓扑视图
  - `/topology/modern.html` 与 `/topology/topology-modern.html` 进入架构拓扑视图
  - `/topology/devices.json`、`/topology/links.json`、`/topology/topology.json`、`/topology/prometheus.json` 优先从 `DATA_ROOT` 读取，若不存在则返回空结构

## 部署建议
- 设置环境变量：
  - `WEB_ROOT` 指向静态资源所在目录
  - `CONFIG_DIR` 指向配置目录
- 通过反向代理暴露 `8181` 端口，开启 TLS。
- 将采集脚本按需写入定时任务，保证数据文件持续更新。
- 单环境使用 `examples/topology-web.service`、`examples/snmp.service`、`examples/snmp.timer`，部署目录为 `/ops/web/topology`；多环境使用 `examples/multi/` 中同名 unit，部署目录为 `/ops/web/topology-multi`。两者均通过 `topology-web.service` 和 `snmp.timer` 管理，部署模式由 unit 中的 `TOPOLOGY_MODE` 控制。
- 多环境的 `config/environments.json` 列出集群、数据目录、代理地址和 `secret_ref`；令牌放在 `config/.env_sources.local.json`，权限设为 `0600`。采集由 timer 触发且不重叠执行；查询暂时失败时沿用上一版端口状态。代理模式的 LLDP 邻居默认每小时全量更新一次，可用每个环境的 `lldp_refresh_sec` 调整，其余轮次保留已采到的邻居信息以缩短状态刷新时间。

## 开发问题记录
- 前端使用 `vis-network`，图标资源走 `/icons/*`，缓存头已优化。
- 需要定制风格与图标时可修改 `config/topology_config.json` 的 `default_icons` 与布局相关字段。
