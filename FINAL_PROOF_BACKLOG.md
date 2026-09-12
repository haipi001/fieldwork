# FINAL Proof Backlog

Date: 2026-08-27  
Rule: implementation presence is not acceptance evidence. Items below stay unproven until the named evidence exists.

## Requires a real authorized Traditional SRC target

| FINAL item | What must be proved | Required evidence |
|---|---|---|
| F024 / S01 | Traditional runtime inventories a real authorized Web/API target | ScopeSnapshot, DNS pin, request ledger, normalized inventory Observations |
| F025 | Recon adapters normalize real tool output | ToolResultEnvelope → Observation trace for at least two capabilities |
| F026 | Browser/HTTP evidence is captured and redacted on a real flow | Request/response Artifact hashes, redacted export, replay reference |
| F027 / S02 | Multi-role and multi-tenant identity boundaries work with real test accounts | Opaque credential references, role matrix, no secret material in DB/events |
| F030 / S03–S05 | Controlled HTTP/state Oracle reproduces an authorized candidate | Two stable replays, negative control, counterevidence, confirmed scope |
| F031 / S06 | Proof Capsule is portable outside the current process | Export/import replay on a second clean local instance |

## Requires optional security tools

| FINAL item | Current state | Required evidence |
|---|---|---|
| F024 Strix | CLI v1.5.3 and ARM64 sandbox 1.3.0 installed; canonical RepoDigest `sha256:f6906c…4331`, image inspect and container smoke test passed; Provider/sandbox readiness are separate fail-closed gates; source snapshot isolation and sanitized artifact parser are implemented | Provider configuration, scoped dry-run, normalized live observations, timeout/stop test |
| F025 Nuclei/Katana/httpx/subfinder | Installed and versioned; dedicated bounded argv adapters and parsers implemented; httpx/Katana/Nuclei local target runs proved | Real authorized-target trace and Subfinder authorized-domain proof |
| F028 Semgrep/Gitleaks/Trivy | Installed and versioned; local vulnerable/clean fixture outputs normalized as Observations | Real authorized repository trace |
| F030 pentest-ai | ptai 1.3.1 installed; official bundled vulnerable/hardened demo and 3/3 replay passed; capsule integrity/scope/IP/budget/safe-replay adapter implemented | Real authorized candidate replay and second-instance portability proof |
| F047 Slither | Capability entry exists; binary missing | Slither-first local Solidity fixture run and normalized detector results |
| F050 Echidna/Medusa | Capability entries exist; binaries missing | Local property fixture, bounded fuzz run, failure/timeout normalization |
| F050 Aderyn/Halmos | Capability entries exist; binaries missing | Local fixture output and degraded-path tests |

## Requires a real Web3 program or public-chain read-only snapshot

| FINAL item | What must be proved | Required evidence |
|---|---|---|
| F044 / W01 | Immunefi ProgramSnapshot importer survives real program changes | Two timestamped versions plus field/rule diff |
| F045 / W02–W03 | Source commit aligns with deployed bytecode | Commit hash, compiler settings, bytecode hash, chain/block reference |
| F046 / W04 | Protocol graph represents proxy/implementation/external dependencies | On-chain read evidence and graph edges with Artifact references |
| F048–F049 / W05 | Generated invariants find or reject a real hypothesis | Forge invariant/fuzz corpus, seed, run count, trace and counterexample |
| F051 / W07–W08 | Production/public-testnet writes remain denied under live configuration | Denial audit against read-only RPC configs; no transaction broadcast |
| F053 / W06 | Local fork reproduces a program-relevant state transition | Fork block, calls, trace, storage/balance diff and reset proof |
| F054 / W09 | Impact and eligibility match current program rules | ProgramSnapshot rule IDs, feasibility analysis, known-issue/audit checks |
| F055 / W10 | Immunefi package satisfies a real program overlay | Custom questions, PoC rules, completeness gate and human review |

## Requires human product acceptance

| FINAL item | What must be proved | Required evidence |
|---|---|---|
| F063 / U01–U07 | A non-technical user completes the five-workspace flow without CLI help | Moderated session notes or recording, task completion, observed confusion list |
| Report usability | Human can resolve missing platform fields without invented content | Review checklist and corrected preview for each target platform |
| Recovery usability | User understands paused/recoverable after a forced restart | Forced-stop session followed by successful Resume from checkpoint |

## Already proved locally

- FINAL package integrity and 64-task static contract.
- Five-workspace browser navigation and Traditional/Web3 switch with zero console errors.
- Target canonicalization, immutable confirmed ScopeSnapshot and out-of-scope denial.
- Candidate does not automatically become Verified; demo/synthetic Oracle is rejected.
- Real local HTTP two-round replay with negative control.
- CanonicalFinding binds ScopeSnapshot and Evidence; portable Proof Capsule has SHA-256.
- Generic SRC, CN SRC, HackerOne, Bugcrowd, Intigriti and Immunefi completeness/render tests.
- JSON/Markdown consistency and SARIF applicability rules.
- Real Forge compile + 64-run fuzz fixture and real Anvil local-fork state mutation.
- Production/public-testnet write denial and real-private-key denial.
- Checkpoint resume without duplicate stages, atomic budgets, rate limiting and export redaction.

This file is a proof queue, not a release-complete declaration.
