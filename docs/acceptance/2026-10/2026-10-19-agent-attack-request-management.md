# 智能体攻击测试申请管理（部分交付）

日期：2026-10-19。完整UI-04及原15Task/8KnownGaps继续开放。

## 当前问题与本轮实现

原攻击测试页只有不能保存的预览表单。登记的agent_registry_v5身份保存声明，不具备可执行适配器；既有本地策略回归没有攻击该身份。

新增 `/api/v1/workspace/agent-attack-requests/preview`、申请保存与取消接口，并将原预览入口接到实际API：

- 选择登记身份、提示注入/工具边界/记忆污染类别及1–100最大用例数。
- 预检返回身份与组件快照SHA、固定阻塞原因、零调用及agent_tested=false。
- 明确确认后保存 reviewed_request / awaiting_adapter 记录和事件；不建立执行任务或授予能力。
- 保存事务重新核对快照；变化409要求重新预检。严格类型/范围、请求键内容冲突、幂等重试与取消已覆盖。
- 修改表单使旧预检失效；等待适配器状态、阻塞与取消可在页面查看。原测试记录、独立评估、本地策略回归及其他页面保留。

这是申请管理的真实持久化，不是主动攻击派发完成。没有通过模拟策略函数、注册信息或模型Profile代替实际受测Agent行为。

## 验收

- build/attack-requests-regression-current-20261019.log：62 passed，包含新请求生命周期/无任务、无模型调用、无Run/回执/授权副作用；目标或组件变更拒绝；缺失目标/严格预算/类别/确认/幂等冲突及原工作台测试。
- 首轮测试错误引用不存在的verification_receipts表，1 failed/61 passed；已修正为实际verification_receipts_v5，失败日志保留 build/attack-requests-regression-20261019.log，不改产品门。
- build/attack-requests-workspace-ui-20261019.log：隔离真实API UI完成预检→修改用例数撤销预检→重预检→保存→取消；随后原18页、5页签、144显示组合及原交互通过，0模型调用。
- node --check static/v5-workspace.js、git diff --check通过。五文件摘要保存在 build/attack-requests-source-sha-20261019.json。
- 上一轮1491全量通过早于本轮改动，不能作为本轮完整回归结果。

## 下一实现与边界

继续实现明确受测Agent的调用适配器、当前项目/授权范围与能力、时间/请求/费用预算、隔离运行/取消/恢复、原始输出及反证、独立判定与回执。接通后必须重新审阅执行范围，已保存申请不会自动运行。当前最大用例数不是已执行预算或模型费用上限。

原生App仍待Mac解锁后重启加载本轮源码并验收；本轮未操作主库研究数据、未付费模型调用、未提交推送Git。完整规划未完成。
