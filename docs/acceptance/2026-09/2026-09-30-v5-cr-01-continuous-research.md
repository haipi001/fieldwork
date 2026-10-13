# V5-CR-01 Continuous Research Engine

日期：2026-09-30
版本：Fieldwork 0.52.0（Build 61 / Schema 23）

V5 Continuous Research 使用显式启用的 Campaign 策略、定时或手动触发、原子 bounded tick 和 Research Checkpoint。每次 tick 读取当前图谱，计算 Observation/Claim 增量及矛盾/验证缺口，最多排入策略规定数量的声明式 AgentTask；上限未排入的缺口保留供后续 tick 处理。Checkpoint 保存图谱摘要、计数、ID 状态和实际用量差额；重复 tick 不重复排队。现有应用调度循环每 30 秒检查到期 Campaign，默认未启用时不工作。

所有连续研究任务只路由到本地 Runner，`max_cost_micros=0`，不授予工具能力，也不授权外部目标动作。配置与 tick 均要求活动 Campaign、已确认的当前 Scope 和 Policy；领取、心跳/完成及 Runtime 路由再次校验 Scope/Policy。更换 Scope/Policy 时旧任务作废、后续 tick 才能按新边界重排；禁用策略取消未完成的连续任务。每日实际用量预算、空闲门禁、最小间隔及单次任务上限均可阻止过量调度。

云端升级尚无受信任的自动执行链，策略配置会明确拒绝 `cloud_escalation=true`，不会静默切换云端。tick 只排入审查/验证准备任务，不直接扫描外部目标、不自动签发 Verification Receipt 或 Canonical Result；真实 Runner 执行及云升级仍受后续编排/运行时门禁约束。

验收覆盖 API 启用/禁用、到期 tick、任务上限与积压、Checkpoint 增量/幂等、预算/空闲、Scope 变化后的领取与路由阻断。Schema 不变；回滚移除路由和调度调用，并停用 Campaign 策略，旧图谱和任务记录保留。

验证结果：全量测试 `600 passed, 3 skipped`；Python 编译、`git diff --check` 与未安装 macOS bundle 的 deep/strict 签名校验通过。未启用真实 Campaign 的连续策略，未执行外部目标动作或覆盖安装版。

下一任务：`V5-INTEL-01` Vulnerability Intelligence core。
