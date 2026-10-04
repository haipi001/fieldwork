# V5-ORCH-02：传统HTTP统一V5任务、回执与修复谱系

日期2026-10-04；源码0.68.11/build89/schema25。

## 变更文件

traditional_runtime.py、v5_http_receipts.py、tests/test_http_business_boundary.py、tests/test_final.py、version.py、当前进度/完整规划审计及本文。

## 实现与授权

传统同步正式对象读取复验创建持久verification_job，后台入口复用其既有记录；请求仍由已授权连接代理的沙箱发送。已完成两轮10个响应的完整Artifact和检查点引用进入V5现有claim/artifact图、验证请求、持久agent_task/租约、独立纯语义沙箱与不可变V5回执。合同明确verification_job_id，不伪装为guided_job_id；传统原始候选证据/Observation、Scope/Policy行、字段名和候选指纹在请求前捕获，无请求凭据或原始请求头进入快照。

replay_input严格核对来源任务、Run/Candidate、完整响应数、最终Artifact引用、来源生产者、文件位置/O_NOFOLLOW/大小/SHA、图输入、Scope/Policy、原证据行及实际传输证明。检查点kind本身不能进入这一协议。guided合同保留原核验，并兼容此前没有producer_kind的历史guided材料。

正式positive结果复用finding_plan/promote_finding，机器收据绑定V5回执；严重度unknown，影响仅限实际所选对象读取，源码根因未检查。旧body中的人工严重度/影响/根因保存在脱敏review_notes并标记machine_verified=false，不进入机器确认的根因或严重度。报告附件包含proof/v5-verification.json。

repaired_negative复用fixed_plan/confirm_fixed：精确旧Finding/对象/类别/项目、最近positive时间线、原所有者/读取主体/业务规则摘要及定向复测计划均需一致。传统路径不再使用仅拒绝状态码的旧修复签发分支。

新增发送前显式allow_authentication与认证域名门。正式请求及等待期间还核对项目/Run状态、Scope/Policy、候选指纹和整个原始证据快照，归档、原证据变化或新增引用停止执行。

## 失败、重试与限制

判定失败不降级为进程内成功；保留完整Artifact、末检查点和V5请求/任务编号。传统failed任务仅在未取消、确有完整10响应/final_artifact_id、全部材料和授权仍当前时，允许现有V5任务重新评估已记录响应，不再发送HTTP。它不会因重试自动晋升传统Finding，仍需既有显式结果计划/确认。

同步入口也持久记录completed/failed终态。cancelled任务不可重试其证明。应用启动把旧running任务标interrupted；当前传统来源门尚未允许interrupted完整任务参与纯判定重试，因此重启后这条恢复链还需补齐。独立判定仍是既有25秒截止时间，取消在判定返回后阻断签发，期间及时取消尚未补齐。

执行期间原证据快照已核对，但签发后新增证据全集的失效规则还需统一：已有V5守卫验证原始引用不变，不能把允许附加证据解释为已完整证明所有新反证没有冲突。历史不含V5证明的正式Finding不能直接通过当前fixed_plan关闭，需先获得可信V5正例谱系。

## 验证与基准

- 业务边界/V5回执/工作流专项54 passed（61.62秒）；旧入口正例/导出/复测额外专项3 passed（13.72秒）。三种旧入口现场：原主体一致、读取主体更换、所有者更换；后两者拒绝关闭旧Finding、保持retest_required且不签负收据，恢复原主体后可确认。
- 同步正例机器收据关联真实V5 receipt，任务succeeded，机器严重度unknown，人工high备注未验证。后台正例与同步修复均走同一账本，报告导出含V5证明。
- 未允许认证零请求；首响应后原证据变化/项目归档/策略变化各只发一次；Oracle失败后V5语义重试得到可信回执，目标HTTP仍10次。
- 最终全量740 passed / 3 skipped（173.46秒），仅既有Starlette/AnyIO弃用警告。

没有额外生产目标HTTP、模型发现或成本基准结论。统一路径替代此前单独pure confirmation，不重复调用两个语义Oracle。fixture账号/候选是给定输入，不是真实自主发现或安装版冷启动。无DDL迁移、生产数据修改或App覆盖安装，安装检查点仍0.68.2。

## 回滚与下一Task

逆向本提交恢复0.68.10；数据库无DDL变化，但旧版不识别verification_job_id合同的新V5回执，不能改标签伪装成guided合同或声称旧版有新谱系守卫。

下一Task仍V5-ORCH-02：完整任务重启恢复、判定期间取消、签发后证据全集守卫，继而真实发现与跨域独立验证。完整15 Task/8 Known Gaps的直接验收继续，整体目标未完成。
