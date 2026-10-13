# V5-DOM-02 Web3 Domain Adapter

日期：2026-09-30
版本：Fieldwork 0.50.0（Build 59 / Schema 23）

Web3 源码/AST 分析、Forge 属性测试、独立工具扫描及 Forge 属性重放保存旧 Observation/Artifact/Evidence 时，在同一事务中向活动 Research Campaign 投影 V5 图谱。投影使用旧记录 ID 生成稳定节点 ID，Observation 与 Artifact 由 `derived_from` 连接；Forge 正证据映射为 Evidence，反证据映射为 Counterevidence。

部署上下文由最新部署对齐快照和当前源码检查的明确参数组合，仅允许链 ID、区块、合约/实现地址、源码提交及字节码哈希等字段进入图谱。RPC URL、源码目录及 Artifact URI 不复制到图谱。扫描和属性测试结果仍是 Observation/Evidence，不生成 V5 Claim、Verification Receipt 或 Canonical Result。

验收覆盖 API 源码分析入口、部署上下文保留、正反证据、重放/工具既有回归、重复投影与跨项目隔离。Schema 无变化；回滚仅需移除 Web3 写路径投影调用与图谱投影函数，旧数据保持原样。

验证结果：全量测试 `595 passed, 3 skipped`；Python 编译、`git diff --check` 与未安装 macOS bundle 的 deep/strict 签名校验通过。未触发外部扫描或覆盖安装版。

下一任务：`V5-DOM-03` Agent Audit Domain Adapter。
