# V5-ORCH-02 Package Applicability Oracle Progress

日期：2026-10-01
版本：Fieldwork 0.57.0（Build 66 / Schema 23）

新增 `package_applicability_v1` 本地独立 Verifier Oracle。它只领取当前 Campaign/Scope/Policy 下，Claim 与 Replay Contract hash 明确绑定、Claim 派生自情报匹配、输入同时引用公告 Observation 与工具 Artifact 的任务。包指纹必须指向同 Engagement、同 Scope/Policy、非合成运行产生的已脱敏 Trivy Artifact；服务端在受限 Artifact 目录读取最多 256 KB，核对持久化 SHA-256、工具完成/成功状态以及 Graph 来源哈希，然后仅将 PURL、包名、安装版本与相关公告的明确受影响版本送往独立子进程。子进程验证 PURL 与包名/版本一致、扫描记录中同一包没有冲突版本、目标版本被公告明确列出；范围语义不作推断。

子进程输出的包名、版本、公告 hash 和 Artifact hash 再由服务端按当前持久化事实核对，签发后也在读取/晋升时重新核验。合成运行、跨 Scope 源、Artifact 改动、缺失 PURL、冲突扫描版本、公告新修订或仅人工自报指纹均不能成为当前可信的肯定适用性证明。情报 `verified` 的 `verification_scope` 明确为 `source_artifact_exact_package_version_only`；这不证明生产部署、运行时可达或漏洞可利用，更不自动创建 Finding。

晋升域另行强制区分：包适用性 Receipt 的 `integrity.promotion_domain` 为 `applicability_only`，只能支持情报匹配的有限范围 `verified`；即使 Receipt 为肯定，`canonical_result` 创建也会被拒绝。前端回执列表相应显示“包版本适用性可确认”，不把它称作漏洞结果晋升。

本能力只覆盖规范化 PURL 与明确列出的受影响版本，不是通用软件成分分析或沙箱隔离。`V5-ORCH-02` 仍需通用/远端受信任 Verifier 与更强 OS 沙箱等验收。

验证：`python -m pytest -q` 为 623 passed、3 skipped（1 条第三方弃用警告）；其后补强 Artifact 变化的 `current_inputs_match` 以及适用性/Canonical 晋升域分离，相关 38 项专项重跑通过，`node --check static/v5.js` 通过。`python -m compileall -q` 和 `git diff --check` 通过。`./scripts/build_macos_app.sh` 生成未安装的 `build/macos/Fieldwork.app`，`codesign --verify --deep --strict` 通过，包内版本为 0.57.0 / Build 66。未覆盖 `/Applications/Fieldwork.app`，未执行真实外部目标扫描。
