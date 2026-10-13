# V5-BE-03 Research Group / AgentTask 编排验收

日期：2026-09-30
版本：Fieldwork 0.45.0（Build 54 / Schema 21）

## 交付范围

- 新增 `ResearchGroup` 与 `AgentTask` API，支持列表、详情、优先级、暂停、恢复、取消和显式重试。
- 新增独立 `Runner` 注册表。逻辑 Agent 角色不再等同于执行进程；Runner 只领取其能力和路由标签都满足的任务。
- 领取使用 SQLite `BEGIN IMMEDIATE` 原子事务，按优先级和创建时间排序；并发领取同一任务只有一个胜者。
- 租约、owner、heartbeat、attempt 和到期时间持久化。进程重启仅回收已到期租约；未到期任务不会被伪造为失败或重复领取。
- 到期租约在剩余尝试次数内重新排队，最后一次到期转为失败；Runner 活跃任务数从事实任务重新计算。
- 任务创建使用全局幂等键；相同请求返回原任务，同键不同载荷返回 `409`。
- Group 支持 `max_tasks`、`max_concurrency`、`max_cost_micros`；Task 支持 token、费用和运行时预算。上报超限结果保存 Usage，但状态为 `budget_exhausted`。
- Checkpoint 与 Usage 独立持久化，完成结果不会自动写入 Research Graph，更不会自动晋升为 Canonical Result。

## 安全边界

编排器只保存和分配声明式任务，不执行 shell、网络请求或目标工具。`tool_grants` 是 Runner 能力匹配条件，不是本模块的执行授权。后续 Worker 实际执行仍必须通过 Scope、Policy、隔离运行器与证据门。

完成接口仅接受当前、未过期租约的 owner；暂停、取消、过期或其他 Runner 都不能提交结果。Route 只接受 `kind` 与字符串标签，未知约束失败关闭。

## 数据库迁移

V5 内部 schema 1→2，产品 Schema 20→21，增量增加：

- `agent_task_usage`
- `agent_task_checkpoints`
- Runner 状态索引与 Checkpoint 查询索引

旧表不重写、不复制，迁移仍由升级前一致性备份和版本回滚机制保护。

## 自动化验收

`tests/test_v5_orchestration.py` 覆盖：

- 幂等重放与冲突；
- 优先级、能力、路由标签与 Runner 并发上限；
- Group/Task 暂停、恢复和取消；
- heartbeat、owner guard、显式重试；
- 租约到期恢复和最大尝试次数；
- Checkpoint/Usage 持久化与预算停止；
- Group 任务/费用预算；
- 双 Runner 并发领取的单一胜者；
- 编排结果不自动生成 ResearchNode。

## 回滚

回滚应用版本后使用启动升级前生成并校验的 Schema 20 备份。新增表为纯增量且不改变 Legacy 数据，但旧版本数据库文件不应与 Schema 21 写入混用。

## 下一任务

`V5-BE-05`：实现不可变 Verification Receipt、输入/环境/Runner 绑定、重放契约与 Canonical Result 完整验证门。
