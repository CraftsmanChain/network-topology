# config 目录说明

该目录存放管理员模式可编辑的 JSON 配置文件示例：

- group_rules.json：分组规则源（include/exclude 支持正则与 ID 列表）。
- topology.config.json：全局样式与布局配置（背景色、侧栏模块、刷新策略等）。
- positions.json：节点与组的坐标、锁定与尺寸（用于拖拽保存布局）。
- link_overrides.json：连线黑白名单（覆盖动态连线的显隐）。

其他数据源：
- devices.json（实时设备列表，前端只读）。
- links.json（实时连线数据，前端只读）。
- .users.local（明文用户名与密码，2 行；存放于 config/ 目录：`config/.users.local`）。
  - 第1行：用户名；第2行：密码。
  - 示例：
    - admin\n
    - 123456

字段要点：
- group_rules.json：
  - groups[].include_regex/ids、exclude_regex/ids 组合使用；冲突设备需在 UI 中提示与处理。
  - style_override.forcedStatus 可为 "UP"/"DOWN"/null；颜色与 icon_url 可按需覆盖。
- topology.config.json：
  - backgroundColor 与现有页面风格一致，示例为 #1f2637。
  - dynamic_links.debounce_ms 与 grace_ms 控制连线的防抖与宽限期。
- positions.json：
  - nodes 与 groups 的坐标与锁定只影响布局，不改变数据源。
- link_overrides.json：
  - whitelist 固定显示、blacklist 固定隐藏；仅影响显隐，不改变样式。

tips：
- 编辑完成后可通过浏览器导出 JSON 文件并替换服务器静态文件。
- 为保证性能，正则匹配建议在 Web Worker 中进行、预览分页显示。