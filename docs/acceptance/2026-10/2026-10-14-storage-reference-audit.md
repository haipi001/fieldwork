# 旧数据根目录引用审计

## 2026-10-14 迁移旧目录引用审计

用户目标：继续完成文件夹开发规划，保留全部UI功能和页面。
实现：storage_reference_audit.py以只读事务扫描暂存SQLite文本及解码JSON中的旧根目录引用，支持Unicode转义；输出表/字段/匹配行数，不输出字段值。对数量、单值大小和JSON深度设置上限，超限失败不截断；暂存清单纳入remaining_database_references。发现run_configs_v2和Web3导入源码路径另有迁移依赖，历史授权/快照关联不盲目改写。
验证：23引用审计/重映射/暂存/完整schema后端相关测试通过，日志build/storage-reference-audit-tests.log；git diff --check通过。未修改UI、主数据库或已安装App。保留当前0.68.75及其他并行开发修改。
边界：检查范围仅SQLite文本及JSON，外部JSON文件和其他编码路径未覆盖；历史路径与操作性路径仍需分类，activation_allowed保持false。正式配置切换、全写入者停止、原生迁移回滚及完整开发目标继续。
同步状态：本轮可见请求、动作和结果已同步八个记忆入口，无秘密或实际研究目标。
