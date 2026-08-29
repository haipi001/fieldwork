# Final Migration Plan

## 本包的优先级

本包是**最终产品规范**。

之前两个包：
- 主 SRC AI Agent OS 包
- Web3 Dual Mode Extension 包

仍是有价值的：
- 架构资料
- tasks
- schema
- tool mapping
- P0-P6 / W0-W6 实现依据

但是如果它们与本包的：
- 产品页面数量
- 用户流程
- Canonical Finding
- Report Compiler
- 双模式 shared-core

产生冲突，以本包为准。

## 不推倒现有代码

### KEEP
- 已稳定的 core modules
- Scope
- Evidence
- Oracle
- checkpoint
- tool registry
- sandbox
- working adapters

### MERGE
- 42 UI screens → 5 workspaces
- separate report logic → Report Compiler
- raw scanner findings → Observations

### REFACTOR
- mode-specific if/else → DomainRuntime / CapabilityRegistry
- duplicated result schemas → CanonicalFinding

### ADD
- Target Resolver
- Observation Normalizer
- Entity Graph
- Correlation
- Completeness Gate
- Platform Report Adapters
- run timeline API

## Migration sequence

### F0 — Audit
No broad code changes.

### F1 — Canonical Core
TargetSpec / Observation / Evidence / Finding / Run state.

### F2 — Simple UI Shell
5 workspaces + mode switch.

### F3 — Traditional SRC Runtime convergence
Adapters → normalized observations → Oracle.

### F4 — Report Compiler
Generic/H1/Bugcrowd/Intigriti.

### F5 — Web3 Runtime
ProgramSnapshot / protocol / local fork / eligibility.

### F6 — Hardening
cost, recovery, e2e, security, docs.

## Backward compatibility

旧 Engagement 默认：
`mode=traditional_src`

旧 Finding：
- migration script maps fields
- if verification evidence absent → `human_review`
- 不允许迁移后自动标 `verified`
