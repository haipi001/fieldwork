# V5-BE-02 Research Graph Repository / Service

日期：2026-09-30

## 已实现

- Research Node：创建、读取、更新、受约束删除。
- Research Edge：创建、读取、更新、删除；同 Campaign 同关系幂等。
- Graph：Campaign 级分页读取，返回节点/边总数与 `has_more`；当前页只返回两端都在本页的边，避免悬空引用。
- Campaign 与 Run 归属检查，拒绝跨 Campaign Edge 和跨 Engagement Run 绑定。
- 每项写操作进入 `v5_events`，但不启动任何 Runner 或目标请求。

## Canonical Result 门禁

创建或更新 `canonical_result` 必须同时满足：

1. `source_ref` 指向同 Campaign 的 `claim`；
2. `attributes.verification_receipt_id` 存在；
3. Receipt 属于同 Campaign 且绑定同一 Claim。

Canonical Result 和已经被 Receipt 引用的 Claim 不可通过普通删除接口移除。Receipt 哈希、独立上下文和 replay 合同的完整签发/重验属于 `V5-BE-05`，本任务不提前伪造。

## 显式 Provenance Bridge

`POST /api/v1/research/campaigns/{id}/bridge` 是显式、幂等的只读投影动作：

- Entity → `entity`；
- Observation → `observation`；
- Evidence / Counterevidence → 对应一等节点；
- Research Hypothesis → `hypothesis`；
- Campaign 链接的 Candidate → `claim`；
- Artifact → 只保存来源 ID、哈希、媒体类型、脱敏状态，不复制 URI 或二进制内容；
- Existing Canonical Finding → `legacy_verified_claim`，明确 `canonical_result_eligible=false`，不会绕过 V5 Receipt 门禁；
- Existing Relationship → 支持的关系类型，未知类型降为 `related_to` 并保留原类型属性。

稳定 ID 由 Campaign、source type 和 source ref 派生。重复桥接不会新增 Node/Edge，也不覆盖人工创建或已投影内容。

## 验证覆盖

- Node/Edge CRUD、重复 Edge 幂等；
- 跨 Campaign Edge 拒绝；
- Graph 分页和删除依赖门禁；
- Receipt 缺失、跨 Campaign Receipt 拒绝，正确 Claim/Receipt 组合通过；
- Legacy bridge 第二次执行新增数为 0；
- Artifact 私有 URI 不进入 Graph；
- Legacy Canonical Finding 不产生 `canonical_result`。

本任务未迁移正式数据库、未调用外部目标、未启动 Agent 或验证任务。

## 下一任务

`V5-BE-03`：ResearchGroup、AgentTask、Lease/Heartbeat、重试、暂停/恢复/取消、并发与预算控制。真实工具执行仍必须经过既有 Scope/Policy 与隔离边界。
