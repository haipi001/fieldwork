# V5-ORCH-03 受约束 Evolver Worker 进展

日期：2026-10-01
版本：Fieldwork 0.60.0（Build 69 / Schema 24）

`POST /api/v1/evolution/populations/{id}/advance` 只对当前、最新一代的已选父代创建任务：每个父代一个 mutation，相邻且 Scope 兼容、无直接矛盾的父代配对一个 combine。任务受组任务预算约束，带有限 Context Capsule、固定父代 ID、Evidence/Counterevidence 引用、当前 Scope/Policy、`structured_evolver` 能力要求和本地路由。重复调用返回同一批任务，不扩大任务数。

显式本地 Worker tick 现在可经已配置的回环模型处理 Evolver；外部本地 Runner 也可领取，但结果必须通过 `POST /api/v1/evolution/tasks/{id}/result` 的结构化合同。服务端复查租约、Runner 能力、父代当前性、模式和父代 ID、Scope、Evidence 子集，并要求继承的全部 Counterevidence 保留。输出需有局限与开放问题，且不能逐字复述父代。通过后只生成 `draft` Claim 和 `derived_from`/`supports`/`contradicts` 图谱边，不生成 Verification Receipt、Canonical Result 或 Finding。普通任务完成端点不能绕过该合同。

`POST /api/v1/evolution/populations/{id}/collect` 仅在该代所有 Evolver 任务成功后，将保留的已选父代与新草案一起交给现有确定性 Evaluator，形成下一代；再次调用幂等。父代 Scope/Policy 或相关图谱变化时，排队任务不可领取，运行中任务不可提交，下一代不可汇集。

边界：本地模型 Provider 需由用户自行配置且健康；无 Provider 时任务保持待执行。模型输出是未信任的研究提案，外部 Runner 自报不获得验证信任。当前尚无 V5 页面展示/操作代际演化，也缺少跨多轮、资源耗尽和应用重启的长期验收；`V5-ORCH-03` 不应宣称整体完成。

验证：全量 `python3 -m pytest -q` 为 634 passed、3 skipped（1 条第三方弃用警告）；Evolution、Structured Worker、Orchestration 专项 25 passed。覆盖重复 advance、组任务预算原子拒绝、普通完成 API 绕过拒绝、缺失反证拒绝、三种 mutation/combine 输出、幂等 Collect、真实本地 Worker tick 的结构化路由、运行中父代图谱变化拒绝。Python 编译和 `git diff --check` 通过；0.60.0 Build 69 未安装 bundle 构建、版本及 deep/strict 签名校验通过。未覆盖 `/Applications/Fieldwork.app`，未操作真实外部目标。
