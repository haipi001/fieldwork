# 导入终态读取竞态

## 2026-10-15 导入完成状态读取竞态修复

用户目标：继续完成文件夹开发规划，保留UI功能和页面。
发现与实现：read_job读取旧source_ready/未finished快照后，dispatch可能保存终态并移除TASKS，读取方再据旧值误判interrupted并覆盖完成结果。read_job完整读取/判定/保存与dispatch运行标记清理统一使用现有RLock，保存仍复用save_job。
验收：新增真实线程并发检查，终态保存和清理持锁时读方必须等待，完成后读取finished而非误写interrupted；Web3导入及Agent Audit组合114通过，日志build/import-terminal-race-tests.log；git diff --check通过。此竞态可解释之前计划就绪波动，但没有原失败时序记录，不能断言它是那次波动的唯一原因。
边界：本轮UI与主数据未修改，尚未原生重载；三域真实研究闭环与完整规划继续，之前全量1176早于本次增量。
同步状态：当前请求、动作及结果同步八个入口，无秘密或真实研究目标。
