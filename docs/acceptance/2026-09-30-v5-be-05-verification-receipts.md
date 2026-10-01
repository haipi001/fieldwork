# V5-BE-05 Verification Receipt 验收

日期：2026-09-30
版本：Fieldwork 0.46.0（Build 55 / Schema 22）

## 交付范围

- `POST /api/v1/verification/claims/{claim_id}` 冻结 Claim 与输入 Evidence 节点的规范化 SHA-256，并创建只授予 `independent_verification` 能力的 verifier task。
- `POST /api/v1/verification/tasks/{task_id}/receipt` 是唯一 V5 签发路径。它要求未过期、owner 匹配的 verifier task 租约和已登记独立 Runner；普通任务完成接口不能绕过签发门。
- 收据绑定 Campaign、Claim、Verification Request、verifier task、逻辑 verifier、物理 Runner、Runner-attested 环境、输入哈希、Replay Contract、结果、产出 Evidence、限制和时间。
- 收据规范化 JSON 使用 SHA-256；单独保存不可变 binding。SQLite 触发器拒绝收据和 binding 的 UPDATE/DELETE。
- GET 单条与分页列表都会复算收据 hash/binding，并报告当前输入是否仍与签发快照一致。
- Replay 不把旧结果当作新验证；它创建一个新的独立 verifier task，并引用源收据。
- Receipt 签发具备丢响应幂等：相同 Runner 和完全相同载荷返回原收据，变化载荷返回 `409`。

## 独立性与晋升门

- Runner 必须注册 `independent_verification` capability。
- Claim 若记录 `producer_runner_ref`，同一 Runner 不能自证。
- `verified/refuted` 结果必须满足有效前提、完成 counterevidence 检查、提供机器可读 oracle 和产出 Evidence。
- `invalid_precondition` 与 `interrupted` 只能产生 `inconclusive` 收据。
- Canonical Result 必须声明与收据一致的 `verification_outcome`；只有 `verified/refuted`、未中断且当前输入仍匹配的收据可以人工创建 Canonical Result。
- 收据签发后，绑定的 Claim、输入 Evidence、产出 Evidence 与 Canonical Result 通过 API 不可修改或删除。
- Scanner、Agent 共识、历史 Canonical Finding、无 binding 的数据库行均不能冒充 V5 Receipt。

环境元数据明确标记为 `runner_attested`，不伪装成硬件或系统级远程证明。Replay Contract、环境和结果中的凭据型字段失败关闭。

## 数据库迁移

V5 内部 schema 2→3，产品 Schema 21→22，纯增量增加：

- `verification_requests_v5`
- `verification_receipt_bindings_v5`
- Receipt/Binding 不可变触发器和 Request 查询索引

既有 `verification_receipts_v5` 保持原列合同；没有 request/task/binding 的旧行不能通过 V5 完整性和晋升门。

## 自动化验收

`tests/test_v5_verification.py` 覆盖：

- Positive 收据、hash、幂等重试、分页读取与 Canonical 晋升；
- repaired negative 与 healthy negative 的 refuted 收据，不自动晋升；
- invalid precondition 与 interrupted 的 inconclusive 收据，拒绝晋升；
- 输入证据篡改、自证、普通完成接口绕过与秘密字段拒绝；
- Receipt/Binding 数据库不可变；
- 签发后 Graph 事实不可变与越库篡改后的 stale guard；
- Replay 创建新 task 且重复请求幂等。

## 回滚

回滚应用时恢复该版本启动前自动创建并校验的 Schema 21 备份。不要删除不可变触发器后让旧代码继续写入 Schema 22 数据库。

## 下一任务

`V5-BE-04`：Runtime Router，落实 cloud/local/hybrid/offline、敏感任务本地约束、provider fallback 和 usage accounting。
