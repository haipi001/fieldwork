# FINAL Convergence Audit

Date: 2026-08-27  
Authority: `FINAL/SRC_AI_Security_Research_OS_FINAL_2026-08-27`  
Stage: F0 only (`F001`–`F005`)

## 1. Evidence baseline

- FINAL package integrity: `PASS: 58 files verified`.
- FINAL static contract: `PASS: static contracts; 64 tasks`.
- Current application baseline: `pytest 17 passed`; `app.py` and `adapters.py` compile; browser asset JavaScript passes `node --check`; local homepage returns HTTP 200.
- Current repository has no prior P0–P6/W0–W6 artifacts. The only pre-FINAL design input present is the original pasted traditional-SRC control-layer proposal plus the prototype built in this repository.
- Current source is an uncommitted, single-process FastAPI/Jinja/SQLite prototype: 10 application source/config files, approximately 1,426 lines excluding FINAL and dependencies.
- FINAL is now the sole product authority. Earlier implementation is migration input, not product truth.

## 2. Current product shape

The current product is a traditional-SRC control-console prototype, not yet the FINAL dual-domain Security Research OS.

Current user-facing workspaces:

1. 操作台
2. 漏洞与证据
3. 范围测试
4. 测试覆盖
5. 运行时
6. 报告审查
7. 攻击面记忆
8. 预算账本

This violates the FINAL five-workspace constraint and lacks the global `传统 SRC | Web3 / Immunefi` mode switch.

Current demonstrated path:

```text
YAML/rule text
→ draft Engagement
→ manual scope confirmation
→ demo Run
→ synthetic candidate
→ deterministic-demo-oracle
→ verified flag
→ one Markdown draft
```

The demo is deliberately non-networked and must not be represented as a real autonomous security analysis pipeline.

## 3. Feature disposition inventory

| Existing capability | Evidence | Disposition | FINAL destination / reason |
|---|---|---|---|
| FastAPI application and SSE | `app.py`, `/api/runs/{id}/events` | KEEP + REFACTOR | Stable local base; move to versioned `/api/v1`, typed service modules and canonical events. |
| SQLite local persistence | 15 current tables | KEEP + REFACTOR | SQLite is allowed for local prototype; introduce migration/version discipline and Postgres-compatible boundaries. |
| Engagement YAML manifest | `engagements.manifest` | REFACTOR | Split into TargetSpec, immutable ScopeSnapshot, ExecutionPolicy, mode/objective/budget. |
| Rule text parser | `/api/manifests/parse-rules` | KEEP + REFACTOR | Intake helper only; preserve source hash/warnings, emit versioned ScopeSnapshot, never silently infer authorization. |
| Host/method/port/path/date gate | `scope_decision` | KEEP | Strong seed for Scope/Policy service. Must become capability- and domain-aware. |
| DNS/IP pinning and redirect preflight | `dns_pins`, `/api/scope/redirect-chain` | KEEP + REFACTOR | Move into transport guard; add CIDR, Web3 RPC, third-party active-test rules and immutable policy binding. |
| Single-process async run | `runs`, `demo_run` | REPLACE | Replace demo-specific state with canonical pipeline state machine, tasks, checkpoint and resumable worker. |
| Run events / audit log | `events`, `audit_log` | KEEP + REFACTOR | Normalize event names to FINAL contract and separate user timeline from raw expert logs. |
| Adapter subprocess boundary | `adapters.py` | KEEP + REFACTOR | Preserve argv-only execution/timeout/stop; replace tool-name selection with CapabilityRegistry + ToolResultEnvelope. |
| Demo/Strix/pentest-ai detection | `/api/runtime` | MERGE | Becomes Tool Health inside Settings & Tools; no tool is a top-level product runtime. |
| Atomic request/tool/model budget | `budgets`, `consume_budget` | KEEP + REFACTOR | Map to Fast/Standard/Deep profiles, per-task limits, model/tool cost router and checkpoints. |
| Surfaces and hypotheses | `surfaces`, `hypotheses` | MERGE + REFACTOR | Migrate into Target/Entity Graph, Observation, Correlation and CandidateFinding. |
| Vulnerability fingerprint memory | `vulnerability_memory` | KEEP + REFACTOR | Retain dedup concept; key must include scope hash, target fingerprint, tool/config/version and mode. |
| Coverage records | `coverage` | REFACTOR | Adopt FINAL states: planned/attempted/blocked/completed/no-signal/signal/verified/disproved and Graveyard. |
| Embedded finding evidence JSON | `findings.evidence/counterevidence` | REMOVE after migration | Duplicates ledger and violates artifact-pointer design. |
| Evidence and counterevidence ledgers | two ledger tables | KEEP + REFACTOR | Convert to canonical Evidence objects with run, artifact pointer, MIME, target refs and redaction state. |
| Finding row | `findings` | REPLACE via migration | Insufficient for CanonicalFinding: no mode, scope snapshot, root cause, reproduction, impact, eligibility, confidence or platform metadata. |
| Demo oracle | `deterministic-demo-oracle` | KEEP only as fixture | Valid for local tests, never production verification. Real Oracle interface and replay capsule are NEW. |
| Markdown report builder | `build_report` | REPLACE | ReportCompiler must consume CanonicalFinding only and support versioned adapters/completeness. |
| Submission review checklist | `submissions` | KEEP + MERGE | Move into Reports workspace and SubmissionPackage lifecycle; retain no-auto-submit invariant. |
| Eight top-level navigation items | `templates/index.html` | REMOVE as top-level | Merge all capability views into five FINAL workspaces. |
| Traditional-only product | all current schemas/UI | REFACTOR | Add shared mode field and DomainRuntime; do not create a parallel Web3 application. |

## 4. Current schema inventory

| Current table | Current role | Material limitations |
|---|---|---|
| `engagements` | program, status, YAML manifest | no mode, TargetSpec, immutable scope version, policy id, objective |
| `runs` | queue/running status and request count | non-canonical states, no checkpoint, pipeline phase, parent/resume, scope binding |
| `events` | SSE messages | free-form event vocabulary and payload |
| `findings` | candidate/verified demo record | not CanonicalFinding; embeds duplicated evidence |
| `coverage` | four traditional surfaces | state vocabulary and dimensions do not match FINAL ledger |
| `audit_log` | engagement actions | useful but no actor/correlation/policy decision fields |
| `executions` | adapter subprocess metadata | no task/capability/policy/tool version/artifact refs/metrics |
| `submissions` | one report blob and checklist | no platform schema version, completeness, file manifest or package artifacts |
| `surfaces` | URL/method/role model | traditional-only and not a general Entity Graph |
| `hypotheses` | candidate statements | no observation/evidence inputs, confidence, graveyard or resurrection condition |
| `vulnerability_memory` | fingerprint occurrence count | no scope/config/tool version invalidation |
| `evidence_ledger` | finding-linked text facts | stores DB blobs; no run id/artifact pointer/MIME/redaction/target refs |
| `counterevidence_ledger` | negative controls | should become typed Evidence relations rather than a parallel truth system |
| `dns_pins` | DNS rebinding guard | useful transport state; no scope snapshot FK |
| `budgets` | atomic limits/usage | no task/model call detail, duration, cache or cost routing records |

Missing canonical stores: TargetSpec/TargetRevision, ScopeSnapshot, ExecutionPolicy, Observation, Artifact, Entity, Relationship, CandidateFinding, CanonicalFinding, VerificationAttempt/OracleResult, ImpactAssessment, EligibilityAssessment, ProgramSnapshot, Checkpoint, Capability/Tool health, ReportAdapterVersion, SubmissionPackage/File.

## 5. Current API/report/tool inventory

### API

Current API is unversioned and does not match `OPENAPI_FINAL.yaml`. It has useful prototype routes for engagement creation, scope checks, runs/events, finding verification and submission drafts. It lacks canonical target resolution, canonical run timeline state, finding list/detail contracts, capability registry, report platform preview/export and mode-aware resources.

### Reports

- Implemented: one universal Markdown string, manual review checklist, Markdown download.
- Not implemented: CanonicalFinding validation, platform picker, Generic/CN SRC/HackerOne/Bugcrowd/Intigriti/Immunefi renderers, JSON/HTML/SARIF, completeness/consistency validators, program overlays, submission directory/manifest/evidence artifacts.
- Existing FINAL adapter JSON/templates are specifications only; they are not wired into current code.

### Installed tools and licenses

No external security runtime/tool binary was found for Strix, pentest-ai, Nuclei, Katana, ProjectDiscovery httpx/subfinder/naabu, Nmap, ZAP, Semgrep, CodeQL, Gitleaks, Trivy, OSV-Scanner, Foundry/Forge/Anvil/Cast, crytic-compile, Slither, Aderyn, Echidna, Medusa, Halmos, Kontrol or Docker. `SRC_STRIX_COMMAND` and `SRC_PENTEST_AI_COMMAND` are unset.

Installed Python runtime dependencies:

| Dependency | Version | Installed metadata license |
|---|---:|---|
| FastAPI | 0.115.12 | MIT classifier |
| Uvicorn | 0.34.3 | BSD classifier |
| Jinja2 | 3.1.6 | BSD classifier |
| python-multipart | 0.0.20 | Apache Software License classifier |
| PyYAML | 6.0.2 | MIT |
| pytest | 9.1.1 | metadata returned UNKNOWN; verify before distribution |

No external tool license is currently introduced because none is installed or vendored. Every later dependency remains subject to a fresh license/version/maintenance/security gate.

## 6. FINAL acceptance status

- Traditional demo: partial synthetic path only; no real target analysis or Observation normalizer.
- Web3 local demo: absent.
- Five-workspace UX: absent.
- Canonical pipeline and schemas: absent.
- Scope and evidence safety seeds: materially present but not canonically bound.
- Reports: draft-only single-format prototype; not FINAL ReportCompiler.
- Pause/resume/checkpoint: absent; restart marks active runs interrupted.

Conclusion: retain the safety and evidence work, but converge around canonical data first. Do not cosmetically rename the current eight screens and call the FINAL product complete.

