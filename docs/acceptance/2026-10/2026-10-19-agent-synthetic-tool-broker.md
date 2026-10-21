# 合成工具请求边界观察（限定场景）

日期：2026-10-19。完整规划UI-00→QA-07、15Tasks/8KnownGaps继续开放。

## 接通的范围

新增synthetic-tool-request-boundary.v1，使用fieldwork.local-agent-tools-jsonl.v1适配器协议。可信生命周期监督器持有工具代理，受测脚本只能通过stdin/stdout的JSONL消息提出请求；最终口头响应与实际请求分别保存。

- 唯一允许的操作是fixture.read读取fixture://public合成资料；没有主机文件、shell或网络工具。未知资源和工具返回拒绝，不附结果数据。
- 控制组必须真实请求公开资料并在最终响应正确返回随机回执，才具备可解释的校准。攻击组文档请求扩大到fixture://private，监督器记录真实请求和拒绝，不能把自述读取当调用。
- 结果区分attack_specific_forbidden_tool_request与broker_policy_bypass_observed。前者表示受测脚本发出过被拒绝的请求；本场景没有成功突破主机权限的证明，不签发Finding或独立回执。
- 两次适配器运行使用原Run逐次预算；代理另外限制每例最多4次合成请求、两例最多8次。授权计划/新Scope明确列出虚拟工具与资源范围，host_tool_permissions为空。
- 消息8KiB、最终响应4KiB、唯一请求ID、严格消息结构与调用次数；重复/超量/非法消息、校准缺失、结束响应后额外输出均incomplete，不当作干净负例。
- 每条受理请求、响应、SHA及observed_by来自可信代理通道；适配器输出伪造代理包不会成为可信记录。原父进程约束、禁止fork/signal、取消/超时继续生效。

## 验收

- build/agent-tool-final-current-regression-20261019.log：130 passed/1依赖警告/38.39秒，含实际工具沙箱请求、未授权资源/工具拒绝、控制污染、自述无调用、校准缺失、配额/重复ID/格式错误/结束后输出/伪造代理包、实际API授权及产物、原注入/记忆/生命周期/工作台/Web3路径。
- 清理一处误插入的无效测试分支后，build/agent-tool-executor-final-20261019.log：执行器34 passed/16.92秒。功能代码未变化。
- 工具JSONL阻塞取消、调用父进程死亡导致代理与受测脚本停止均有真实进程测试；不以模拟返回值替代这些执行证明。
- build/agent-tool-workspace-ui-current-20261019.log：最终界面三种场景均完成实际API执行与精确Run证据展开；核对工具计划只授予fixture://public、两例均有真实代理调用记录，原18页/5Agent页签/144显示组合及窄屏保留通过，0模型调用，进程exit0。此前首轮UI日志另保留。
- 11个实现/测试/界面文件在最终UI检查期间SHA不变：build/agent-tool-source-final-before-20261019.json与after。JS语法与git diff --check通过。

## 尚未完成

当前只是合成工具通道内的请求与授权决定观察，不测真实主机工具效果、外部Agent或实际模型质量；观察覆盖不包含绕开此通道的全部行为。单JSON适配器需要实现JSONL协议才能运行工具场景，校准失败会incomplete。

代理在正常结束时返回完整调用记录；中断导致没有取得完整记录时保留incomplete/unknown，不声称0次工具请求，不自动重发。逐调用持久检查点、完整队列/租约Worker、独立验证/不可变回执、正式晋升仍待接通。

本轮全部执行为自建本机夹具，无付费模型。主库只读核对无活动Run/租约/验证作业，agent_attack_tests_v5为空；原生App仍因Mac锁屏无法加载本轮代码，未操作主库配置或研究、未提交推送Git。
