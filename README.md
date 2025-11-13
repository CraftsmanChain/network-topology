# 集群拓扑监控（web）

- 静态前端配合轻量后端，展示与管理网络/集群拓扑，支持多拓扑多集群。
- 支持管理员登录与在线编辑配置，历史快照与回滚。
- 数据统一到 Prometheus 获取指标生成设备与链路数据（默认5分钟更新数据），可方便接入现有集群监控。

## 目录结构
- `server/` 后端服务（Gin），提供静态资源与配置 API
- `cmd/` 数据采集脚本：
  - `cmd/lldp.go` 基于 LLDP 指标生成 `links.json` 并增量维护 `devices.json`
  - `cmd/snmp.py` 基于 SNMP/流量指标生成 `topology.json`
- `config/` 配置文件与示例（受保护写入）
  - `.users.local` 两行明文：第1行用户名，第2行密码
  - `group_rules.json`、`topology_config.json`/`topology.config.json`、`positions.json`、`link_overrides.json`
  - `.history/` 按文件归档最近版本，支持查看与回滚
- `icons/` 设备与组图标资源
- `examples/` 采集任务的 systemd 定时示例
- 根目录下若干前端页面：`topology.html`、`config.html`、`login.html`、`debug.html`、`ethernet.html`
- 数据文件（前端只读）：`devices.json`、`links.json`、`topology.json`

## 快速开始
- 依赖：
  - Go ≥ 1.20
  - Python ≥ 3.8，且安装 `requests`
- 启动后端：
  - `go run server/main.go`
  - 浏览器访问 `http://localhost:8181/`
- 可用环境变量：
  - `PORT` 默认 `8181`
  - `WEB_ROOT` 静态资源根目录，默认 `..`（项目根）
  - `CONFIG_DIR` 配置目录，默认 `../config`
  - `JWT_SECRET` JWT 签名密钥，默认开发值，生产必须覆盖

## 登录与权限
- 在 `config/.users.local` 写入两行：
  - 第1行用户名
  - 第2行密码
- `POST /api/login` 获取 `token` 后在请求头加入 `Authorization: Bearer <token>` 即可访问受保护接口。

## 前端页面
- `topology.html` 可视化拓扑，支持图例、故障高亮、管理员模式与前台编辑工具条。
- `config.html` 简化的配置查看/编辑入口（通过后端 API）。
- `login.html` 管理员登录界面。

## 配置文件说明
- `config/group_rules.json` 分组与样式覆盖规则。
- `config/topology_config.json` 或 `config/topology.config.json` 全局样式、布局与交互参数。
- `config/positions.json` 节点/组坐标与锁定，用于拖拽保存布局。
- `config/link_overrides.json` 连线黑白名单，仅影响显隐。
- 页面在缺失配置时提供默认包的下载入口，放置到 `config/` 即生效。

## 数据源与采集
- `devices.json` 设备名列表（由采集维护）。
- `links.json` 设备间连线（由 LLDP 采集生成）。
- `topology.json` 设备端口速率与流量聚合（由 SNMP/Prometheus 采集生成）。
- 采集脚本：
  - 编辑 `cmd/snmp.py` 中的 `PROM_URL`，运行：
    - `python3 cmd/snmp.py`
    - 输出更新 `topology.json`
  - 编辑 `cmd/lldp.go` 中的 `promURL`，运行：
    - `go run cmd/lldp.go`
    - 输出 `links-raw.json`（原始）与过滤后的 `links.json`，并增量追加 `devices.json`
- 可参考 `examples/snmp.service` 与 `examples/snmp.timer` 将采集任务以 systemd 定时运行。

## 后端 API
- `GET /api/health` 服务健康检查
- `POST /api/login` 登录，读取 `config/.users.local` 生成 JWT
- `GET /api/config/:name` 读取配置
  - `name ∈ {group_rules, topology_config, positions, link_overrides}`
- `PUT /api/config/:name` 写入配置（需 `Bearer`），支持内容未变时的无操作返回
- `GET /api/history/:name` 查看历史版本列表
- `POST /api/history/:name/backup` 从当前配置创建快照
- `POST /api/history/:name/rollback` 按时间戳回滚配置
- 静态与数据：
  - `/web` 映射到 `WEB_ROOT`
  - `/icons` 映射到 `WEB_ROOT/icons`
  - `/topology` 映射到 `WEB_ROOT`（便于相对路径加载）
  - `/devices.json`、`/links.json`、`/topology.json`、`/prometheus.json` 若不存在则返回空结构

## 部署建议
- 设置环境变量强化安全与路径：
  - `JWT_SECRET` 使用强随机字符串
  - `WEB_ROOT` 指向静态资源所在目录
  - `CONFIG_DIR` 指向配置持久化目录（确保可写）
- 通过反向代理暴露 `8181` 端口，开启 TLS。
- 将采集脚本按需写入定时任务，保证数据文件持续更新。

## 开发问题记录
- 前端使用 `vis-network`，图标资源走 `/icons/*`，缓存头已优化。
- 管理员模式支持在线检查缺失配置与下载默认包；前台编辑可在无后端写入时使用本地缓存并导出 JSON。
- 需要定制风格与图标时可修改 `config/topology_config.json` 的 `default_icons` 与布局相关字段。