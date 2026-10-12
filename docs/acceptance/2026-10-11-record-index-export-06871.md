# 脱敏记录索引导出与原生下载支持

日期：2026-10-11。UI-06增量，完整规划仍进行中。

## 变更

设置页新增项目范围和标准/加强脱敏表单，POST `/api/v1/workspace/exports`生成一致事务内的研究、任务、候选、发现和证据元数据JSON索引。采用字段白名单，不读取证据文件、模型输入、凭据、授权或URI字段。标准过滤常见秘密模式，加强另处理URL/邮箱/IPv4；不是保证消除所有人工文本秘密，分享前需检查。
每类10000条、文件16MiB上限，超限409，不静默截断。标记格式、版本、数量、范围、synthetic研究标识、排除项、不可恢复及非复验回执。
自动保留策略仍为独立预览，未删除原有设置入口。旧18页保留。

原生App增加WKDownloadDelegate：本机页面触发的blob/localhost下载进入系统保存对话框，本机attachment响应也支持下载；目的位置由用户选择，不自动开放文件。0.68.71/build149/schema40已构建签名验证并安装；0.68.70回滚包保留于 `build/macos/rollback-06870-record-export/Fieldwork.app`。安装仍依赖工作区和venv，不是独立发布包。

## 已验证

- `build/record-export-final-tests.log`：56工作区/前端通过，1既有弃用警告。
- 之后增强的跨项目测试 `build/record-export-scope-test.log`：1通过；标准/加强脱敏区别、不存在项目、非法选项和数据库不变。
- `build/record-export-limit-test.log`：1通过，10001产物明确拒绝，没有部分下载且DB不变。
- `build/record-export-final-browser.log`：18页及旧操作通过；真实下载JSON文件并解析格式、任务/证据数量、URI排除，零模型调用。8显示组合仅为设置布局。
- Swift构建与codesign严格验证、JS语法及diff检查通过。
- `build/record-export-data-check.json`：九类业务表与此前快照一致，integrity ok。

## 原生未完成

正常退出旧App后启动0.68.71，原生页面等待后显示启动超时。子进程1935在观测时仍活着，health未响应，`build/record-export-startup-sample.txt`显示getpath_readlines→fopen等待。没有终止重启活进程，没有修改系统权限或绕过边界；未取得系统提示内容，不能确定根因。新下载保存对话框尚未原生操作验收，不能标完成。

## 剩余范围与回滚

索引导出不是全数据导出/恢复；原始证据使用专用归档。自动保留、目录迁移、正式恢复/回滚、远程更新及原规划质量门仍未完成。没有Git提交或推送。需要回滚时正常退出App并确认子进程结束，再还原上述回滚包；不还原真实数据库。最新全仓918属于此前执行器阶段，本轮未重跑全仓。

## 2026-10-11后续原生验收通过

原子进程1926/1935保持存活，后续health=200、版本0.68.71；点击原生“重试连接”复用原后端，未重启进程。设置页加载0.68.71/build149。实际点击下载，经系统NSSavePanel选择本地验收目录后保存JSON；399819字节，25研究/0任务/98候选/1发现/1087证据索引，strict格式及URI字段排除检查通过，九表不变、integrity ok。再次下载取消保存，返回同设置页且原文件摘要不变。证据 `build/macos/record-export-native/acceptance.json`。
保存对话框原生验收现已完成；启动延迟根因及三次清洁启动仍未解决/验收，不能标启动问题修复。未传输到外部。
