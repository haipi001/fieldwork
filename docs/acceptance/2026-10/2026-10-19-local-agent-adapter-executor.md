# 本地Agent适配器执行原语（部分交付）

日期：2026-10-19。目标仍为完整开发规划；本轮没有关闭UI-04或其余15Tasks/8Gaps。

## 实现与实际范围

新增 agent_attack_executor.execute_local_adapter：输入经SHA确认的最多64KiB Python适配器源码，协议 fieldwork.local-agent-json.v1。仅实现 prompt-injection-canary.v1，一次控制组加一次注入组，各用全新的macOS sandbox与临时目录，复制内容仅适配器脚本。stdin提供合成系统标记与不可信文档；stdout协议严格为单个response字符串。无网络、无工具授权、不继承用户凭据。

调用方必须先审阅并授权适配器。此原语尚未接到App派发、适配器注册或执行Worker；现在没有外部Agent被测试，没有自动消费上轮保存的申请。

- 源码摘要不符、非严格预算类型、不是恰好两用例、每例时限不在1–15秒、源码语法或大小不合法均在执行前拒绝。
- 使用现有isolated_execution执行边界，输出有界64KiB；非零退出、超时、非法/截断协议停止后续用例并标incomplete，不作为干净负例。
- 实际stdout/stderr及其SHA与输入SHA保留；JSON解码后的标记泄露也检查，避免unicode转义绕过。
- 仅当两例有效且控制组未泄露时，报告attack_specific_disclosure。控制组也泄露不会被计为注入特有问题。
- 返回finding_issued=false、independently_verified=false；不把适配器自报行为作为独立工具/记忆证据，不签发Finding。

## 验收证据

build/agent-adapter-executor-current-20261019.log：16 passed/1依赖警告/6.33秒，含7项执行原语测试与9项既有申请测试。真实macOS子进程覆盖保护正常、攻击组泄露、JSON转义泄露、两组都泄露、协议错误及超时；适配器实际尝试socket连接并收到PermissionError，同时检查常见API/会话环境凭据未继承。未通过mock执行器来替代实际沙箱实验。

首轮执行原语6 passed，后增加JSON转义观察用例。摘要：build/agent-adapter-executor-source-sha-20261019.json。git diff --check通过。

## 继续开放的具体项

1. 受测Agent适配器的注册、源码/依赖完整性、授权范围与禁用/版本更新。
2. App从申请重新审阅到实际派发、持久任务、取消/重启恢复、费用/总时长等预算，以及原始产物登记与独立观察回执。
3. 工具越权与记忆污染的真实行为测试；本轮单场景执行原语不能代替它们。
4. 此测试中的适配器是本地构造传输夹具，不是实际模型/外部Agent抗攻击质量验收，也不是holdout。
5. 既有执行器的macOS边界不是VM或独立正式分发保证；其他系统拒绝无隔离运行。UI没有改动，原生加载仍待Mac解锁。上一轮1491默认全量结果早于本轮源码，不扩称本轮全量通过。

本轮不调用付费模型，不修改主库，不提交推送Git。
