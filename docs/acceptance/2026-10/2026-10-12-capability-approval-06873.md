# 人工能力审批账本与界面

源码0.68.73/build151/schema41，V6 schema13。追加capability_approvals_v6，唯一grant绑定，批准/拒绝记录不可更新删除；不改原不可变授权行。审批预览绑定原grant/intent摘要、当前资格、用量和既有审批状态。决定必须严格bool确认、理由及匹配预览摘要，并在BEGIN IMMEDIATE事务复查。重复、变化、耗尽、过期或无域支持不能批准；支持拒绝当前授权。

审批只覆盖本地会话意图或具有既有模型网关绑定的第一方Worker，操作者与执行主体身份分开保存。支持已允许的读取/导航与既有模型路由；未支持写入和风险>=80不批准。执行资格复查仍保留主体、范围、策略、到期、撤销、次数和域门；策略记录只在审批匹配时解除risk>=50的等待，但预算/legacy guard拒绝与risk>=80隔离不受影响，策略证据记录approval_id。没有主动执行模型或工具。

能力页保留申请、审查、撤销，新增预览人工决定→明确确认和依据→记录决定；预览显示不能批准时仅可拒绝，已有决定不提供第二次操作。字段输出递归脱敏。

63相关测试通过（build/capability-approval-worker-repair-tests.log），涵盖不可变账本、确认类型、额外命令、旧预览、耗尽、危险操作拒绝、预算/风险门、第一方Worker主体区分及迁移；迁移专项5通过。隔离浏览器实际申请/审查/预览/确认/批准/撤销、18页144组合及旧操作通过，零模型调用和工具使用；build/capability-approval-final-browser.log。新版Swift构建与codesign严格校验通过，build/macos/approval-06873/Fieldwork.app。

首轮全仓1失败/958通过/3跳过：旧迁移夹具按原语句切片，新增表插在前面导致历史intent表未创建；已改为在既有表后追加，63相关复验通过。失败日志保留，修复后的全仓961通过/3跳过/1既有弃用警告，270.21秒（build/capability-approval-final-full-regression.log）。最后追加审批事件到原任务/Run的关联，50相关通过及最终18页144组合/实际审批撤销浏览器通过（build/capability-approval-event-final-tests.log、build/capability-approval-event-final-browser.log），零模型调用/工具使用。全仓收集早于最后事件关联改动，该补充由后续专项和浏览器覆盖，不冒称全仓再次重跑。

安装版仍0.68.72/build150/schema40；主库未升级、授权0、九业务表摘要不变、integrity ok（build/capability-approval-main-preservation.json）。原生工具仍报告Mac锁屏，之前手动解锁请求未获回复，未绕过锁屏。0.68.73尚未安装/原生验收；旧bundle依赖源码，源版本已前进，重启需要同时完成匹配bundle更新与正常schema备份升级，不能冒称已部署或旧bundle独立可回滚。升级在隔离库测试通过，主库实际升级待完成。

这不是所有高风险操作已可批准；写入域、外部扩展、完整后台质量、三域规模和发布门仍未完成，完整目标继续。
