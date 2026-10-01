# V5-DOM-03 Agent Audit Domain Adapter

日期：2026-09-30
版本：Fieldwork 0.51.0（Build 60 / Schema 23）

Agent Audit 导入遥测和自述时，在同一事务将活动 Research Campaign 的 Observation、Evidence、自述 Claim 及其 Artifact 来源投影到 V5 Graph。事件节点只保留来源类型、动作类型、可信层级、独立性和签名真实性标记；原始资源、路径、命令、自述正文和 Artifact URI 不复制到图谱。自述 Claim 明确标记 `unverified`，不作为独立证据。

确定性分析完成时，仅对 reconciliation 中 `CONTRADICTED` 的自述/事件对创建 `contradicts` 关系；`UNKNOWN`、`UNSUPPORTED` 和单纯遗漏不伪装为矛盾。不生成 V5 Verification Receipt 或 Canonical Result。稳定来源 ID 使重复投影幂等，并按审计 Engagement 与活动 Campaign 隔离。

验收覆盖真实导入/API 分析链、遥测及自述映射、分析前后矛盾关系、敏感正文不复制、无自动晋升与重复投影。Schema 无变化；回滚移除 Agent Audit 两处投影调用及图谱投影函数即可，旧审计数据不变。

验证结果：全量测试 `596 passed, 3 skipped`；Python 编译、`git diff --check` 与未安装 macOS bundle 的 deep/strict 签名校验通过。未导入真实审计材料或覆盖安装版。

下一任务：`V5-CR-01` Continuous Research Engine。
