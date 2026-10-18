# 包复验证据读取


## 2026-10-17 包复验证据文件读取边界

_read_trivy_artifact复用read_proof_file：在绝对产物根目录内按路径组件拒绝符号链接，非阻塞打开普通单链接文件，限256000字节，读取前后与当前目录项核对设备/inode/mode/nlink/size/mtime/ctime及字节数，再核对DB SHA并解析对象JSON。拒绝硬链接、FIFO、超限、读取中追加或同字节替换；过深/损坏JSON及错误路径作为不可用材料，不抛500。保留原大小上限/产物SHA/独立验证门，不降低检查或改变契约。

47相关测试通过（build/package-material-reading-tests.log，10.18s），覆盖普通材料、错SHA/根外路径、链接/父链接/硬链接/FIFO、超限/深JSON/损坏JSON，以及读取后同字节替换与追加。真实Trivy三样本复验重放3通过（build/package-material-actual-replay.log，1.63s），未重扫/下载/调用模型；原始文件及落库产物manifest核对0变化。正例仍仅包适用性、定向修复仍inconclusive、无关包无match。git diff --check通过。

主库0活动Run/执行任务/模型调用/活动监控后正常退出、端口关闭，再启动来源链接App加载修复；未改主库研究配置/创建任务。无UI页面/功能删除，无schema迁移、提交或推送。全量1353基线早于本次增量，本轮47+3不能替代当前全仓、实际App三域闭环或完整目标验收。全部15Task/8Gaps继续。
