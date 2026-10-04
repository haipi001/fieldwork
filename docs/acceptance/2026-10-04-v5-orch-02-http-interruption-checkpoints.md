# V5-ORCH-02：HTTP中断材料与逐响应持久检查点

日期2026-10-04；源码0.68.10/build88/schema25。

## 变更文件

http_replay_checkpoints.py、traditional_runtime.py、guided_http.py、guided_research.py、static/v5-candidate-workflow.js、tests/test_v5_http_workflow.py、tests/test_final.py、tests/test_candidate_workflow.py、tests/test_guided_http.py、tests/test_guided_verification_queue.py、version.py、当前进度/规划审计及本验收文件。

## 行为与数据

每个返回到监督器的HTTP响应在进入下一请求或业务回调前保存新检查点。使用唯一文件名、0600权限、flush/fsync，既有文件不覆盖；随后事务写入Artifact及零置信度Observation。每份材料绑定Run/Candidate/Scope/Policy、响应数、前一材料ID与文件SHA，形成可核对链。

检查点仅白名单保留状态码、响应内容哈希/字节数、字段摘要和实际隔离证明，不保存请求头、凭据、响应正文/预览或原异常消息。schema为http-replay-checkpoint/1，Artifact kind为http.replay.checkpoint；verification_complete和promotion_eligible始终false，没有完整Oracle或业务边界声明。成功流程另存既有完整http.replay产物，末检查点以final_artifact_id指向它。检查点不会替代正式证明。

传统任务每响应保存进度和检查点引用；取消/普通异常的终态保留它。V5受控工作流用独立回调保存引用，即使cancel_requested已设置，也允许将已经完成的响应写入任务结果。它不会继续发送请求或将失败部分放入auto_verification，不能被去重逻辑当作完成证明复用。页面在复验未完成时显示保留数量和材料编号。

完整两轮10次请求成功新增11份检查点，取消后新增一份终态检查点；没有额外目标HTTP请求。计数表示监督器已记录的完整响应，不是对网络故障时是否发送请求的推断；预算仍按原请求守卫扣除。该增量增加文件/SQLite写入，未产生模型成本或发现精度基准结论。

## 验证

改动后既有HTTP业务边界、受控工作流与真实guided HTTP专项33 passed（26.04秒）。新增/更新中断及恢复专项9 passed（12.62秒）：

- 真实本地服务在第1、4、10个复验响应后取消；准确保留对应响应数，停止后续HTTP，无auto_verification、正式Finding或正/负/V5机器回执。
- 每条检查点文件及前向引用链的SHA一致；材料缺少完整业务边界，独立HTTP成功守卫拒绝它。
- 首响应后材料变化停止，任务保留failed检查点。
- 模拟监督器SystemExit发生在首响应引用持久化后，调用真实启动恢复函数，将running任务标为interrupted且保留相同材料和数量。此为恢复函数验收，不是安装版App冷启动或真实操作系统kill实验。
- 传统取消任务保留非晋升检查点，返回/持久任务JSON无测试凭据。

旧去重/队列/材料替身已适配新的检查点回调，相关专项21 passed（3.61秒）。首轮全量发现四处替身签名不支持回调，已修复并重跑全量。

node --check通过；页面条件文案已接入，尚未做新的浏览器截图验收。最终全量735 passed / 3 skipped（159.91秒），仅既有Starlette/AnyIO弃用警告。

## 迁移、回滚与下一Task

无DDL/schema迁移，不覆盖安装App、不修改生产项目或扫描外部目标。逆向本提交恢复0.68.9；旧版可保留新增Artifact，但不会继续生成或展示这些检查点。

下一Task仍V5-ORCH-02：统一传统任务与V5任务/租约/不可变回执及精确复测谱系、纯判定期间取消、候选证据全集/归档状态守卫。实际进程在响应返回与第一份检查点之间退出，或文件/DB写入失败时，无法宣称保留未提交响应；启动后不自动重放请求。完整15 Task/8 Known Gaps及真实发现、三域/情报/角色/持续研究/规模和发布安装验收仍未完成，整体目标保持进行中。
