# 迁移文件暂存验收

## 2026-10-13 迁移文件暂存与摘要校验

用户要求：继续完成文件夹开发规划，保留UI功能与页面。
实现：新增storage_staging.py和scripts/stage_storage_migration.py。源和目标维护独占锁；目标须为空且owner-private；通过目录描述符/O_NOFOLLOW读取，拒绝链接/特殊文件/硬链接，限制100000项、64层、2GiB。首次完整摘要清单、流式复制、目标独立读回、源再次摘要比较；持久化成功清单或失败标记，保留失败副本供诊断，不删除源文件、不切换配置。文件仅保留内容及owner执行位，目录700/普通文件600。成功状态files_verified_not_activated，activation_allowed=false。
验收：34存储暂存/锁/预检测试通过；真实CLI隔离复制256行SQLite数据库及2MiB二进制证据，目标数据库integrity_check=ok、摘要一致、源数据库不变。证据build/storage-staging-tests.log、build/storage-staging-cli.json。未访问主数据库，未重启App，未修改UI或接入误导性迁移按钮。
剩余：独立外部写入者停止确认、完整数据库一致性与路径URI重映射、配置原子切换、启动/回滚验收未完成。文件暂存不能直接用作已完成迁移；ACL/xattr不复制，后续激活必须重新验证。完整15 Task/8 Gaps目标继续。
同步状态：当前可见请求、动作及结果同步八个入口，无秘密或实际目标信息。

## 使用方式

`python3 scripts/stage_storage_migration.py /absolute/source /absolute/empty-private-target`

命令创建target/migration-stage-随机值/data及manifest.json。失败副本含FAILED.json；源数据和现有配置保留。源/目标的.storage-access.lock不得删除或替换。重试应使用另一个空私有目录。这个命令只提供文件暂存，不授权激活该目录。
