# V5-ORCH-02：判定期间取消与完整材料重启恢复

日期2026-10-04；源码0.68.12/build90/schema25。

## 变更文件

v5_verification.py、v5_http_receipts.py、traditional_runtime.py、tests/test_v5_local_verifier.py、tests/test_v5_http_receipts.py、tests/test_http_business_boundary.py、version.py、当前进度/规划审计及本文。

## 实现

独立判定监督器在创建子进程前、每个200ms通信轮询、返回结果后检查当前任务/租约与输入。检查源任务取消标志、租约所有者/截止时间、当前claim与输入节点摘要、Scope及领域专用源材料。仍保留原25秒总截止时间、输入64KiB/输出8KiB门和OS文件/网络拒绝证明。

停止时finally终止并回收仍活着的子进程，不提交回执。LocalVerificationStopped只记录cancelled/lease_lost/inputs_changed三个固定原因：源取消使仍归本监督器所有的任务cancelled，当前输入失效等使任务failed；已被其它租约持有者接管的任务不由旧监督器覆盖。重新计算runner active_jobs。普通子进程故障仍执行既有有界重试。

传统来源任务的interrupted状态现在可参与纯判定恢复，但仍必须未取消、10个完整响应、末检查点指向完整http.replay Artifact、文件SHA/路径/角色/Scope/Policy/原证据等全部当前。部分检查点、cancelled任务或失效材料不能作为恢复证明。没有自动重新发送HTTP。取消发生在传统判定期间时，父入口返回取消状态并保留10响应检查点与V5 stopped原因。

## 直接验证

最终专项25 passed（25.09秒）：

- 注入只读取stdin并等待30秒的脚本，实际通过sandbox-exec/Python启动；350ms后取消，3秒内结束，实际Popen已回收且负返回码。这个测试验证真实监督器停止/回收，不把注入脚本当成可信Oracle结果。
- 本地对象读取完成10个响应后，在真实等待中的判定沙箱运行期间通过取消API请求停止。传统任务cancelled、V5任务cancelled、零回执，检查点保留10响应。取消辅助线程已join，无目标请求重放。
- Oracle故障保存完整任务，模拟旧running记录后调用实际init_final_db启动恢复函数，得到interrupted且result原样保留；V5纯语义重试签发有效回执，目标HTTP仍10次。这是恢复函数/真实判定验收，不是安装App冷启动或实际主进程SIGKILL实验。
- 既有本地隔离/负例/范围守卫仍通过；修改判定后原证据时再次检查，不能签回执。测试替身已适配监督器的可选当前状态回调。

最终全量743 passed / 3 skipped（183.69秒），仅既有Starlette/AnyIO弃用警告。轮询增加短时DB与材料哈希读取，没有目标HTTP、模型成本或发现精度基准结论。未将历史25秒等待时间当作新的取消延迟承诺；200ms是轮询间隔，实际延迟还包含DB/文件读取和进程回收。

## 迁移、回滚与剩余范围

无DDL/schema迁移、生产项目改动、外部目标扫描或App覆盖安装；安装检查点仍0.68.2。逆向本提交恢复0.68.11；旧版仍保留新材料，但取消需等判定返回，interrupted传统任务不能直接重新判定。

下一Task仍V5-ORCH-02：签发后新增证据全集/反证的失效守卫、实际主进程退出/安装冷启动验收、真实发现与跨域独立验证。15 Task/8 Known Gaps的完整直接验收、情报源治理/引用固定、模型与规模基准、发布安装门仍未完成；整体目标保持进行中。
