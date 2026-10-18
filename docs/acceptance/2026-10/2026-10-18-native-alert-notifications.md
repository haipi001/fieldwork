# macOS App运行期间系统通知配置与派发代码


## 2026-10-18 macOS App运行期间通用系统通知入口

继续UI-05系统告警通道。新增macos/LocalAlertNotifications.swift及static/v5-native-alerts.js，原页面/告警/来源/重放/处理记录不删除。安全监控收件箱下新增实际原生状态、App开关与系统授权分离、明确确认启用/停用/核对，以及浏览器无桥时的降级说明。默认关闭；启用动作才请求macOS alert授权，本轮未替用户启用或申请权限。开关持久化、中文/英文通知语言保留并随页面语言更新。

通道仅在App运行期间处理启动基线之后的新未处理告警，15秒轮询、每批最多50窗口聚合一条通用提示；启动不补发历史，已处理不通知，游标成功提交后推进，无变化不重复，失败保留游标。提示不含目标、事件正文、身份、策略或凭据，不表示漏洞成立。App退出后不派发系统通知，独立监控如已启用仍只保存收件箱历史。这是App运行通道，不替代退出App/登录服务通知及外部通知需求。点击现有App运行期间的本应用通知可打开安全监控；冷启动点击通知路由和实际弹出仍未验收。

新增只读workspace/monitor-alert-native-delivery接口，cursor基线/分页/数据库重置处理，先校验冻结来源再返回仅窗口ID与信号数；不创建告警或自动标记已处理。原生桥只接固定status/enable/disable和语言，限主框架、固定127.0.0.1端口及/v5路径；不接受任意内容、命令或地址。轮询前核对自有后端仍活跃及实例/版本，拒绝重定向，session token仅放本机请求头、Cookie与缓存关闭，不进JS或系统提示。停用/重启的代际围栏丢弃旧异步提交结果。

验收：build/native-alert-regression.log 23相关通过/1既有warning（18.88s）；后续原生来源/命令/游标纯策略实际Swift编译运行，build/native-alert-origin-policy.log 1通过（2.83s）。包含基线不回填、重复/失败/停用代际、来源端口/主框架/命令拒绝，以及API去重/处理/隐私字段。build/native-alert-ui-release.log隔离真实API、明确模拟原生桥的启用确认/停用/语言/浏览器降级与原告警来源/重放/处理流程通过，0页面错误/模型/系统任务变化；它没有执行真实macOS授权或UN通知提交。Swift SDK类型检查及最终build/native-alert-build-release.log原生构建成功。build/native-alert-workspace-preservation.log原18页/5Agent子页/144语言主题宽窄组合及原操作链通过，0模型调用。未重跑当前全仓，旧1406基线早于本增量。

实际安装/Applications/Fieldwork.app 0.68.76/build154，schema仍41、沿用已锁定私有Python环境和来源链接工作区；strict codesign校验通过，但仍ad-hoc，非独立签名/公证发布包。最终二进制SHA256=4328e7636c2b00a53e45efc575c70e9811ebb3952cfe9f7f6eed650189652e01，install.json留存于build/acceptance/native-alert-install。正常退出旧App并确认端口关闭后原子切换，旧0.68.75/build153原包和匹配版本文件保留，初始154亦保留；ROLLBACK.md列步骤，未实际执行回滚门。原生实际显示运行版本.76、通知开关关闭、系统尚未授权、原监控暂停/收件箱关闭；未点授权/未发送系统通知/未注入主库样例，故实际授权、通知送达、Focus模式行为等仍未证明。

期间检测到并行提交与后端目录迁移，保留而不撤回。当前通知实现/版本已移至backend/monitor_alerts.py、backend/v5_workspace_ui.py、backend/version.py，兼容启动器仍在根目录。最初源码manifest因此失效，未声称冻结不变；source-manifest-after-layout.json记录重定位后实现字节相同，build脚本随布局更新。迁移后补build/native-alert-after-layout-tests.log 13相关通过/1warning（12.98s），并再次正常退出、确认端口停止、通过根兼容入口冷启动安装App，health ready，原生通知页关闭/未授权状态及原数据可读。18页遍历期间存在并行布局改动，不把该遍历或13局部测试扩展成整个重构验收。

主库前后均0活动Run/执行任务/模型调用/活动监控，登录与告警关闭，数据仍指向原root/data；没有新权限、自动研究或原生通知派发。回滚版本实现路径已补充为backend/version.py，不能只恢复旧二进制或误写根version.py。完整15Task/8Gaps、自动独立行为复验、真实通知与退出App通道、外部通知、三域真实发现/报告及正式分发门继续。此轮未执行Git提交/推送操作；不对并行任务的Git行为作否认。
