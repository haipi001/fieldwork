# V5-ORCH-02：限定正式Finding与报告证明桥接

日期2026-10-04；源码0.68.6/build84/schema25。整体Task及Master Bundle目标继续进行中。

## 本增量与变更文件

- `v5_http_receipts.py`：由有效、当前、positive的`http_object_read_v1`回执构造只读正式结果计划。计划固定为所选对象读取的实际两轮结果，严重度unknown、源码实现未检查、影响仅为实际响应字节与冻结规则违反。不让调用者填写成功、严重度或更广影响替代机器证据。显式确认和计划指纹一致后复用现有正式Finding门与根因生命周期；同一回执重复保存返回同一Finding。
- `v5_verification.py`：GET回执的`http-finding-plan`不发请求；POST`promote-http`要求authorized=true及64位计划指纹。refuted/inconclusive、旧输入、错误候选/证据/Oracle不能晋升。
- `verification_receipts.py`：机器收据额外绑定V5不可变回执ID；签发与正式Finding事务中再次核验当前输入、独立进程证明、positive结论以及候选、Artifact和Oracle匹配。不改变旧调用的证明模型字段。
- `final_core.py`：报告证明附件保留V5回执完整载荷，导出时复查V5绑定；材料变化后不能继续导出该证明包。旧HTTP的已有业务权限门保持。
- `guided_research.py` / `static/v5-candidate-workflow.js`：显示持久正式Finding关联；有效positive回执可进入单独审阅对话框，说明限定影响和unknown严重度；确认后保存，失败保留错误及关闭路径。未通过当前回执门时不显示晋升按钮。
- `tests/test_v5_http_receipts.py` / `scripts/check_v5_http_workflow_ui.py`：真实HTTP正例、保存确认/指纹门、幂等、不增HTTP请求、报告预览、V5证明附件、材料变化后拒绝导出及修复负例不可晋升。
- `version.py` / 当前进度文档 / 本文：版本、验收与剩余范围。

## 验证、迁移与基准

专项正式Finding路径通过：本地采集3次、隔离复验10次，此后的计划、晋升、重复保存、报告预览和附件构造不增目标请求。refuted修复样本计划返回409。隔离UI审阅、确认保存、持久关联和回执失效检查通过。最终全量725 passed/3 skipped，145.69秒；1项已有Starlette/AnyIO依赖弃用提示。补充专项7 passed，覆盖正式结果报告预览与三轮修复负例不可晋升。

无schema迁移、无生产数据修改、无外部扫描、未覆盖安装App。新增Finding操作增加本地SQLite/文件哈希检查，无目标请求或模型调用成本。没有新增发现精度、模型8/16/32角色或规模基准结论。

## 剩余边界与下一Task

正式Finding仅确认所选对象读取违反有依据的冻结权限规则；unknown严重度不自动填充，更广数据访问和源码根因仍未证明。计划字段不可用来宣称任意漏洞或自动发现；历史回执不是部署一直相同的保证。报告完整性门继续要求审阅，保存Finding不自动发送平台报告。

旧HTTP finalize=True仍保留历史进程内执行路径，本增量未将所有旧Oracle迁入独立V5流程；全计划独立确认门仍需覆盖此路径。

本轮尚未把V5 refuted回执接到定向复测的verified_fixed生命周期，也未完成修复后重现、真实发现链、外部目标隔离器、中断材料和完整App冷启动。继续V5-ORCH-02补V5回执与复测谱系，再按Master Bundle全部任务和验收门推进。

## 回滚

停止活跃操作后可逆向本增量Git提交恢复0.68.5源码，无schema降级。保留已签发收据及Finding；回滚代码不能验证额外V5绑定时不得继续将该绑定当成当前有效的晋升证明。
