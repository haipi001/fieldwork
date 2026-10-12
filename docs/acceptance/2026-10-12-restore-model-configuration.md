# 恢复预检：模型结算与执行配置

除原新调用ID和未核销检查外，追加6组模型账本整行双向对照：runtime_calls、runtime_usage、runtime_usage_reports、agent_task_usage、runtime_call_inputs、runtime_call_timings。相同调用ID在备份后结算、实际费用和任务用量变化也阻断，缺表/结构变化显示不可核对。

追加5组执行配置对照：Profile、路由决定、提供方配置、组的Profile/预算绑定、任务上下文/工具授权/路由需求/预算。提供方只比较ID/类型/名称/地址/模型/密钥引用/启用/配置元数据，不将健康探测时间或最近健康变化当作权限配置变化。仅返回差异计数，不输出模型输入、密钥引用内容或提供方地址。设置页显示对照和中英文阻断原因，原表及控制保留。

72相关通过，build/restore-model-configuration-final-tests.log；原夹具错误地要求released转settled被状态机拒绝，改为calling→保存用量→settled，原状态机未改，失败日志保留。相同调用ID结算变化被阻断；提供方健康更新不触发配置差异，禁用变化触发。读取字节不变。实际浏览器预检新增模型对照、18页144组合及既有操作通过，零模型调用，build/restore-model-configuration-browser.log。JS/diff检查通过，未全仓重跑。

正式替换/迁移、完整停机与授权冻结、原生升级仍未完成。这些对照不代替全部恢复条件验证，也没有替换当前DB或包库。源码0.68.73，安装仍待升级。完整开发规划继续。
