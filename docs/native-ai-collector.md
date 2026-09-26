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

`native_ai_events.py` 校验事件类型、字段、授权状态及模拟标记。签名或导入不会自动把采集器声明升级为独立验证；现有来源信任规则仍适用。

## 实时启用前尚需完成

Apple 批准的 Endpoint Security entitlement、有效签名、管理员权限和 Full Disk Access；仅放置 `entitlements.plist` 不会授予权限。本次预检没有发现可用代码签名身份，未申请权限或执行实时采集。

还需受保护服务或 System Extension 打包、私钥与配对保护、可靠转发和重试、停止清理，以及真实文件动作验证。不要把此命令行原型当作已完成的保护服务，也不要用诊断用 eslogger 替代正式采集器。

依据：[Apple entitlement](https://developer.apple.com/documentation/BundleResources/Entitlements/com.apple.developer.endpoint-security.client)、[Endpoint Security client](https://developer.apple.com/documentation/endpointsecurity/client)、[close event](https://developer.apple.com/documentation/endpointsecurity/es_event_close_t)。
