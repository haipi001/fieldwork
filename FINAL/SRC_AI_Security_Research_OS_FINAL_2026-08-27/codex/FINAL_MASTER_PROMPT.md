# FINAL CODEX MASTER PROMPT

You are taking over an existing project previously described as `SRC AI Agent OS`.

You may already have:
- a base P0–P6 development package
- a Web3 / Immunefi dual-mode extension package
- an existing prototype/repository

A new package is now authoritative for the **final product shape**:

`SRC_AI_Security_Research_OS_FINAL_2026-08-27`

## Final product

The user must be able to:

```text
Input Target
→ Analyze Target
→ Produce Structured Data
→ Analyze/Correlate Data
→ Verify Vulnerability
→ Assess Impact/Eligibility
→ Produce Submission-Ready Material
```

This must work through the same shared system for:
- Traditional SRC
- Web3 / Immunefi

## User experience

DO NOT restore dozens of expert screens.

Top-level UI:
1. New Analysis
2. Run
3. Findings
4. Reports
5. Settings & Tools

Global:
`传统 SRC | Web3 / Immunefi`

Advanced technical details are drawers/tabs inside those workspaces.

## Architecture

Implement:

```text
TargetResolver
→ Engagement/Scope
→ Planner/Executor
→ DomainRuntime
→ Capability/Tool Registry
→ Observation Normalization
→ Entity/Target Graph
→ Correlation
→ Verification Oracle
→ CanonicalFinding
→ Impact/Eligibility
→ ReportCompiler
→ PlatformAdapter
```

## First action: F0 only

Before broad implementation:

1. Read the entire repository.
2. Read prior P0-P6/W0-W6 docs if present.
3. Read this final package.
4. Produce:
   - `FINAL_CONVERGENCE_AUDIT.md`
   - `FINAL_GAP_MATRIX.md`
   - `FINAL_UI_MERGE_MAP.md`
   - `FINAL_SCHEMA_MIGRATION.md`
   - `FINAL_IMPLEMENTATION_ORDER.md`
   - `FINAL_RISK_REGISTER.md`

### Audit every existing feature into:
- KEEP
- MERGE
- REFACTOR
- REPLACE
- REMOVE
- NEW

Do not enter F1 automatically.

## Canonical pipeline

Raw scanner/tool output cannot go directly to reports.

Must be:

```text
Tool Result
→ Observation
→ Evidence
→ Candidate
→ Verification
→ CanonicalFinding
```

Only CanonicalFinding is consumed by ReportCompiler.

## Report adapters

Support base adapters:
- Generic SRC
- HackerOne
- Bugcrowd
- Intigriti
- Immunefi
- JSON
- Markdown
- HTML
- SARIF where appropriate

Platform/program custom fields must be overlays.

Do not hardcode one universal Markdown as if every platform were identical.

## Web3 execution

Default policy:
- Production chain: read only
- Public testnet: read only
- Local fork: read/write
- Local devnet: read/write
- real private key: not required
- external auto-submission: disabled

## Tool strategy

Prefer adapting mature tools.

Traditional references include:
- Strix
- pentest-ai
- PentAGI
- Argus
- SWE-agent ACI
- ProjectDiscovery stack
- ZAP
- Semgrep
- CodeQL
- Gitleaks
- Trivy
- OSV

Web3 references include:
- Foundry / Anvil
- crytic-compile
- Slither
- Aderyn
- Echidna
- Medusa
- Chimera
- crytic/properties
- Halmos
- Kontrol
- revm

Evaluate current license, version, maintenance and runtime cost before introducing a dependency.

## Cost

Use:
parser/deterministic
→ static tool
→ correlation
→ cheap model
→ targeted reasoning model
→ targeted fuzz/formal

Do not dump complete raw logs/repositories into high-reasoning model context.

## Completion criteria

The system is not complete because “tools run”.

It is complete when one authorized demo target can go:

Target
→ Scoped Run
→ Observations
→ Verification
→ Verified Finding
→ Evidence Capsule
→ Report Preview
→ Exported Submission Package

for Traditional SRC, and a local-only Web3 demo can complete the corresponding Web3 path.

Stop after F0 documents and present the migration plan.
