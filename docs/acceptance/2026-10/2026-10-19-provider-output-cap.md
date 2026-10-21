# 可配置生成输出上限与预算约束

2026-10-19。完整UI-00→QA-07/15Task/8Gaps继续开放。

## 确认的问题

`v5_model_inputs.input_plan`原先固定`min(4000,任务总额度-输入,上下文余量)`。模型页只有总额度/上下文，无法单独调整生成上限。UTF-8字节加256的输入预算是保守估计，不是精确tokenizer计数；此前实际输入827而生成在3438触发length，单凭增加Profile总额度也仍受固定4000限制。不能据此断定所有输出失败的原因，也不能未经验收宣称DeepSeek稳定。

## 实现

ProviderMetadata与单字段PATCH新增严格整数max_output_tokens，1–100000，默认4000；旧metadata未包含字段时仍为4000。safe_metadata公开此非敏感配置；生成计划取配置上限、任务总预算扣除输入、上下文余量三者最小值。无效原始metadata拒绝派发，不转成无限额度；原有输入计数、预留、用量/截断检查不放松。

模型与路由页新增Provider表单与已有Provider的“生成输出上限/保存输出上限”。保存需确认配置变化与新Profile版本；配置hash变化使旧Profile失效，不能偷偷改变已冻结路线。与费用同时PATCH用一次json_set更新，保留其他metadata、模型及凭据。语言切换保留已有Provider未保存的费用/输出编辑值，原表单、健康、费用和路由入口保留。

## 验收

- `build/provider-output-cap-current-regression.log`：**37 passed，19.60秒，1依赖warning**。包括旧默认4000、新9000、任务/上下文余量限制、非法/布尔值拒绝、同时更新费用/输出保留与旧Profile失效；实际本地HTTP模型协议夹具收到max_tokens=9000且记录同值预检，另有旧990用例保留。是协议夹具，不是真实推理。
- `build/provider-output-cap-ui.log`：真实隔离API配置，创建8000/修改12000/费用保留，未保存12345切换语言保留、Profile/路由/策略/用量原验收继续通过；1次本地健康GET，0模型请求，page_errors为空。
- 安装0.68.76/build154源链接App正常退出，确认8000端口关闭后重启。原生模型与路由页实际显示生成输出上限4000和保存按钮；没有编辑或保存真实Provider。主库分析/Agent/guided/verification活动均0，重启前后全部Provider metadata摘要完全一致（`build/output-cap-main-provider-before.json`），未自动迁移/上调DeepSeek预算或创建Profile。

## 剩余限制

本轮未新增付费模型调用。此前DeepSeek schema_mismatch与output_truncated两次失败、已结算66290保守micros、0Claim/0候选的记录仍有效，未转为成功。新配置让限制可调整，但真实模型稳定输出及草稿→候选→独立复验/报告仍需实际验收；schema提示补全也尚未证明稳定性。最大上下文为操作员配置，字节估计不是准确计数；平台自身输出限制仍可能更小。完整三域质量、原生三次清洁启动、自动化/通知、来源/规模/迁移/正式分发等继续开放。未提交推送Git，未把旧全量回归冒充本轮当前全量。
