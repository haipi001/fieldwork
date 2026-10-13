# V5-ORCH-02：真实监督器 SIGKILL 与跨进程恢复

## 范围与改动

源码仍为0.68.13 / build91 / schema25。本次增加tests/test_http_process_recovery.py及进度/审计文档，并修复scripts/build_macos_app.sh的包版本写入，没有修改HTTP运行时或数据库结构。

测试启动独立Python监督器，使用临时SQLite与Artifact目录、真实localhost HTTP服务和明确标记的fixture身份。每次响应仍经过正式隔离传输，等待检查点文件fsync、数据库Artifact及任务引用提交后，父测试仅向自己创建的监督器发送SIGKILL并回收它。恢复在另一个新Python进程调用实际init_final_db进行。

## 直接证据

- 第1响应提交后强制终止：保留1/10响应及Artifact哈希，来源任务重启后interrupted；没有回执或正式Finding。
- 全部10响应与完整Artifact提交后强制终止：保留完整材料及非晋升检查点，来源任务interrupted；重启不自动签回执或重新发送HTTP。
- V5独立判定任务已领取租约、尚未启动Oracle子进程时强制终止：使用fixture将该真实租约期限缩短到2秒，按实际墙钟等待过期后，由新进程recover_expired_leases重新排队；第二次领取执行真实沙箱Oracle，签发当前材料eligible的verified回执。HTTP调用数仍为10，未自动生成正式Finding。
- 三种场景均比较恢复前后任务result、检查点原始字节与数据库SHA。检查点不保存请求headers或响应正文，运行状态为paused或已经completed。
- 专项：3 passed，10.96秒。最终全量：754 passed / 3 skipped，236.10秒；仅既有Starlette/AnyIO弃用警告。

## 打包版本检查

原build_macos_app.sh只复制macos/Info.plist，其模板仍为0.68.2/build80；新版源码构建后也会携带旧expectedVersion，导致与当前后端版本握手不匹配。现在构建时从version.py读取APP_VERSION/BUILD_NUMBER写入生成包Info.plist，再编译与签名。

实际zsh scripts/build_macos_app.sh成功生成build/macos/Fieldwork.app；元数据为0.68.13/build91，codesign --verify --deep --strict及zsh -n通过。这是本机ad-hoc开发构建，不是发布签名或安装/冷启动验收；/Applications/Fieldwork.app检查仍为0.68.2/build80。

## 限制、回滚和下一任务

这证明独立HTTP监督器进程退出及新Python进程恢复，不等同于实际桌面App/ASGI全生命周期冷启动。SIGKILL时Oracle尚未spawn，未证明父进程在活跃子进程执行期间死亡后的孤儿进程清理。没有在文件/SQLite提交中途杀死，也没有执行断电测试。没有生产数据、外部目标或实际凭据。

无迁移、无性能基准变动。回滚只需撤销新增测试、文档及构建脚本增量；新开发包在忽略的build目录中，未覆盖安装App。App安装检查点仍0.68.2/build80，源码版本与安装版不能混称。下一Task继续V5-ORCH-02：实际桌面启动/退出、活跃子进程父进程退出、真实发现及跨claim/三域独立验证。完整15 Task与Known Gaps仍按总审计推进。
