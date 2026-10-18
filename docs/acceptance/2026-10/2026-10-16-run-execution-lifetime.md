# Run后台执行互斥与线程生命周期

## 2026-10-16 快速暂停恢复的Run执行互斥

用户目标：继续完成完整开发规划，UI不缺失页面功能。回到Master Bundle与UI分包计划，优先处理P0执行幂等/恢复边界。
复现：新增真实线程阻塞工具测试，先暂停再恢复并启动第二执行器，原代码在首工具仍未返回时重复调用；build/run-overlap-before.log明确1失败。
实现：新增run_execution，数据目录内私有.run-execution目录、Run ID摘要命名锁文件，NOFOLLOW/owner/权限/普通文件/链接数及inode检查，flock跨进程互斥。工具链、远程仓库流水线、合约流水线三个后台入口串行同Run，嵌套调用复用仍存活的所有权，不同Run并行。线程提交前保留执行引用，执行线程结束才释放；shield executor future防止取消排队线程后引用泄漏，Fork启动也接入。任务取消时仍活线程保留Run互斥与存储共享锁，等待者取消不释放现有执行器锁。锁文件不得在服务运行时删除/替换。
验证：首轮57相关专项通过（18.13秒）；新增跨进程/取消/独立并发/不安全锁与进程死亡后接管，最终8通过（3.93秒），build/run-execution-final-tests.log。较大组合首轮193通过/1Forge断言失败/6临时目录准备错误，build/run-execution-regression-tests.log；当时磁盘低余量，未据此断言所有错误具体原因，独立真实Forge重跑恢复预期1反例。同一完整组合顺序重跑200全部通过/1既有Starlette警告（70.33秒），build/run-execution-regression-retry.log。git diff --check通过。
安装：确认无活跃Run/租用AgentTask/模型调用，CUA正常退出确认8000停止监听，再重启源码关联0.68.75/build153，health ready。队列/证据/报告/智能体四页实际打开无加载失败，留在智能体清单。九类业务表计数/摘要不变、quick_check ok，build/run-execution-native-before.json与-after.json；上一轮证明包有界读取也随此次重启加载。未创建真实研究，未重打独立发行包或Git提交推送。
回滚：无需schema迁移；仅在停止相关执行且确认无存活线程后，人工审查回退本轮入口装饰与线程封装及run_execution模块，保留其它未提交成果；不删除在用锁文件，不直接恢复旧数据库。
边界：仅上述三个后台入口及其封装线程，独立单工具API、V5 AgentTask租约及其它执行类型不能据此统称全局at-most-once；没有证明断电后工具副作用/所有进程树/真实模型质量。真实本机模型服务仍未提供可用地址，完整15 Task/8 Gaps、三域质量、持续监控、迁移激活回滚与发布门继续。
同步状态：目标、复现、实现、测试/安装证据及边界同步八个入口，无秘密或真实目标信息。
