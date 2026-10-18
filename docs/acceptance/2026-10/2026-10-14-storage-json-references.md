# 迁移JSON文件引用检查

## 2026-10-14 迁移JSON文件路径引用覆盖

用户目标：继续完成开发规划，UI不缺失页面或功能。
实现：迁移暂存清单增加remaining_json_file_references，检查清单内UTF-8 JSON文件的旧数据根引用，涵盖Web3导入任务记录等文件。通过目录描述符/O_NOFOLLOW读取并核对清单SHA256；拒绝变化、超限和无效JSON。仅报告相对文件名和匹配数量，不返回字段值。复用数据库JSON转义识别逻辑。
验收：24引用审计/暂存/重映射/完整schema后端测试通过，日志build/storage-json-reference-tests.log；git diff --check通过。真实主数据与UI未修改，未重启App。保留其他开发修改。
边界：范围是清单内UTF-8 .json文件；其他编码、JSONL、非JSON配置尚未审计，引用仍需分类迁移；没有自动改写历史证据或授权快照，activation_allowed仍为false。正式切换回滚与完整规划继续。
同步状态：当前可见请求和操作结果同步八个入口，无秘密或真实目标信息。
