# FINAL Implementation Status

Date: 2026-08-27  
Authority: `FINAL/SRC_AI_Security_Research_OS_FINAL_2026-08-27`  
Meaning: `IMPLEMENTED` means the product path exists and has local automated evidence. `PROOF_PENDING` points to `FINAL_PROOF_BACKLOG.md` and is not release acceptance.

## F0 — convergence

| Tasks | Status | Evidence |
|---|---|---|
| F001–F005 | IMPLEMENTED | Six original F0 documents; package integrity 58 files; static contract 64 tasks |

## F1 — canonical core

| Tasks | Status | Evidence |
|---|---|---|
| F006–F007 | IMPLEMENTED | `target_specs`, resolver, versioned immutable `scope_snapshots`, confirmation gate |
| F008 / F013 / F057 | IMPLEMENTED | `analysis_runs`, eight-stage state machine, SSE, Pause/Resume/Stop, unique checkpoints and restart recovery |
| F009–F010 | IMPLEMENTED | Observation, Artifact pointer and Evidence normalization; tool/fork/compiler outputs enter Observation |
| F011 | IMPLEMENTED | Candidate/CanonicalFinding split and conservative legacy migration to `human_review` |
| F012 | IMPLEMENTED | Entity/Relationship stores; Traditional subjects and Web3 contracts/edges |
| F014–F016 | IMPLEMENTED | Version/license-aware capability registry, Scope/Policy check, DNS/IP/redirect/network/write guards |

## F2 — five-workspace product

| Tasks | Status | Evidence |
|---|---|---|
| F017–F023 | IMPLEMENTED | `/new`, `/runs/:id`, `/findings`, `/reports`, `/settings`; mode switch; one-field intake; Timeline; findings split; report picker; tool health |

Browser evidence: all five workspaces were opened in the in-app browser, Traditional/Web3 switch changed the target contract, and no console errors were recorded.

## F3 — Traditional SRC

| Tasks | Status | Evidence |
|---|---|---|
| F024–F026 | IMPLEMENTED / LOCAL_TOOL_PROOF | Traditional runtime plus dedicated Subfinder/httpx/Katana/Nuclei adapters; real httpx/Katana/Nuclei local-target runs and normalized outputs proved; authorized external-target proof remains queued |
| F027 | IMPLEMENTED | Role/tenant identities with opaque credential references and raw-secret rejection |
| F028 | IMPLEMENTED / LOCAL_TOOL_PROOF | Dedicated Semgrep/Gitleaks/Trivy argv-only adapters, secret-safe artifact boundary and normalized local fixture runs; authorized repository proof remains queued |
| F029 | IMPLEMENTED | Cross-observation correlation creates Candidate only, never Verified |
| F030 | IMPLEMENTED | Real local two-round HTTP replay Oracle, negative control, DNS/IP/scope/rate/budget guards |
| F031–F032 | IMPLEMENTED | Portable SHA-256 Proof Capsule and mandatory counterevidence before Verified |
| F033 | IMPLEMENTED | Coverage Ledger, Graveyard and resurrection APIs |

## F4 — reports

| Tasks | Status | Evidence |
|---|---|---|
| F034–F035 | IMPLEMENTED | Universal model, required/conditional completeness, no-hallucination consistency gates |
| F036–F039 | IMPLEMENTED | Generic/CN SRC, HackerOne, Bugcrowd VRT hook, Intigriti adapters loaded from FINAL configs |
| F040 | IMPLEMENTED | Markdown, JSON, HTML and applicability-limited SARIF exporters |
| F041 | IMPLEMENTED | Redacted ZIP bundle with report, structured model, completeness and evidence manifest |
| F042 | IMPLEMENTED | Report Center preview/completeness/export; no auto-submit |

## F5 — Web3 / Immunefi

| Tasks | Status | Evidence |
|---|---|---|
| F043–F044 | IMPLEMENTED / PROOF_PENDING | Web3 shell and versioned ProgramSnapshot; real Immunefi program import proof pending |
| F045 | IMPLEMENTED | Foundry/Hardhat/Solidity detection; real Forge compile; ABI/AST/bytecode normalized model |
| F046 | IMPLEMENTED / PROOF_PENDING | Contract/inheritance protocol graph; real proxy/deployment graph proof pending |
| F047 | IMPLEMENTED / PROOF_PENDING | Slither-first capability adapter contract; binary proof pending |
| F048–F049 | IMPLEMENTED | Invariant Registry, real Forge build and 64-run fuzz fixture |
| F050 | IMPLEMENTED / PROOF_PENDING | Echidna/Medusa/Aderyn/Halmos argv-only adapters behind truthful capabilities; binary runs pending |
| F051 | IMPLEMENTED | Production/public-testnet writes and real private keys denied |
| F052–F053 | IMPLEMENTED | Real Anvil upstream + local fork manager, state mutation, balance diff Observation and cleanup |
| F054–F055 | IMPLEMENTED / PROOF_PENDING | Program-aware impact/eligibility gates and Immunefi completeness; real program overlay pending |

## F6 — hardening and acceptance

| Tasks | Status | Evidence |
|---|---|---|
| F056 | IMPLEMENTED | Local-deterministic-first capability router and atomic request/tool/model budgets |
| F057 | IMPLEMENTED | Restart-to-paused and resume from first missing unique checkpoint |
| F058 | IMPLEMENTED | Event/context/report/artifact secret redaction |
| F059–F062 | IMPLEMENTED | 34 local regression/E2E/policy/adapter golden tests; real Forge and Anvil exercised |
| F063 | LOCAL PASS / HUMAN PROOF_PENDING | Browser five-workspace walkthrough passes; non-technical external user session remains queued |
| F064 | IMPLEMENTED | Updated `README.md` and `OPERATIONS.md` install/run/recovery/operator guidance |

## Current verification snapshot

```text
49 tests passed
PASS: 58 files verified
PASS: static contracts; 64 tasks
JavaScript syntax: PASS
Python compile: PASS
Five-workspace browser smoke: PASS, 0 console errors
```

Development paths are implemented. External/real-program/human proof is deliberately not claimed and is enumerated in `FINAL_PROOF_BACKLOG.md`.

## Strix installation readiness

- Strix CLI: installed and version-verified (`1.5.3`).
- Pinned sandbox: `ghcr.io/usestrix/strix-sandbox:1.3.0`; installed for Linux ARM64 with canonical RepoDigest `sha256:f6906c3114e504fd1a218fcf028d7a0e46851118403a438b63956de6ea7c4331`; image inspect and container smoke test pass.
- Model Provider: intentionally unconfigured; credentials are accepted only through the local Strix/environment configuration boundary.
- Capability API/UI now reports `available`, `configured`, `sandbox_ready`, and combined `ready` independently.
- Strix execution fails closed before DNS resolution, budget consumption, or process launch when either Provider configuration or the pinned sandbox is unavailable.
- Settings reads these readiness fields live; Provider credentials are intentionally configured only in the service environment and are never accepted or echoed by the web UI.

## Docker-free Native Agent readiness

- Native Agent Supervisor: implemented as a bounded read-only research loop; it can only choose `navigate` or `finish` and cannot invoke arbitrary Shell, submit forms, upload/download files or modify target state.
- Browser runtime: Playwright uses the installed system Chrome, so no Docker or second browser download is required.
- Every HTTP(S) browser request, including scripts and subresources, passes immutable Scope, DNS/private-IP and atomic request-budget checks; denied requests are retained as redacted browser evidence.
- Each run uses `data/agent_workspaces/<run_id>` with directory mode `700` and artifact mode `600`.
- Model responses create Candidate records only; they cannot create CanonicalFinding or bypass the independent replay/counterevidence gate.
- Provider remains intentionally unconfigured. Capability status truthfully reports browser available / provider required; deterministic native tools continue when the Agent is degraded.
- Current verification: 57 tests passed, Python compile PASS, JavaScript syntax PASS, real system-Chrome smoke PASS, Fieldwork cold-start HTTP 200.
