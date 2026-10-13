# 统一数据路径配置验收 — 2026-10-12

## 实现与边界

- 后台统一读取进程固定的 FIELDWORK_DATA_DIRECTORY；未设置保留原默认值，显式无效配置拒绝启动。
- 配置绝对、已存在、当前用户所有、私有POSIX权限目录；逐级NOFOLLOW拒绝路径及父目录链接。
- app、final_core、传统/Web3证据、传统仓库、报告、Agent工作区、Web3练习、本地凭据、回滚命令均使用统一解析器。依赖core路径的备份、扩展包与监控保持同根。
- 不热切换，不复制旧目录，不重写历史文件URI。正式迁移、原生持久设置、完整复制/切换/回滚仍未完成。详见 docs/guides/STORAGE_LOCATION_CONFIGURATION.md。

## 证据

- build/storage-paths-tests.log：9项测试通过，真实新子进程导入全部相关模块并核对一致路径；隔离数据库、导出、本地凭据实际写入；正常服务初始化与维护API、两次重启读取首次保存的表记录通过。相对、空、缺失、公开权限和符号链接配置拒绝启动。
- build/storage-paths-browser.log：18页、144显示组合及既有实际操作检查通过，零模型调用；不代表原生或所有组合全部功能。
- build/storage-paths-main-preservation.json：主库9张业务表摘要相同、迁移前schema40、迁移后schema41、integrity ok。
- 初次全量回归发现迁移入口原标题被改名，已恢复原标题并保留真实检查表单；build/storage-paths-final-focused.log：44项相关检查通过。
- build/storage-paths-full-suite.log：1007 passed / 3 skipped / 2 warnings，296.45秒；全量发生在最终Swift启动参数调整之前。调整后build/storage-paths-launcher-tests.log：17项相关通过，含真实 -P / 明确PYTHONPATH / 根目录cwd启动、生产会话401、Host403、父管道关闭退出。
- Python编译和git diff --check通过。

## 部署与原生验收

本轮前两次CUA连接超时，后续连接恢复。旧原生包0.68.72与源码后台0.68.73版本不匹配，工作台拒绝连接；正常退出后核对端口关闭，保留旧包并安装0.68.73/build151，签名检查通过。

启动采样首先显示Python阻塞在getcwd。启动改为 -P、固定PYTHONPATH、cwd=/，构建并再次安装；真实生产启动参数测试通过。原生首次启动仍在导入目录初始化等待并超时，使用已有重试入口后成功，不能声称首次启动延迟已修复。系统TCC查询没有相关记录，不能据此断言权限问题。

直接CUA逐项打开18个导航页与6个Agent子页，检查实际页标题/选中标签，无加载失败；设置显示0.68.73/151/schema41。原生执行迁移前只读检查：1109文件、30目录、49389128逻辑字节、无特殊/不可读/扫描变化；所需165887120字节，目标目录保持为空，检查后已清理测试空目录。未实际迁移或模型调用。

主库9业务表摘要与基线相同；正常启动自动完成40→41，迁移前备份20261012T115536Z-pre-schema-40-to-41.db schema40/integrity ok，UI备份显示valid。证明见build/storage-paths-main-preservation.json。

旧包保留于build/macos/rollback-06872-storage-paths/Fieldwork.app。应用仍链接此源码与本机Python，旧包不是独立可运行回滚：回退需要匹配旧源码和数据库方案，不能仅覆盖旧包。正式独立发布、稳定冷启动、正式存储迁移与完整规划继续。
