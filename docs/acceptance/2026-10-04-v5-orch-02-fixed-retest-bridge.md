# V5-ORCH-02：独立修复回执与定向复测谱系

日期2026-10-04；源码0.68.7/build85/schema25。整体开发目标继续进行中。

## 本增量

- `v5_http_receipts.py`：refuted且classification=repaired_negative的有效、当前独立回执才可进入修复计划。必须绑定新Run的定向复测候选、原正式Finding和原positive独立证明；精确对象、类别、项目、所有者摘要、读取主体摘要及业务规则摘要相同。新负例必须晚于最近positive证明，不能用旧修复回执关闭之后的重现。共享反例、不适用身份或不同对象不能确认修复。
- `v5_verification.py`：只读`http-fixed-plan`与显式authorized=true/计划指纹确认的`confirm-http-fixed`接口；不发送目标请求。
- `verification_receipts.py`：在写事务内复查V5谱系，然后复用既有负向机器收据与verified_fixed生命周期。负收据记录V5回执ID；V5正/负收据签发加写事务锁，保护输入检查至写入阶段。重复确认返回同一负收据，不新增请求。
- `guided_research.py` / `static/v5-candidate-workflow.js`：只有可绑定原Finding的修复负例显示审阅入口；确认后显示持久修复状态。对话框说明仅确认所选对象、无目标请求，要求显式确认。
- `tests/test_v5_http_receipts.py` / `scripts/check_v5_http_workflow_ui.py`：真实HTTP正例→正式Finding→新Run定向复测→两轮拒绝→独立负回执→verified_fixed、幂等；更换所有者或读取主体拒绝关闭原结果；页面审阅/确认/持久状态。
- `version.py` / 当前进度文档 / 本文：版本、验收及剩余工作。

## 验证与迁移

全量726 passed/3 skipped，148.11秒；后续增加两个错身份场景，专项3 passed；最终当前文件专项18 passed（含后加入的两个错身份场景），32.38秒；全量726为加入该两项前的基线结果，没有把新增项算作已跑全量。已有Starlette/AnyIO一项依赖弃用提示。隔离UI检查通过，覆盖正式保存与修复确认。

同一实际本地服务，正例采集3+复验10，再在新Run采集3+复验10；全部后续只读计划、明确确认、重复确认共不增加HTTP，总26次。只有绑定原攻击主体的拒绝负例使生命周期变为verified_fixed；两个错身份场景保持retest_required，不签发机器负收据。

无schema迁移，无生产数据改动，无外部扫描，无App覆盖安装。安装检查点仍0.68.2。此为临时数据库/本地服务验收，不是安装版冷启动或真实模型发现验收。没有新增目标或模型成本；没有新发现精度、角色数量或规模基准结论。

## 回滚与下一任务

停止活跃任务后可逆向本次Git提交恢复0.68.6源码，保留正/负收据与历史，不需要schema降级。旧代码不支持V5修复接口时不可重新解释新负收据为未绑定的人工修复确认。

V5-ORCH-02还需完善中断材料、重现/复测更广谱系与所有旧HTTP进程内路径迁移。随后回到完整Master Bundle要求核对，推进真实发现、外部隔离执行器、三域桥接、持续研究、情报治理、Critic/Synth及规模/安装验收；不能用本地正反例和测试数量宣称全目标完成。
