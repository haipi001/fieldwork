# V5-DOM-01 Traditional Domain Adapter

日期：2026-09-30

现有 Traditional 工具链保存 Artifact 和 Observation 时，在同一数据库事务中向该 Engagement 的活动 Research Campaign 投影 V5 Graph 节点。Observation 保留来源工具、类型、Run、置信度与旧记录 ID；Artifact 节点只保留哈希、媒体类型及脱敏标记，不复制文件 URI 或内容。`derived_from` 边连接每条 Observation 与其 Artifact。

投影使用 Campaign、来源类型和旧记录 ID 形成稳定节点 ID；手动 legacy bridge 可补入其他历史事实，重复调用不会复制已投影节点。暂停或其他 Engagement 的 Campaign 不接收新的工具产物。扫描结果只形成 `observed` 状态节点，不产生 Claim、Receipt、Canonical Result 或 Finding。

验证：`tests/test_v5_graph.py` 覆盖真实工具结果持久化路径、活动/暂停/跨项目隔离、Artifact 内容边界与 bridge 去重；全量 593 passed、3 skipped。0.49.0 Build 58 未安装 macOS bundle 已构建并通过深度签名校验。Schema 保持 23；无数据迁移。回滚此变更仅需撤销工具结果入库时的投影调用和图谱投影函数，既有 Observation 与 Artifact 保持原样。

下一任务：`V5-DOM-02` Web3 Domain Adapter。
