# 01 — 一键分析完整用户链路

## 用户视角

```text
[传统 SRC] [Web3 / Immunefi]

┌──────────────────────────────────────┐
│ 输入 URL / Repo / Program / Contract │
└──────────────────────────────────────┘

Scope / Program Rules
[自动读取] [上传/粘贴] [我确认已授权]

              [开始分析]
```

之后进入单一 Run 页面：

```text
1 目标解析       ✓
2 Scope 定界     ✓
3 攻击面建模     ●
4 自动分析       ○
5 数据关联       ○
6 漏洞验证       ○
7 影响判断       ○
8 报告生成       ○
```

## 系统内部链路

### Phase 0 — Intake
- Detect target kind
- Canonicalize target
- Fetch project/program metadata
- Create Engagement

### Phase 1 — Scope
- Import / parse rules
- Build allow / deny
- Create execution policy
- Snapshot rules

### Phase 2 — Map
Traditional:
- domains
- endpoints
- routes
- parameters
- auth roles
- technologies
- source symbols

Web3:
- contracts
- proxies
- implementations
- roles
- token flows
- oracle/dependency graph
- state transitions

### Phase 3 — Analyze
先运行 deterministic / low-cost tools。
再根据 Coverage Gap 调度 Agent / high reasoning。

### Phase 4 — Normalize
所有工具输出转成：
- Observation
- Artifact
- Entity
- Evidence

### Phase 5 — Correlate
把多个弱信号合并成 Hypothesis / CandidateFinding。

### Phase 6 — Verify
机器 Oracle / deterministic assertion / local reproduction。
不能验证时进入 Human Review，不冒充 VERIFIED。

### Phase 7 — Impact
计算：
- real-world impact
- prerequisites
- affected users/assets
- repeatability
- exploitability/feasibility
- severity mapping

### Phase 8 — Eligibility
按平台 / 项目规则判断：
- in scope
- known issue
- duplicate/audit match
- impact in scope
- PoC complete
- special rule conflicts

### Phase 9 — Report
CanonicalFinding → Platform Adapter。

### Phase 10 — Export
生成：
- report.md
- report.json
- evidence/
- poc/
- reproduction/
- screenshots/
- traces/
- SARIF (where meaningful)
- platform-specific text
- manifest.json
