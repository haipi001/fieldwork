# Fieldwork 文档导航

当前开发代码位于 `codex/v6-final-control-plane`。本文将使用入口、当前范围与历史材料分开导航；日期文档中的安装状态和测试数量只对应当时快照。

## 从哪里开始

| 需要 | 入口 |
| --- | --- |
| 产品、能力、启动与限制 | [项目 README](../README.md) |
| 工具、模型与日常维护 | [OPERATIONS](../OPERATIONS.md) |
| 当前完整目标与缺口 | [Master Bundle 范围审计](MASTER_BUNDLE_PROGRESS_AUDIT_2026-10-04.md) |
| UI 与核心交付条款 | [交付计划](V5_UI_AND_CORE_DELIVERY_PLAN_2026-10-04.md) |
| 按日期积累的页面交付 | [UI 交付记录](UI_DELIVERY_STATUS_2026-10-10.md) |
| Agent Audit 数据与验证模型 | [架构说明](AI_AGENT_AUDIT.md) |
| 数据目录与迁移边界 | [存储配置](STORAGE_LOCATION_CONFIGURATION.md) |
| 本地扩展包格式与保存限制 | [包格式](LOCAL_EXTENSION_PACKAGE_FORMAT.md) |

## 最近验收入口

- [0.68.73 Git 开发快照](acceptance/2026-10-12-git-development-snapshot.md)：1020 项通过、3 项跳过和浏览器显示矩阵；后续改动单独验证。
- [原生启动自动恢复](acceptance/2026-10-12-native-startup-recovery.md)：等待与后台身份绑定、原生验收及剩余根因。
- [干净锁定运行环境](acceptance/2026-10-13-clean-locked-runtime.md)：匹配平台的依赖及原生环境证据。
- [Python 依赖与 SBOM](acceptance/2026-10-12-python-dependency-sbom.md)：元数据、导出和供应链范围限制。
- [存储协作锁](acceptance/2026-10-13-storage-access-gate.md)、[工作线程生命周期](acceptance/2026-10-13-storage-thread-lifetime.md)。
- [迁移暂存](acceptance/2026-10-13-storage-staging.md)、[文件引用重映射](acceptance/2026-10-13-storage-reference-relocation.md)：尚未激活和正式切换。
- 全部证据保留在 [acceptance/](acceptance/)。

## 历史与接力

- [接力说明](../HANDOFF_TO_NEXT_AI.md)、[网站发布接力](../PUBLISH_HANDOFF_2026-09-12.md)：检索代码与维护背景；版本与状态以当前源码、最新验收为准。
- [实现历史](IMPLEMENTATION_NOTES.md)：早期详细技术记录。
- 根目录日期规划、评估与 `FINAL/`：历史规格、迁移与交接材料，不是另一份当前运行代码。

## 目录整理原则

仓库目前主要问题是根目录混放与历史入口繁多，未发现大于 1KB 的逐字节重复文件。测试、独立验证 schema 与按日期验收证据承担不同职责，不按文件数量直接删除。当前先提供一个阅读入口；后续源码打包与历史文档移动应作为独立重构，更新引用后验收，保留必要的历史证据。
