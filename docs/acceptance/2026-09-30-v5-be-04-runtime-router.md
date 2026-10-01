# V5-BE-04 Runtime Router 验收

日期：2026-09-30
版本：Fieldwork 0.47.0（Build 56 / Schema 23）

## 交付范围

- Provider Registry：local/cloud 明确分类、模型与成本元数据、健康状态、优先级、独立验证能力和启停状态。
- Provider Secret：SQLite 只保存 opaque ref；值保存在 owner-only `0700` 目录和 `0600` 原子文件中。API、事件、配置、状态和 Route Decision 均不返回 secret 或 secret ref。
- Immutable Runtime Profile：支持 `cloud`、`local`、`hybrid`、`offline`，冻结 provider 集合、fallback、复杂度阈值、token 与费用上限。
- Route Decision：结合 profile、任务上下文敏感性、复杂度、独立性、Provider 健康、Provider context、Campaign/Task 预算与已用费用，持久化不可变决策。
- Usage Ledger：实际 token/费用通过幂等报告写入不可变账本；同键变载荷拒绝，后续路由会扣除 Campaign 已用费用。
- ResearchGroup 可绑定 Runtime Profile，组内 AgentTask 自动继承且不能在路由请求中覆盖。
- `/api/v1/runtime/status` 与 readiness 保留原有本机工具状态，同时增加 `ai_runtime` 快照。

## 路由语义

- `offline`：仅健康本地 Provider；没有本地 Provider 时阻塞。
- `local`：仅本地 Provider。
- `cloud`：优先云端；只有 profile 允许时才可回退本地。
- `hybrid`：低复杂度/无云预算 local-first，高复杂度在预算内 cloud-first，健康异常按 profile 回退。
- `private/secret`：无论请求模式如何都只能本地；本地不可用时失败关闭。
- `requires_independence`：只能选择声明 independence capability 的 Provider；云预算不足时只能回退本地独立 Provider，绝不回退普通模型。

Provider 未执行健康检查或健康状态不是 `healthy` 时不进入路由候选。Cloud endpoint 必须是公共 HTTPS；实际健康检查在 DNS 解析后再次拒绝私网/环回地址、环境代理和重定向。Local endpoint 必须是带显式端口的 loopback。

## 数据库迁移

V5 内部 schema 3→4，产品 Schema 22→23，纯增量增加：

- `runtime_route_decisions`
- `runtime_usage_reports`
- Route Decision、Usage、Usage Report 和 Runtime Profile 不可变触发器

既有 `runtime_profiles`、`runtime_providers`、`runtime_usage` 列合同保持不变。

## 自动化验收

`tests/test_v5_runtime.py` 覆盖：

- Secret 只存在于权限正确的后端文件，轮换/清除不回显；
- cloud/local/hybrid/offline 四模式；
- Provider unavailable fallback 与 cloud budget stop；
- 明示及任务上下文派生的敏感性只走本地；
- 独立验证不降级到普通 Provider；
- Usage 幂等、不可变和跨决策预算扣减；
- URL/Provider 分类/Profile 合同失败关闭；
- ResearchGroup Profile 继承与覆盖拒绝。

## 回滚

回滚应用时恢复该版本启动前自动创建并校验的 Schema 22 备份。Provider secret 文件不属于 SQLite 备份；回滚或卸载时按 opaque ref 清理，禁止把其内容复制进日志、证据或报告。

## 下一任务

`V5-FE-01C`：把 Graph、Orchestrator、Runtime 与 Receipt 页面切换到已实现的真实 Overlay API，并保持未接能力的明确空态。
