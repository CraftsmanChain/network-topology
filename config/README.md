# config 目录说明

该目录存放单一架构视图运行所需的配置与备份：

- `group_rules.json`：分组规则源（include/exclude 支持正则与 ID 列表）。
- `architecture_config.json`：架构视图分区、集群、角色、布局行与刷新周期配置。
- `topology_config.json`：全局样式与布局配置（背景色、侧栏模块、刷新策略、页面标题等）。

当前后台只维护以上 3 份配置。

其他数据源：
- `devices.json`（实时设备列表，前端只读）。
- `links.json`（实时连线数据，前端只读）。

字段要点：
- `group_rules.json`
  - `groups[].include_regex/ids`、`exclude_regex/ids` 可组合使用。
  - `style_override.forcedStatus` 可为 `"UP"` / `"DOWN"` / `null`；颜色与 `icon_url` 可按需覆盖。
- `architecture_config.json`
  - `zones[]` 配置分区，`clusters[]` 配置集群，`roles[]` 配置汇聚/接入等角色。
  - `include_ids/exclude_ids` 精确匹配设备或组 ID，`include_regex/exclude_regex` 正则匹配 ID 与显示名。
  - `layout.rows` 控制架构视图分区排布；`refresh_ms` 控制前端自动刷新周期。
- `topology_config.json`
  - `title` 会同步到页面主标题与浏览器 `document.title`。
  - `dynamic_links.debounce_ms` 与 `grace_ms` 控制连线的防抖与宽限期。
