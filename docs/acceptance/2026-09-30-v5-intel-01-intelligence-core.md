# V5-INTEL-01 Vulnerability Intelligence Core

日期：2026-09-30
版本：Fieldwork 0.53.0（Build 62 / Schema 23）

新增离线情报来源适配器接口，以及 OSV 格式和规范化包公告两个适配器。导入时仅保存公告 ID、受影响包/枚举版本、有限范围事件和来源/许可信息；不调用外部源，也不复制公告自由正文。来源 URL 仅接受不含凭据、查询参数或片段的 HTTPS。记录按来源、外部 ID 和规范化内容 hash 去重，保留修订历史。

OSV 字段选择参考 [OpenSSF OSV Schema](https://ossf.github.io/osv-schema/)；范围解释依赖生态语义，因此本阶段不根据范围单独断言某版本受影响。

`POST /api/v1/intelligence/fingerprint` 接受人工或同 Engagement Observation/Artifact 来源的包指纹，绑定当前 Campaign Scope/Policy。生态与包名严格匹配；版本明确列在受影响枚举中时标为 `verification_required`，版本未知或只有范围信息时保守标为 `uncertain`，不把范围规则误判为肯定适用。匹配生成带来源 hash 的公告 Observation 与候选 Hypothesis，以及 `derived_from` 关系；不会生成 Candidate Finding、Canonical Finding 或 Canonical Result。

人工评估可设置适用/不适用/不确定/待验证，但不能自行设置 `verified`。标记已验证须有与同 Campaign Claim 绑定、输入仍有效的独立 V5 Receipt；Claim 必须从该情报假设派生，Receipt 必须覆盖公告来源 Observation。验证状态通过单独的 Verification 节点与 `verified_by` 边呈现，不修改 Receipt 已冻结的输入节点。公告新修订版或 Scope/Policy 变化时，历史匹配保留，但 `current=false` 且禁止继续评估/验证。

验收覆盖适配器、去重、保守适用性、跨项目来源拒绝、无自动 Finding、人工评估边界、独立 Receipt 与公告来源绑定、修订/Scope 失效。Schema 无变化；回滚移除 Intelligence 路由与服务文件，既有情报记录保留。

验证结果：全量测试 `605 passed, 3 skipped`；Python 编译、`git diff --check` 与未安装 macOS bundle 的 deep/strict 签名校验通过。未拉取外部情报、未创建真实 Campaign 匹配或覆盖安装版。

下一任务：`V5-ORCH-02` Critic / Synthesizer / Verifier workers。
