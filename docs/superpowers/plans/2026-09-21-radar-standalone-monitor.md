# Radar 独立监控页实施计划

## 1. Radar 工件和契约

- 将监控快照构建器和报告校验器纳入 Radar；
- 让报告路径支持 Radar `data/` 前缀；
- 在 workflow 成功和失败分支都复制监控工件到 Radar `data/`；
- 通过 Radar 自己的契约校验工件。

## 2. Radar 独立页面

- 新增 `monitor/index.html`、`monitor/monitor.css`、`monitor/monitor.js`；
- 页面只读 `/data/radar-monitor.json`、`/data/radar-update-report.json` 和 `/data/radar-reports/index.json`；
- 手动执行按钮只打开 Radar GitHub Actions workflow；
- 页面自动按固定间隔刷新已提交的 Radar 工件。

## 3. Way 边界清理

- 删除 Way 的 Radar 监控页面；
- 删除 Way 的 Radar workflow dispatch、本地 runner 和相关配置；
- 删除 `/api/admin/radar/monitor` 与 `/api/admin/radar/run`；
- 将管理后台入口改为 Radar 外链；
- 不在 C 端前沿页面嵌入监控控制面。

## 4. 验证

- Radar 页面契约测试；
- Radar 快照路径和脱敏测试；
- workflow 关键步骤测试；
- Way 消费边界测试；
- JavaScript 语法检查；
- Radar 全量测试；
- Way 后端 focused 测试。
