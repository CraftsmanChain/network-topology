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
  - `cmd/refresh.py` 单环境、多环境共用的刷新入口，按配置选择数据源
  - `cmd/refresh_scheduler.py` 多环境独立计时调度，限制并发并防止重复采集
  - `cmd/snmp.py` 采集端口状态、流量与 LLDP，结合接口描述生成链路，保留 DOWN 端口的连接
  - `cmd/flapping.py` 查询近 20 分钟端口状态变化，输出独立的 `flapping.json`
  - `cmd/lldp.go` 保留供独立诊断使用，不再参与正式服务的链路写入
- `config/` 配置文件与示例
  - `group_rules.json`、`architecture_config.json`、`topology_config.json`
- `icons/` 设备与组图标资源
- `examples/` 采集任务的 systemd 定时示例
- 根目录下前端页面：`topology.html`、`topology-modern.html`、`config.html`、`login.html`、`debug.html`、`ethernet.html`
- 数据文件（前端只读）：`devices.json`、`links.json`、`topology.json`、`flapping.json`
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
  - `TOPOLOGY_REFRESH_LOOP=1` 多环境启用常驻调度；`TOPOLOGY_REFRESH_WORKERS` 默认最多并发 2 个环境，`TOPOLOGY_REFRESH_TICK_SEC` 默认每 10 秒检查任务

### 多环境独立刷新
- `config/environments.json` 中每个独立环境设置 `refresh_enabled: true`，`topology_refresh_sec` 是本轮完成到下一轮开始的等待秒数，不包括本轮耗时。大环境不会阻塞所有小环境，超过并发上限时按最早应刷新时间排队。
- 示例：银川智元等待 120 秒；算力小镇阿里、智元各等待 180 秒；中卫智谱等待 300 秒。`zpzw` 与 `zwzp` 共用数据目录，保持别名采集关闭。
- `refresh_retry_sec` 控制失败后等待时间，默认 60 秒（不超过正常间隔）；`refresh_timeout_sec` 默认 1800 秒，超时会终止该环境任务及子进程。失败时沿用已有成功数据，不伪造新状态。
- 每个数据目录用 `.refresh.lock` 防止手工采集与调度重复写入；`refresh-schedule.json` 记录耗时、完成时间和结果，服务重启后仍尊重已完成轮次的间隔。调度每次检查会重新读取环境注册表。
- 聚合环境使用 `examples/multi/snmp.service` 常驻服务：设置上述调度变量后，停用旧 `snmp.timer`，启用并启动 `snmp.service`。单环境保持原有 oneshot + timer，不受影响。
- 单独手工刷新仍可运行 `python3 cmd/multi_env_refresh.py --env yczy`。专线汇总与完整拓扑同轮生成，页面刷新频率不代表后台采集频率；告警过期阈值应大于等待间隔加正常采集耗时。

## 前端页面
- `/topology/` 与 `/topology/topology.html` 默认进入架构拓扑视图，按区域、集群、角色聚合展示链路。
- `/topology/modern.html` 与 `/topology/topology-modern.html` 兼容保留，也进入同一个架构视图。
- `/topology/config.html` 为配置后台，`/topology/login.html` 为登录页。
- 顶部搜索按设备名、别名或 IP 匹配原始设备，支持点击结果或键盘选择，直接进入设备详情。
- 集群信息中的“抖动端口”使用 `changes(ifOperStatus[20m]) > 5`，点击查看交换机、IP、端口、变化次数、端口快照状态及统计窗口；支持展示全部列、最小化和跳转设备详情。

### 抖动数据语义
- 单环境直连 VM 与多环境代理共用采集代码和认证配置，按 IP + ifIndex 精确关联本环境设备清单。同一端口重复指标取最大次数，不累加。
- 变化次数只表示状态切换次数，不等同于 DOWN；该统计不会改变设备或链路状态。管理接口的隐藏规则与页面现有开关一致。
- 统计窗口为查询采样时刻之前的 20 分钟，而非浏览器打开时刻；`changes()` 不提供每次切换的时间，因此不展示虚构的最后切换时间。端口快照状态来自 `topology.json`，与抖动查询时刻可能不同。
- 采集失败或 VM 返回部分结果时保留最后成功快照并标记过期；尚无成功数据时显示 `-`，过期计数附 `*`。成功的空结果才表示该窗口内没有符合条件的端口。
- `snmp.timer` 驱动后台采集，页面默认 6 分钟读取缓存。`GET /topology/flapping.json?cs=...` 严格读取所选环境的数据，不回退到其它环境。

## 配置文件说明
- `config/group_rules.json` 分组与样式覆盖规则。
- `config/architecture_config.json` 架构视图规则，配置分区、集群、角色、布局行与刷新周期；缺失时退化为单分区、单集群、单角色。
- `config/topology_config.json` 全局样式、布局与交互参数。

## 数据源与采集
- `devices.json` 设备名列表，优先以 `vmagent /api/v1/targets` 的 SNMP 抓取目标为准；不可用时回退到监控中的网络设备 `up` 指标。
- `devices-meta.json` 设备元数据，包含 IP、job、当前 up/down 状态等。
- `links.json` 设备间连线（正式链路，优先由端口别名从当前 `topology.json` 同步生成，LLDP 采集保留为补充/调试来源）。
- `links-alias-raw.json`：统一流程生成的原始链路；`links-alias.json` 为过滤后结果，发布为页面使用的 `links.json`。旧 `links-raw.json` 仅供历史 LLDP 诊断，不再由正式服务更新，也不参与页面展示。
- `topology.json` 设备端口速率与流量聚合（由 SNMP 指标从 Prometheus/VictoriaMetrics 查询生成）。
- 采集脚本：
  - 运行设备清单同步：
    - `python3 cmd/devices.py`
    - 输出更新 `devices.json` 与 `devices-meta.json`
    - 可通过 `VM_TARGETS_URL` 指定 `vmagent /api/v1/targets` 地址，通过 `PROM_QUERY_URL` 指定 Prometheus/VictoriaMetrics 查询地址
  - 运行节点/端口采集：
    - `python3 cmd/snmp.py`
    - 只更新 `topology.json`；完整刷新请使用 `python3 cmd/refresh.py`
  - 正式服务统一运行 `cmd/refresh.py`：设备清单 → 端口/邻居 → 链路解析 → 抖动查询 → 发布数据与 `status.json`。
  - 不要在正式数据目录另行运行 `cmd/lldp.go`，否则 LLDP-only 结果会覆盖包含 DOWN 链路的完整数据。
  - 如需只从现有 `topology.json` 的端口别名重建链路，可运行：
    - `python3 cmd/snmp.py --links-from-topology`
    - 输出 `links-alias-raw.json` 与 `links-alias.json`；正式 `links.json` 由统一刷新流程发布
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
- 单环境使用 `examples/`，部署目录为 `/ops/web/topology`；多环境使用 `examples/multi/`，部署目录为 `/ops/web/topology-multi`。两种模式使用同一套代码，由部署根目录 `topology.env` 的 `TOPOLOGY_MODE=single|multi` 控制；网页与采集服务共同读取此配置文件。先按实际数据源修改示例 `topology.env`，再安装 unit。
- 多环境的 `config/environments.json` 列出集群、数据目录、代理地址和 `secret_ref`；令牌放在 `config/.env_sources.local.json`，权限设为 `0600`。采集由 timer 触发且不重叠执行；查询暂时失败时沿用上一版端口状态。代理模式的 LLDP 邻居默认每小时全量更新一次，可用每个环境的 `lldp_refresh_sec` 调整，其余轮次保留已采到的邻居信息以缩短状态刷新时间。
- 节点实际配置备份、代码校验值与恢复步骤见 [deployments/README.md](deployments/README.md)。公开配置进入版本管理；含凭据的完整备份只保存在 Git 忽略的 `.local-backups/`，不上传 GitHub。

## 开发问题记录
- 前端使用 `vis-network`，图标资源走 `/icons/*`，缓存头已优化。
- 需要定制风格与图标时可修改 `config/topology_config.json` 的 `default_icons` 与布局相关字段。
