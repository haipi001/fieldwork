# FINAL Gap Matrix

Status legend: `PROVEN`, `PARTIAL`, `MISSING`, `CONTRADICTED`.

## F0 task status

| Task | Status | Evidence |
|---|---|---|
| F001 repository/package audit | PROVEN | `FINAL_CONVERGENCE_AUDIT.md`, package hash/static checks, current source/DB inventory |
| F002 screen merge mapping | PROVEN | `FINAL_UI_MERGE_MAP.md` |
| F003 schema inventory | PROVEN | audit schema table + `FINAL_SCHEMA_MIGRATION.md` |
| F004 tool/version/license inventory | PROVEN | audit tool inventory; all security binaries missing; Python dependency metadata recorded |
| F005 report/platform inventory | PROVEN | audit report inventory and adapter gaps below |

## FINAL acceptance matrix versus current state

| ID | Requirement | Status | Current evidence / gap | Planned stage |
|---|---|---|---|---|
| U01 | Complete flow through 5 workspaces | CONTRADICTED | 8 primary views | F2 |
| U02 | Traditional/Web3 top switch | MISSING | traditional only | F2 |
| U03 | New analysis requires no tool choice | CONTRADICTED | YAML plus runtime selector | F2 |
| U04 | Run hides CLI noise by default | PARTIAL | event panel shows tool text directly | F2 |
| U05 | Findings default verified-only | CONTRADICTED | all statuses share one list | F2 |
| U06 | Candidate explicit section | PARTIAL | badges exist, no separate collapsed section | F2 |
| U07 | Report material completeness | PARTIAL | fixed five-checkbox human checklist, no adapter field computation | F4 |
| P01 | Target canonicalization | PARTIAL | URL normalization only; no TargetSpec/Resolver | F1 |
| P02 | Scope required before active action | PROVEN for current adapter | confirmation + gate; canonical binding missing | F1 |
| P03 | Tool output enters Observation first | MISSING | adapter stdout becomes events; demo writes candidate directly | F1/F3 |
| P04 | Candidate not automatically verified | PROVEN | explicit verify endpoint | F1 |
| P05 | Verified binds Evidence | PARTIAL | ledger integrity gate; no canonical Evidence schema/run artifacts | F1 |
| P06 | Finding binds ScopeSnapshot | MISSING | engagement only, mutable YAML | F1 |
| P07 | Pause/Resume | MISSING | Stop only | F1/F6 |
| P08 | Resume from checkpoint after crash | MISSING | restart marks interrupted | F6 |
| R01 | Generic SRC from CanonicalFinding | MISSING | legacy Markdown from legacy finding | F4 |
| R02 | HackerOne preview | MISSING | spec files only | F4 |
| R03 | Bugcrowd preview | MISSING | spec files only | F4 |
| R04 | Intigriti preview | MISSING | spec files only | F4 |
| R05 | Immunefi preview | MISSING | no Web3/ProgramSnapshot | F5 |
| R06 | Missing required fields block Ready | PARTIAL | manual checklist blocks export, not adapter schema validation | F4 |
| R07 | Renderer never hallucinates | PARTIAL | current renderer uses stored fields, but no missing-field contract | F4 |
| R08 | Evidence export redaction | PARTIAL | regex redaction; no artifact manifest/PII/private-key gate | F4/F6 |
| R09 | JSON/Markdown consistency | MISSING | JSON package absent | F4 |
| R10 | SARIF only applicable findings | MISSING | SARIF absent | F4 |
| S01 | Web/API inventory | PARTIAL | one synthetic surface | F3 |
| S02 | Multi-role identity | MISSING | role string only | F3 |
| S03 | Controlled verification | PARTIAL | demo-only oracle | F3 |
| S04 | Oracle replay | PARTIAL | replay count assigned, not real capsule replay | F3 |
| S05 | Counterevidence | PARTIAL | ledger negative control exists | F3 |
| S06 | Proof Capsule | PARTIAL | UI label/ledger only; portable directory absent | F3 |
| W01–W10 | Complete Web3/Immunefi path | MISSING | no Web3 runtime/data/UI/tooling | F5 |
| A01 | Out-of-scope blocked | PROVEN for HTTP prototype | host/port/path/method/date/DNS/redirect guards | F1/F6 harden |
| A02 | Rate limit enforced | PARTIAL | total request budget; requests-per-second not actually scheduled/enforced | F1/F6 |
| A03 | Destructive action approval/policy | PARTIAL | state change default false; no capability approval system | F1 |
| A04 | Secrets excluded from context/export | PARTIAL | report regex redaction only; subprocess events/context not fully protected | F6 |
| A05 | No real Web3 private key | MISSING implementation | policy documented only | F5 |
| A06 | Third-party active tests denied | PARTIAL | redirect/host guard, no canonical third-party capability policy | F1/F5 |

## Architectural component gaps

| FINAL component | Current analogue | Decision |
|---|---|---|
| TargetResolver | `normalize_target`, rule parser | NEW service; reuse parsing helpers |
| Engagement/Scope | YAML engagement + gates | REFACTOR into immutable snapshots/policy |
| Planner/Executor | `demo_run` | REPLACE |
| DomainRuntime | none | NEW shared interface + two domains |
| Capability/Tool Registry | three hard-coded statuses | REFACTOR |
| Observation normalization | none | NEW; mandatory before candidates |
| Entity/Target Graph | `surfaces` | REFACTOR |
| Correlation | direct synthetic hypothesis | NEW engine; migrate hypothesis data |
| Verification Oracle | demo verifier | REFACTOR interface; demo remains fixture |
| CanonicalFinding | legacy `findings` | NEW schema/store + conservative migration |
| Impact/Eligibility | none | NEW |
| ReportCompiler | `build_report` | REPLACE |
| PlatformAdapter | none wired | NEW from FINAL configs/templates |
| Web3 Runtime | none | NEW after shared core/traditional convergence |

## Release truth

The current app may be called a local traditional-SRC control-layer prototype. It must not be called FINAL, dual-mode, autonomous, submission-ready across platforms, or Web3-capable until the corresponding acceptance evidence exists.

