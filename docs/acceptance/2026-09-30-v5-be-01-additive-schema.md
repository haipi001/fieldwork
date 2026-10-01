# V5-BE-01 Additive V5 Schema

日期：2026-09-30

## 范围

本任务只建立 V5 存储基线，不创建 Graph API、不桥接历史事实、不启动 Agent、不运行历史目标，也不把任何 Candidate 自动晋升为 Canonical Result。

新增表覆盖：

- Runtime Profile / Provider / Usage；
- Research Node / Edge / Group / Checkpoint；
- Agent Task、Runner Registry、V5 Event；
- Verification Receipt V5；
- Continuous Research State；
- Intelligence Record / Match；
- Agent Registry / Memory / Supply Chain / Attack Test；
- 独立 `v5_schema_meta`。

所有表都在现有表旁增量创建，没有 `DROP`、`ALTER` 或历史行更新。V5 receipt 使用 `verification_receipts_v5`，避免与当前验证事实链混淆。

## 迁移性质

- 全部 DDL 在单个 `BEGIN IMMEDIATE` 事务执行；任一语句失败时回滚所有本轮对象。
- 迁移幂等；重复执行不会改变现有行。
- App Schema 从 19 升至 20，启动前由现有 lifecycle 创建 SQLite 在线备份并校验 SHA-256/完整性。
- App 只有在既有初始化和 V5 DDL 成功后才写入最终 schema version。
- 历史 Candidate、Finding、Run、Evidence、Campaign 不被复制或改写；桥接留给 `V5-BE-02`。

## 生产数据库副本演练

对当前 `data/src_control.db` 使用 SQLite backup API 创建临时一致性副本，并在副本完成迁移与回滚：

- 原数据库 integrity：true；
- 迁移副本：Schema 19 → 20；
- 迁移前备份：有效；
- 70 个原有业务表行数全部不变；
- `research_nodes`、`research_edges`、`verification_receipts_v5`、`agent_tasks` 均为 0；
- 从迁移前备份恢复的数据库：Schema 19、integrity true、不含 V5 表。

临时副本已自动清理；正式数据库未在本轮演练中修改。

## 自动化验证

- 增量保留与零自动晋升；
- 幂等重跑；
- 注入无效 DDL 后完整事务回滚；
- 备份可作为有效回滚源。

## 回滚

若正式启动迁移失败，应用不会更新 `PRAGMA user_version`。恢复时使用 `data/backups/` 中对应 `pre-schema-19-to-20` 的有效备份；同一版本不删除旧表。

## 下一任务

`V5-BE-02`：Research Graph repository/service。必须验证 node/edge CRUD、旧事实 provenance bridge 与 Canonical Result receipt guard，且桥接幂等、不复制二进制 Artifact。
