# FINAL UI Merge Map

## Final navigation contract

Only these five top-level workspaces will remain:

1. 新建分析 (`/new`)
2. 分析过程 (`/runs/:runId`)
3. 漏洞结果 (`/findings`, `/findings/:findingId`)
4. 报告中心 (`/reports`, `/reports/:findingId`)
5. 设置与工具 (`/settings`)

The header contains a persistent `传统 SRC | Web3 / Immunefi` mode switch. Mode filters/creates Engagements; it never mutates the mode of an existing Engagement.

## Existing view mapping

| Current primary view | Final workspace | Placement | Disposition |
|---|---|---|---|
| 操作台 / project list | 新建分析 + 分析过程 | recent projects below intake; selected run summary in Run | MERGE |
| 漏洞与证据 | 漏洞结果 | verified-first cards; candidate collapsed section; Evidence drawer | MERGE |
| 范围测试 | 新建分析 | Scope card; redirect/DNS details in advanced policy drawer | MERGE |
| 测试覆盖 | 分析过程 | expandable Coverage tab | MERGE |
| 运行时 | 设置与工具 | Tool Health default; registry/commands expert drawer | MERGE |
| 报告审查 | 报告中心 | platform picker, completeness, preview, human export gate | MERGE |
| 攻击面记忆 | 分析过程 | Target Graph / Correlation expert tabs | MERGE |
| 预算账本 | 分析过程 + 设置与工具 | current-run stats; global budget profile/settings | MERGE |

No current capability is silently dropped. Existing panels cease to be top-level navigation.

## Workspace content contract

### 1. 新建分析

Default layer:
- ModeSwitch
- one TargetInput
- Scope/Program Rules card
- Fast/Standard/Deep profile
- Start Analysis
- recent Engagements

Advanced drawer:
- parsed manifest/scope warnings
- exact host/path/action policy
- local lab/private-network opt-in
- account/credential handles (never plaintext)

Existing YAML editor becomes expert-only. The runtime/tool picker is removed from intake; Planner selects capabilities.

### 2. 分析过程

Default layer:
- canonical phase timeline
- current explanation (“what/why”)
- asset/candidate/verified counts
- time/cost/budget
- Pause / Resume / Stop

Expanded layer:
- activity/events
- Coverage Ledger
- Evidence counts
- agent/tool health

Expert layer:
- raw logs/command traces
- Target/Entity Graph
- HTTP request/response
- DNS pins/transport decisions
- fuzz/fork/protocol traces

The current terminal-like event box moves to expert activity, not the default experience.

### 3. 漏洞结果

Default:
- Verified Findings only
- impact, verification level, evidence count, report readiness

Separate collapsed section:
- Candidate / Human Review / Disproved / Graveyard

Detail drawer/page:
- CanonicalFinding
- scope proof
- reproduction/oracle/counterevidence
- Evidence Capsule/integrity
- impact/eligibility

### 4. 报告中心

- choose CanonicalFinding
- choose adapter: Generic, CN SRC, HackerOne, Bugcrowd, Intigriti, Immunefi, Markdown, HTML, JSON, SARIF where applicable
- adapter-derived completeness and missing fields
- preview and consistency warnings
- explicit human export button
- exported SubmissionPackage index

The current generic checklist is retained as an additional human gate, not as platform completeness truth.

### 5. 设置与工具

Default:
- model provider/router
- Fast/Standard/Deep budgets
- sandbox status
- tool/capability health

Expert drawer:
- CapabilityRegistry / ToolRegistry
- Skills/MCP
- adapter argv templates and versions
- execution policies/environment handles
- raw audit/maintenance

## Transition rules

1. Build the five-workspace shell only after canonical APIs exist enough to support it (F1 before F2).
2. Keep current routes/API operational during migration via compatibility adapters.
3. Do not relabel synthetic demo metrics as real analysis.
4. Default Findings query is verified-only; candidate count remains visible.
5. Web3 mode initially may show capability-degraded state, but never fake analysis results.
6. Remove the eight-item sidebar only after every current capability has a mapped drawer/tab and an acceptance test.

