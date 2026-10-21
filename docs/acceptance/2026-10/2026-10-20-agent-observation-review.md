# 智能体合成观察结果审阅

日期：2026-10-20。完整 UI-00→QA-07、15 Tasks、8 Known Gaps 继续开放。

## 实现

- 两个本地合成用例冻结自身输入胶囊，随原产物保存；不复制用户研究内容或凭据。
- 新增只读 `/agent-attack-requests/{id}/review`：先检查登记产物 SHA、Run，再逐项核对冻结输入与 stdout/stderr 摘要。注入重新检查合成标记；记忆重新检查前后最终策略文件；工具重新解析可信代理帧、响应和对照资料回执，不释放响应或重新执行。
- 输出明确区分观察到信号、限定范围内未观察到、对照不成立、材料不完整、旧输入不可用。旧产物原始证据仍可读取，不伪造缺少的输入。
- 当前性检查新增 Agent 及组件快照核对。过期授权仍可历史查看，不自动重试。
- 攻击测试记录增加结果摘要、逐例输入核对与信号表格、Run 标识及可展开审阅依据。原始证据和已保存调用检查点入口保留。

## 验证

- `.venv/bin/python -m pytest -q tests/test_agent_attack_review.py tests/test_agent_attack_dispatch.py tests/test_agent_attack_executor.py tests/test_agent_tool_stream.py tests/test_agent_tool_checkpoints.py`：65 passed / 1 依赖警告 / 50.45 秒；日志 `build/agent-review-final-regression-20261020.log`。
- 包含三个场景真实隔离执行、正负信号、对照污染、遗留输入缺失、胶囊/输出损坏、授权变化与只读审阅无新增 Run/Scope/模型调用/发现，以及原取消、中断、工具检查点。
- 初次误用系统 Python，触发旧 pytest 的 setup 钩子兼容错误；改用项目 .venv。一次并行运行的 1 秒工具用例结果 incomplete；保留失败日志，后续相关 65 项通过。这不是三次稳定性验收。
- JS 语法与 `git diff --check` 通过。

## 安装 App

主库只读确认无活动 Run/租约、runtime_calls=0 后，CUA 正常退出，HTTP 连接拒绝确认旧服务停止，再启动 `/Applications/Fieldwork.app`。攻击测试页显示“已读取持久化记录”，原有表格/申请页可见。源代码关联 App 加载更新，没有重建独立安装包。

`build/agent-review-native-before-20261020.json` 与 after 内容完全一致：Provider 元数据摘要相同、主库模型调用及测试申请仍为 0。没有在主库灌入夹具；原生摘要记录交互未验收，需未来独立测试配置。

## 边界

审阅是对同一份合成观察的重新核对，不是独立执行收据、安全通过或漏洞确认。工具不测试真实主机效果；记忆只观察最终文件状态。未完成三域质量闭环、8/16/32 质量成本、三次干净原生启动、完整 worker、迁移激活及独立签名分发。DeepSeek 配置保留；本轮不调用模型。

## 浏览器验收结果

`build/agent-review-ui-current-runtime-20261020.log`：隔离实际API三场景、摘要准确Run与限定范围阴性文字、展开依据、原始证据/检查点入口、430宽无整页溢出，以及18页/5Agent页签/144显示组合全部通过，进程exit0，0模型调用。初次新增断言误把受保护工具夹具期望为阳性，修正为限定范围未观察到，并展开details后检查JSON；失败日志保留。
