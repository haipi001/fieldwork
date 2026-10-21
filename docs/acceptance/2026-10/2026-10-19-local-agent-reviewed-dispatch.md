# 本地Agent适配器受审阅派发与同Run证据（限定场景交付）

日期：2026-10-19。完整UI-00→QA-07/15Tasks/8KnownGaps保持进行中。

## 本轮接通的实际流程

登记并启用本地Python适配器 → 保存新的测试申请 → 选择ready Agent Audit项目 → 审阅源码/身份/组件与当前Scope/Policy摘要、两例合成场景与每例1–15秒预算 → 明确确认新增授权并执行 → 在同一记录展开登记证据。

- 不沿用只有offline_analysis的旧授权冒充执行许可。明确确认后新增ScopeSnapshot，记录local_synthetic_adapter_test允许动作及精确Agent/源码SHA、零网络/工具权限和授权说明；建立新的synthetic Run。旧Scope与旧Run不修改。
- 每次仅两例，Run预算最多两次调用，调用开始前逐次扣记；请求/模型预算为0。全本机只有一个此类执行锁，正在执行时不接受第二次；同申请结束后不能再次执行。
- 启动前重算计划；执行中持续检查身份/组件、适配器配置、Scope/Policy、Run状态和预算，变化即停止。可请求取消。
- 状态与Run、逐例预算、计划指纹持久化。进程持有执行锁时拒绝中断恢复；失去锁后可显式标记interrupted，保留已用预算，不自动重试。
- 产物以0600/排他无符号链接写入，注册artifact与observation并事务收尾。失败时清理本轮文件，保留执行中记录供显式恢复，避免伪造完成。
- 证据在原测试记录内展开，绑定精确request/Run/artifact并重读SHA；文件篡改409，授权/适配器变化标历史状态。没有跳到当前研究域中的另一Run。
- 新建授权、执行开始/结束、取消与中断均记录事件。输出标为合成标记观察，不生成Claim、独立回执或Finding。

## 当前证据

- build/agent-adapter-dispatch-evidence-regression-20261019.log：93 passed/1依赖警告/21.93秒，覆盖真实API两例执行、旧Scope/Run逐字段保留、实际产物SHA与0600、重复执行阻止、旧计划拒绝、实际取消、身份/Run/预算运行中变化、锁与中断恢复、登记失败文件清理、产物篡改拒绝，以及原登记/申请/执行原语/共享隔离/工作台。
- build/agent-adapter-dispatch-evidence-ui-20261019.log：隔离真实API UI登记/启停、申请、计划输入变更撤销确认、明确新增授权执行、数据库case_count=2与预算=2、精确Run证据展开检查通过；原18页/5Agent页签/144组合与原交互保留，0模型调用，进程exit0。
- 8个生产/测试/界面文件运行前后SHA一致：build/agent-adapter-dispatch-source-before-20261019.json与after。JS语法、git diff --check通过。
- 原父进程死亡实验见2026-10-19-agent-adapter-lifetime-cancel.md；本轮API恢复以模拟执行器异常和锁状态验证，不冒称已完成安装App断电/重启组合验收。

## 保留的失败与修正

1. 首轮API收尾误用stage旧列名，2 failed/14 passed；修正为current_stage，失败日志 build/agent-adapter-dispatch-initial-20261019.log保留。
2. 首轮界面项目缓存只含当前研究域，无法选Audit项目，超时失败；改为单独只读加载Audit项目。失败日志 build/agent-adapter-dispatch-ui-20261019.log保留。
3. 随后发现通用证据跳转可换掉Run，增加本记录内SHA核验与精确Run断言。早一轮UI成功日志仅作过程记录，最终采用evidence-ui日志。

## 继续开放的范围

- 当前是同步有界、人工确认的本地Python合成标记执行，不是完整持久队列/租约Worker。App退出停止进程，需显式中断恢复；没有自动重发。
- 工具越权、记忆污染、真实模型/外部Agent适配器、独立行为判定与不可变独立回执、正式晋升仍待实现。受测源码夹具不是holdout或通用抗攻击质量证明。
- Scope授权新增是明确操作的结果；即使后续执行失败，已确认的快照仍保留，不伪称从未发生。每次新执行仍需新申请和新审阅。
- App新源码加载与原生三次干净启动仍待Mac解锁，未操作主库研究或配置。UI保留检查是隔离浏览器，并非本轮原生验收。
- 此前1491默认全量早于本轮改动；本轮只报告93相关与上述UI。三域质量/规模/通知/迁移/正式分发及完整规划未关闭。
- 无付费模型调用、Git提交或推送。
