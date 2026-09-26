# 原生 AI 系统事件采集原型

状态：可编译的 macOS Endpoint Security 通知客户端及严格输入契约。尚未接入一键后台监控，也未完成真实系统事件端到端验证。当前页面仍应把实际文件读写标为未接入。

## 构建与无权限自测

```sh
sh collectors/native-ai/build.sh /tmp/fieldwork-native-ai-collector
/tmp/fieldwork-native-ai-collector --self-test
```

自测不会创建 Endpoint Security 客户端。输出均带 `synthetic: true`，后端拒绝将其作为真实事件导入。自测通过仅证明 SDK 编译、序列与元数据格式正确。

## 事件含义

订阅 EXEC、FORK、EXIT、OPEN、WRITE、CLOSE、UNLINK、RENAME 的 NOTIFY 事件，不订阅 AUTH，也不批准、拒绝或阻断其他软件动作。按已知 AI 可执行文件及其进程后代归属；不承诺识别所有 AI 软件。

- OPEN 表示打开，不能证明读取过文件内容。
- WRITE 表示收到写入通知，不直接声明写入副作用成功。
- CLOSE 保留 `modified` 与 `mapped_writable`；可写映射不证明修改，关闭时已修改也不证明关闭进程就是修改者。
- 保留 PID version、启动时间、父进程、授权结果、系统序列和丢事件计数。启动时已有后代的归属标记为快照推断。
- 不读取命令参数、环境、文件正文、聊天正文或 Cookie。文件路径仍是敏感元数据，原型输出应只保存在本机受保护位置。
- 保留文件、重命名目标和可执行文件的系统路径截断标志；时间线提示完整位置尚未确认。
- SIGTERM / SIGINT 停止时先取消订阅并删除客户端，再最多等待两秒排队输出；stderr 报告队列丢弃、输出失败、未完成数量及等待超时。输出失败或超时返回 74，不会静默宣称完整写出。stdout 消费方断开不会通过 SIGPIPE 无声结束采集器。

`native_ai_events.py` 校验事件类型、字段、授权状态及模拟标记。签名或导入不会自动把采集器声明升级为独立验证；现有来源信任规则仍适用。

`native_ai_forwarder.PendingBatch` 提供最多 100 条、1 MB 的签名批次，签名前严格验证原生格式并拒绝模拟数据。批次内容、序列、nonce 和签名冻结；响应丢失时可查询 `/api/v1/agent-audit/audits/{audit_id}/collectors/{collector_id}/receipts/{sequence}`，仅全部匹配已提交回执才确认成功。查无回执仍保留原批次，不生成新序列重复导入。后端继续拒绝重放。`LoopbackTransport` 已提供实际 HTTP 传输，只允许带显式端口的 127.0.0.1 或 ::1 根地址，禁用环境代理与重定向，限定超时和回执大小；可用 `pending.deliver(transport.post, transport.get_receipt)` 发送并核对提交。尚未连接磁盘队列或原生服务；调用方仍需负责受保护私钥、监控暂停和生命周期。单元测试的签名证明传输格式，不证明生产者具备系统完整性保护。

## 实时启用前尚需完成

后端实时原生导入受当前本机监控约束：必须运行中且有登记采集器签名，批次不能混入其他来源或自述；早于启动/最近恢复、未来超过 60 秒、存储不足均拒绝。拒绝发生在事务写入前，不占用序列或留下半批记录。暂停期间记录不会在恢复后补录；已提交回执仍可查询以确认历史批次。

Apple 批准的 Endpoint Security entitlement、有效签名、管理员权限和 Full Disk Access；仅放置 `entitlements.plist` 不会授予权限。本次预检没有发现可用代码签名身份，未申请权限或执行实时采集。

还需受保护服务或 System Extension 打包、私钥与配对保护、可靠转发和重试，以及真实文件动作与实时停止流程验证。不要把此命令行原型当作已完成的保护服务，也不要用诊断用 eslogger 替代正式采集器。

依据：[Apple entitlement](https://developer.apple.com/documentation/BundleResources/Entitlements/com.apple.developer.endpoint-security.client)、[Endpoint Security client](https://developer.apple.com/documentation/endpointsecurity/client)、[close event](https://developer.apple.com/documentation/endpointsecurity/es_event_close_t)。
