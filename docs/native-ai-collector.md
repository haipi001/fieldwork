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

`native_ai_forwarder.PendingBatch` 提供最多 100 条、1 MB 的签名批次，签名前严格验证原生格式并拒绝模拟数据。批次内容、序列、nonce 和签名冻结；响应丢失时可查询 `/api/v1/agent-audit/audits/{audit_id}/collectors/{collector_id}/receipts/{sequence}`，仅全部匹配已提交回执才确认成功。查无回执仍保留原批次，不生成新序列重复导入。后端继续拒绝重放。`LoopbackTransport` 已提供实际 HTTP 传输，只允许带显式端口的 127.0.0.1 或 ::1 根地址，禁用环境代理与重定向，限定超时和回执大小；可用 `pending.deliver(transport.post, transport.get_receipt)` 发送并核对提交。加密磁盘队列与签名恢复已提供并通过重启组合测试，尚未连接实际原生服务；调用方仍需负责受保护密钥、监控暂停和生命周期。单元测试的签名证明传输格式，不证明生产者具备系统完整性保护。

## 实时启用前尚需完成

实时原生输入还必须通过 `native_ai_binding`：采集器公钥匹配受保护安装的公共登记，登记文件及父目录由 root 持有、不可组/全局写、无符号链接；限制登记大小、字段和读取实例，并验证对应安装采集器的固定签名身份。普通用户在网页登记一把签名密钥不能据此冒充系统来源；系统事件及系统归属的保留标识也要求原生 schema，不能删除 schema 沿用系统标签。公共登记只包含版本、Team ID 和公钥，不能放入私钥；实际安装服务生成和维护登记尚未完成，当前默认拒绝未绑定来源。此检查不单独证明私钥保管或实时权限成功，仍需真实服务验证。

`scripts/prepare_native_release.py --output <zip>` 构建单独的未签名采集器预览，运行明确的模拟自测并生成二进制 SHA-256 清单，不安装或启用采集。发布方提供 `--team-id` 和 `--sign-identity` 时才签名，再检查固定标识、可信团队和 ES entitlement；拒绝 ad-hoc 身份和签名失败产物。无论哪种模式，清单都明确 `installation_ready: false`、不含受保护服务且未完成实时验证，不能当作可用安装器。现有输出不覆盖。

`NativeCollectorSession` 已连接可信启动门禁、读取和批转发，限制重试间隔并保留同一待确认批次；采集器激活失败与网络故障分开报告，不因进程存在而宣称采集完整。停止只处理本会话持有的子进程，限时退出后必要时强制终止，停止期间不请求网络，先持久保存已接受记录；报告未持久保存数量、剩余缓冲、stdout 是否排空及是否强制退出。停止不等于所有输出已收全，未接收尾部明确保留为缺口。测试使用独立测试进程与网络替身，尚未接入已授权的实际 ES 进程、受保护服务安装或网页一键控制。

`native_ai_launcher` 提供固定安装位置的启动门禁：文件及所有父目录必须由 root 持有、不能组/全局可写、不能经过符号链接；签名必须满足 Apple 证书链、固定代码 identifier 与已配置 Team ID，同时带真实布尔 ES entitlement，不能启用调试权限。检查前后核对文件实例，签名超时或失败时不启动。启动函数只允许已授权管理员服务调用，固定可执行文件且不接受任意命令参数。门禁通过不证明 ES 权限已可用；生产采集进程仍可能因授权不足退出。本机未安装此受保护采集器、未配置生产 Team ID，未提升权限或执行该启动函数。真正的受保护服务安装、签名身份和系统授权仍待完成。

签名 requirement 依据 [Apple TN3127](https://developer.apple.com/documentation/technotes/tn3127-inside-code-signing-requirements)；测试用替身验证门禁行为，不宣称签名安装验证完成。

`NativeProcessReader` 已提供进程管道读取：非阻塞、单次等待最多一秒、分段 JSONL 重组、单行 256 KiB 上限，遇到管线背压保留未接受的原始行并暂停读取。格式错误、模拟标记、超长行与退出时半行明确拒绝；stderr 只统计字节，不转发或保存任意诊断正文。状态报告 EOF、已读取数量与退出码，不把进程活着当作已具备系统采集权限。它只接收已经由可信服务启动的 Popen 句柄；代码级签名门禁与会话启动停止已提供；实际受保护安装、签名实测与网页暂停控制仍待完成。当前测试使用明确的独立测试生产者，不证明 Endpoint Security 实时采集已启用。

`NativePipeline` 已把验证、签名、加密队列、HTTP 发送与回执恢复连为一条可调用管线：`accept(record)` 接收元数据，`flush()` 先保存再发送，未明确确认不清队列；最多缓存 100 条且不超过 1 MB，未确认时向进程读取方返回背压。确认后的下一序列独立加密持久化，先保存序列再清除批次；在清理前重启时仍核对原批次回执，不回退序列。本次通过模块级组合验证，尚未连接实际 Endpoint Security 进程或用户的一键监控入口。

`EncryptedPendingQueue` 已提供单个未确认批次的持久队列：AES-GCM 加密并绑定审计和采集器，目录 0700、文件 0600，拒绝符号链接与非普通文件。原子发布、文件/目录 fsync 和协作锁用于重启恢复；未确认批次不能覆盖，签名恢复仍需匹配登记公钥、审计和会话。恢复后只重试同一请求；确认匹配提交回执后才清除。损坏或错密钥时明确失败，不静默丢弃。单批限制对原生输入提供背压，实际管道还需接入。目录权限和加密不提供防同用户删除或阻断的完整性保护；生产密钥必须由受保护服务保管，不能与队列一起存放或暴露给被审计应用。

后端实时原生导入受当前本机监控约束：必须运行中且有登记采集器签名，批次不能混入其他来源或自述；早于启动/最近恢复、未来超过 60 秒、存储不足均拒绝。拒绝发生在事务写入前，不占用序列或留下半批记录。暂停期间记录不会在恢复后补录；已提交回执仍可查询以确认历史批次。

Apple 批准的 Endpoint Security entitlement、有效签名、管理员权限和 Full Disk Access；仅放置 `entitlements.plist` 不会授予权限。本次预检没有发现可用代码签名身份，未申请权限或执行实时采集。

还需受保护服务或 System Extension 打包和签名安装、生产密钥保管与网页配对控制，以及真实文件动作、转发和停止流程的系统级验证。不要把此命令行原型当作已完成的保护服务，也不要用诊断用 eslogger 替代正式采集器。

依据：[Apple entitlement](https://developer.apple.com/documentation/BundleResources/Entitlements/com.apple.developer.endpoint-security.client)、[Endpoint Security client](https://developer.apple.com/documentation/endpointsecurity/client)、[close event](https://developer.apple.com/documentation/endpointsecurity/es_event_close_t)。
