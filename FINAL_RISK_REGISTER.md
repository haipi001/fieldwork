# FINAL Risk Register

Scale: likelihood/impact `Low`, `Medium`, `High`, `Critical`.

| ID | Risk | Likelihood | Impact | Evidence / trigger | Mitigation / gate | Owner stage |
|---|---|---|---|---|---|---|
| R-01 | Current synthetic demo mistaken for real vulnerability validation | High | Critical | `deterministic-demo-oracle`, synthetic response strings | Fixture labeling; conservative migration to human_review; no production claims | F1 |
| R-02 | Eight-screen prototype cosmetically renamed instead of converged | High | High | current sidebar has 8 items | enforce `FINAL_UI_MERGE_MAP`; five-workspace E2E before removing compatibility views | F2 |
| R-03 | Raw tool stdout bypasses Observation normalization | High | Critical | external adapter streams lines directly to events | ToolResultEnvelope + Observation-only adapter contract; block direct finding writes | F1/F3 |
| R-04 | Mutable YAML scope causes findings to lose authorization provenance | High | Critical | findings reference engagement, not snapshot | immutable ScopeSnapshot FK on every run/action/finding/report | F1 |
| R-05 | Legacy `verified` values overstate proof | High | Critical | demo verifier assigns status after checking ledger shape | migrate to human_review unless genuine oracle attempts/capsule proven | F1 |
| R-06 | Report logic hallucinates or omits platform-required fields | High | High | one generic Markdown renderer | CanonicalFinding-only compiler, versioned adapter configs, completeness/consistency gates | F4 |
| R-07 | Web3 implemented as duplicate product/core | Medium | High | no Web3 code yet; temptation to bolt on | core dependency rule and DomainRuntime interface before F5 | F1/F5 |
| R-08 | Mainnet/testnet state mutation or real-key exposure | Medium | Critical | Web3 execution absent, controls unimplemented | read-only RPC guards, local fork/devnet writes only, logical secret handles, denial tests | F5/F6 |
| R-09 | Rate policy appears enforced but only total count is limited | High | High | `max_requests_per_second` parsed but scheduler does not enforce it | token-bucket transport limiter with concurrency and timing tests | F1/F6 |
| R-10 | DNS pinning blocks legitimate CDN rotation or permits unsafe override | Medium | Medium | exact address-set comparison | snapshot-aware policy, explicit re-resolution workflow, audit/approval—not silent relaxation | F1/F6 |
| R-11 | Sensitive data leaks through events, DB blobs or model context | High | Critical | regex only applied to report strings; subprocess lines stored | central SecretStore/redaction pipeline before persistence/context/export; PII/private-key tests | F6 |
| R-12 | SQLite monolith and large DB blobs degrade/corrupt local runs | Medium | High | evidence/report content in SQLite, no artifact store | filesystem artifact store, pointers/hashes, WAL/backup/integrity/migrations | F1/F6 |
| R-13 | Crash recovery loses progress | High | High | startup marks active runs interrupted; no checkpoints | resumable task/checkpoint state, idempotency keys, recovery E2E | F1/F6 |
| R-14 | Adapter stop does not cover descendant processes/containers | Medium | High | process handle tracks only direct child | process-group/container lifecycle, kill escalation and orphan tests | F3/F6 |
| R-15 | Tool cost events can under-report real network/model use | High | High | adapter self-reports `SRC_CONTROL_EVENT` | enforcement at transport/model gateway, not trust-only event reporting | F1/F6 |
| R-16 | External dependency license/version assumptions become stale | Medium | High | all security tools absent; FINAL matrix says re-check | dependency gate and lockfile/SBOM; no installation before review | every tool slice |
| R-17 | Codebase remains unversioned/unrecoverable | High | High | all files untracked, no baseline commit | after F0 approval create reviewed baseline commit; exclude data/logs/venv | before F1 |
| R-18 | Existing data migration silently drops or upgrades truth | Medium | Critical | 15 legacy tables and overlapping evidence stores | backup, idempotent migration, row mapping/reject log/count/hash, dual-read golden tests | F1 |
| R-19 | Candidate dedup suppresses a distinct vulnerability | Medium | High | fingerprint normalizes IDs/title | retain occurrences/evidence, scope/config/tool-version keys, human resurrection/Graveyard | F1/F3 |
| R-20 | FINAL package drifts or is accidentally edited | Low | High | authoritative package copied into worktree | keep package read-only in practice; rerun SHA256 verifier before each phase | all gates |
| R-21 | F0 scope creep changes product before migration is approved | Medium | High | development was active when FINAL arrived | freeze business code now; only six audit docs until user authorizes F1 | F0 |

## Immediate blockers before F1

1. User review/approval of the six F0 documents and migration order.
2. Establish a recoverable version-control baseline for the current prototype and FINAL package references.
3. Decide whether canonical backend remains a staged modular FastAPI refactor (recommended) or is split into services; FINAL currently favors keeping the stable backend.
4. Select a local traditional fixture and local Web3 fixture for future acceptance without external active testing.

