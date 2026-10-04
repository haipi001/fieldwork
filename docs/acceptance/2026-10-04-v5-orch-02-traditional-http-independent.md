# V5-ORCH-02：旧HTTP入口的独立请求与确认

日期2026-10-04；源码0.68.9/build87/schema25。

## 变更文件

http_independent_confirmation.py、traditional_runtime.py、verification_receipts.py、tests/test_http_business_boundary.py、tests/test_final.py、version.py、docs/DEVELOPMENT_STATUS_2026-10-04.md、docs/MASTER_BUNDLE_PROGRESS_AUDIT_2026-10-04.md及本验收文档。

## 实现

传统同步和后台HTTP复验的finalize=True不再使用应用进程内request_once，即使调用者传isolated_transport=False，仍使用已授权连接代理和独立HTTP沙箱。对象读取的两轮10次真实响应提取字段摘要后，另一个禁止网络和应用文件读取的沙箱执行http_object_read_v1。原应用进程的semantic_checks继续作为诊断，不能独自决定正式成功或修复。

新增http_independent_confirmation.py将所有角色的实际传输证明、字段摘要、Scope与规则摘要、精确输入哈希绑定独立结果。实际响应投影的source_artifact_sha256是rounds/assertion/business_boundary的哈希，避免将新生成的确认块与整份文件互相递归哈希；最终Artifact仍独立保存整文件SHA。只有positive进入成功分支、repaired_negative进入定向修复分支；缺失/歧义规则保持human_review，判定进程失败抛出错误，不降级为进程内成功。

verification_receipts.py的新HTTP正/负收据签发再次检查独立结果和当前材料；已有V5回执桥接继续执行其V5账本验证。历史已签发机器收据仍按原哈希/授权协议验证，没有回填或篡改历史观察。

每个正式请求、连接与等待检查当前Scope、Policy与候选指纹；独立判定前后也核对。材料变化停止后续请求或正式签发。HTTP请求期间保留取消检查；纯判定进程目前仍使用既有25秒进程截止时间，取消会在判定返回后阻断保存，尚未改为判定期间200ms取消轮询。

## 直接验证

专项12 passed（17.26秒）：真实本地旧入口正例、异步任务、报告预览/导出/记录回放与修复负例；八类业务规则结果；新增策略首个请求后变化只发送一次、判定进程失败10次请求后不产生正式结果；取消任务不保存凭据。

正例核对实际判定PID区别于全部HTTP worker PID，文件和网络拒绝探针通过。五类确认缺失/输入摘要篡改/网络探针缺失/结果分类篡改/请求材料篡改均被独立确认校验与收据守卫拒绝。取消专项模拟传输等待，真实传输子进程取消另由既有transport专项覆盖。

全量结果：731 passed / 3 skipped（161.08秒），仅既有Starlette/AnyIO弃用警告。无模型调用基准、发现精度结论或真实外部目标验收。没有schema迁移、App覆盖安装或生产数据修改。

## 仍需推进

这一步消除旧HTTP正式结果只靠进程内判定的路径，但没有将传统任务自动登记到V5统一任务/租约/不可变回执账本，也没有把传统修复谱系升级为V5精确主体/时间线守卫。中断时部分HTTP材料持久保存、纯判定的及时取消、候选证据全集变化守卫和终止/归档状态守卫继续补齐；传统人工填写的严重度/源码根因还不能声明已由读取Oracle证明。

完整15 Task/8 Known Gaps仍按MASTER_BUNDLE_PROGRESS_AUDIT逐项验收，整体目标未完成。下一Task ID仍为V5-ORCH-02：统一账本迁移与中断材料，随后真实发现、三域、角色/情报/持续研究/规模及安装发布门。

## 回滚

逆向本提交恢复0.68.8源码。无数据库DDL变化；新Artifact包含independent_confirmation，旧程序能读取既有字段，但回滚会恢复旧HTTP签发能力，不能把回滚后结果声称为当前独立验证。
