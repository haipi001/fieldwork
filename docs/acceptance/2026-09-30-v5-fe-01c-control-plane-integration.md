# V5-FE-01C Control Plane Integration 验收

日期：2026-09-30
版本：Fieldwork 0.48.0（Build 57 / Schema 23）

## 交付范围

- Research Graph 在现有 Engagement 视图内，按持久化 Research Campaign 读取 `/api/v1/research/campaigns/{id}/graph`，合并 V5 Node/Edge 并保留 Campaign 边界。
- 增加只读 `/api/v1/orchestration/status` 操作员快照，返回真实 ResearchGroup、AgentTask、Runner 及计数。读取不恢复过期 lease；恢复由已有领取或显式恢复流程处理。
- Agents 页面展示逻辑 AgentTask、Group、Lease、Usage 和 Attempt，不把 Runner 或模型会话伪装成 Agent 实例。
- Runners 页面展示真实 Registry 中的 kind、labels、capabilities、heartbeat、active jobs 和 concurrency limit。
- AI Runtime 页面读取 V5 Runtime Profile / Provider Registry 与 Usage Ledger，同时将旧 Traditional Provider 配置保持为明确的独立通道。
- Verification 页面读取不可变 Receipt Registry，分别显示 hash 完整性与当前输入匹配；不从 Candidate 状态推断回执。
- 取消 Orchestration 和 Verification 的“规划中 404”例外，这些端点失败现在会进入全局连接告警。

## 信任边界

- Graph 仅合并持久化 ID 关系，不自动执行 legacy bridge。
- Receipt 存在不等于当前有效；只有 `integrity.valid` 与 `current_inputs_match` 同时为真时才显示“完整 · 输入匹配”。
- Runtime Route Decision 不等于已执行调用；用量只来自 Usage Ledger。
- 所有页面保留空、失败与过期状态，不填充演示指标。

## 自动化验收

- `tests/test_v5_frontend.py`：Graph V5 API、Runtime Registry / Usage、Receipt Registry 信任状态和真实 Orchestration 字段。
- `tests/test_v5_orchestration.py`：操作员快照的真实 Task / Group / Runner 与计数。
- Playwright 隔离数据库 smoke：400px / 1280px 下打开 Graph、Agents、Runners、Verification、Runtime，无 page error 或水平溢出。

## 迁移与回滚

本任务不变更 Schema，仅增加只读 API 和前端数据源。回滚时恢复 `v5_orchestration.py`、`static/v5.js`、`static/v5-research-graph.js` 和样式文件即可；无需回滚数据库。

## 下一任务

`V5-DOM-01`：开始 Traditional Domain Bridge，将生产数据经显式、幂等、可追溯的 bridge 写入 V5 Research Graph。
