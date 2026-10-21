# 真实模型自动候选衔接与资产噪声修复 — 2026-10-21

## 验收实现
新增显式开关 `FIELDWORK_LIVE_AUTO_CANDIDATES=1` 的实际Nuclei→持久Observation→Campaign来源→真实DeepSeek→批准策略→自动候选用例；原实际工具/模型手工审阅用例仍保留。默认跳过，不在普通回归中产生付费调用。未注入扫描输出、Claim或模型答案。原工具用例的真实correlate也会产生工具线索，自动策略结果以其确切候选ID和approval_source=resume_policy单独核对，不能把工具已有线索当模型新候选。

所有外发均为隔离的公开loopback合成材料，模型凭据经既有服务设置读取后仅复制至隔离secret root，不打印、不修改主库配置。每场景一模型调用；执行前后主库快照完全一致。

## 真实运行与修复
第一次实际正例/修复对照各1调用。正例模型3Claim、策略2候选；修复对照模型也给出3个基于surface.technology和surface.api_description的资产事实草稿，其中2个被自动保存。没有Finding，但这是自动安全候选的噪声缺口。真实验收终态1passed/1failed/31.23秒，完整失败保留 `build/live-auto-candidate-policy-20261021.log`。

生产修复：自动衔接不再将没有安全类别的research_hypothesis自动保存。纯surface支持保留为图谱草稿、累计non_security_drafts；若含安全支持但类别有歧义，转手工审阅。原手动审阅入口不删、不静默丢Claim。界面增加“仅资产观察，未自动保存安全候选”和保留资产草稿数量。

修复后实际正例再次成功：3Claim、2个未验证安全候选、1个普通资产草稿保留；一次实际模型调用settled，用量973输入/2994输出，按配置保守费率记录39670微美元。exact policy revision和resume_policy审计来源核对，重复tick不再调用模型。

修复对照这次模型返回无效JSON（finish_reason=stop，错误摘要保留）；task paused、policy blocked、0Claim/0策略候选，已知用量716输入/1117输出settled，18330微美元保守计费。没有修复或补造模型答案、没有自动重试。该对照未通过正向有效输出验收，`build/live-auto-candidate-policy-gated-20261021.log`精确exit1、1passed/1failed/32.69秒；不再为取得绿结果反复调用。

四次真实调用均settled，合计按配置10USD/百万token保守上界记109610微美元（0.10961USD），不是实际账单报价。每次用量、错误和原产物见 `build/actual-auto-candidate-security-summary-20261021.json`及两个实际输出目录。

此前有效修复对照的3个真实Claim已按原支持ID/类型离线核对，全部surface-only，无安全类别：`build/archived-fixed-asset-classification-20261021.json`。这只是保存输出的离线分类证据，不冒充修复后新模型成功或完整重放。

## 回归
- `build/auto-candidate-security-gate-final-tests-20261021.log`精确exit0：60passed/1依赖弃用warning/21.34秒。新增surface-only即便被模型描述为安全假设也只保留草稿，不自动生成候选。
- `build/auto-candidate-security-gate-ui-20261021.log`精确exit0：3组11任务45隔离UI写入、0pageerrors、1本机HTTP模型协议夹具；新增asset-only状态、保留数、无结果按钮断言，原8显示组合/精确候选衔接/关闭停止轮询等保留。
- 默认运行两个live模块：4skipped/1warning/0.74秒，证明付费调用需显式开关；跳过不计功能完成。
- git diff --check通过，6文件源码摘要 `build/actual-auto-candidate-security-source-20261021.json`。

## 原生/数据与剩余
CUA正常退出App确认旧health停止，重新启动后智能体页与工具就绪可读，安装中源码关联App已重载修复；没有在原生主库制造测试记录或启用策略。主库前后Provider/Profile/活动计数一致，runtime_calls0、启用策略0。真实4次调用都属于隔离验收库。

无schema迁移、提交/推送或独立打包。1660默认完整回归早于本增量。实际模型正例支持自动候选局部链；修复对照有效响应、安装版完整正向研究、独立复验/报告自动链、三域/holdout/8-16-32质量成本、持久Worker与正式分发仍开放。完整UI-00→QA-07、15Tasks/8KnownGaps保持进行中。
