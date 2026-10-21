# 实际工具/模型到受审阅候选：信号正例与修复负例

2026-10-19。完整UI-00→QA-07、15Task/8Gaps继续开放。

## 设置与数据边界

使用已保存的DeepSeek服务，在隔离临时库创建Provider/Profile：生成上限9000，任务及Profile总token上限16000，单例费用上限200000保守micros，并发1。只发送明确公开的构造loopback Nuclei资料；未发送主库私有材料或凭据正文，未修改主库Provider/Profile。

实际安装Nuclei经受控broker执行、生产解析登记Observation/Evidence；Campaign-source-plan与创建API关联资料；真实模型返回经ResearchOutput严格校验的结构化研究草稿；选一条Claim经新candidate-plan/显式审阅POST保存未验证候选。没有注入scanner输出、模型答案或Claim。测试会预先设置已知靶场行为及扫描模板，不是holdout质量评估。

## 当前两例结果

| 场景 | 模型任务 | 模型Claim | 已审阅新增模型候选 | 正式Finding | 输入/输出tokens | 保守cost_micros |
| --- | --- | ---: | ---: | ---: | --- | ---: |
| 扫描信号正例（缺CSP/模板命中） | succeeded | 3 | 1，scanner_match | 0 | 820/2377 | 31970 |
| 修复负例 | succeeded | 0 | 0 | 0 | 624/541 | 11650 |

各1 passed/1 deselected；正例21.04秒、修复12.96秒，分别见`build/actual-model-candidate-output-cap-20261019.log`与`build/actual-model-candidate-output-cap-fixed-20261019.log`。两例研究调用均settled，预留16000，合计43620本地配置的保守cost_micros，非供应商账单。

原扫描器correlate在正例已创建2条工具候选、修复0条；本次新增的1条是单独的模型Claim审阅候选，不冒称数据库总共只有1条。原扫描器证据artifact SHA与本次模型报告一致，已在聚合sidecar断言。

原始工具产物、冻结输入、结构化Claim/任务/用量/诊断、候选来源及计划SHA保存于`build/actual-model-candidate-handoff-output-cap-20261019/`。`acceptance-summary.json`关联工具基线、模型阶段与两例报告SHA。正例新增候选status=candidate、independently_verified=false；分类依据登记观察为scanner_match，尚无对应独立漏洞行为证明。

## 已解决到哪一层

新增生成额度配置及较大总预算下，实际模型到受审阅候选的连接已取得一次正例和一次修复负例证据。上一轮schema_mismatch与output_truncated失败、66290保守micros记录继续保留。不同配置和不同生成结果不能用于断言“提高上限必然解决失败”，2个成功样例不证明稳定性或发现质量。

本例只有受控扫描信号与未验证模型候选，未自动签发独立回执或生成正式报告。此前HTTP授权回执/报告属于另一条预置候选验收链，本次还未连到对应实际模型发现。未完成三域正例/修复/合法反例集合、保留样本统计、三次原生清洁启动、真实模型8/16/32质量/成本等产品门。

## 主库/安装状态

本轮生产代码沿用上一轮已安装的0.68.76/build154源链接代码，只更新 opt-in 验收配置与证据。主库runtime_calls仍0，Provider metadata与上一轮安装前摘要完全一致；实际修改9000/16000仅发生于隔离库。未创建主库研究项目/任务，未提交推送Git，未额外重复付费调用。

完整自动化、独立确认与三域质量等原目标继续推进，不能据此标记全部规划完成。
