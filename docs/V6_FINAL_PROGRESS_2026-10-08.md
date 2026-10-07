# V6 Final development progress

Source plan: `fieldwork_v6_final_v7_ready_development_bundle` supplied on 2026-10-08.
Baseline: `codex/src-deepening-20260924` at `a1d56c7`, source 0.68.20 / build 98 / schema 28.
Integration branch: `codex/v6-final-control-plane`.

## P0.0 baseline

- Bundle SHA256 manifest passed.
- Source worktree was clean at baseline.
- Main database was copied with SQLite online backup to a temporary file, copied again to a recovery file, and the recovered copy returned `PRAGMA integrity_check=ok`. Main database was not migrated or restored.
- Repository-wide baseline tests: 838 passed, 4 skipped, 1 deprecation warning in 247.24 seconds. The test process began before the V6 files were added.
- Post-change repository-wide regression: 843 passed, 4 skipped, 1 deprecation warning in 222.80 seconds. The V6 bridge/API tests were 5 passed. Python compilation and `git diff --check` passed.
- Local `main` and this branch have common ancestor `0034390`; `main...HEAD` is 0/24 commits. This only measures local refs; remote convergence remains a separate decision and no merge has been performed.

## P0.1 runtime compatibility bridge

- `v6_runtime_bridge.py` reads `v5_events`, `runtime_calls`, `agent_tasks`, `runner_registry_v5`, and `agent_registry_v5` without creating another queue, budget ledger, or event table. Existing `call.*` records are correlated to their task and runner.
- `/api/v1/v6/campaigns/{campaign_id}/runtime-events` exposes paged, payload-redacted event views. Model calls are labeled snapshots because their mutable ledger state cannot reconstruct every historical transition.
- `/api/v1/v6/tasks/{task_id}/model-call-snapshots` and `/api/v1/v6/runners` expose read-only views.
- Unknown principal, agent/task binding, node, signature, and attestation remain null. A Runner's advertised capabilities remain descriptive, not authorization.
- Synthetic bridge and API tests cover correlation, conflicting ownership, redaction, pagination, and absent trust claims.

### Remaining P0.1 work

- Capture all first-party tool execution transitions as canonical events; the current V5 event stream does not contain every model-call state transition.
- Establish authoritative AgentIdentity links for research workers rather than inferring them from roles or the observed-agent registry.
- Add an immutable event lineage for gateway execution in P0.5 and P0.6.

## Overall release state

V6 is in development. P0.2–P0.4 are in progress; P0.5 through P2.5 and all release gates beyond baseline remain open. This file records source progress only; it is not evidence that the installed app or a running service has been upgraded.

## P0.2 passive ActionIntent capture

- Source is now 0.68.21 / build 99 / schema 29. `v6_schema.py` adds only `action_intents_v6` and metadata, with update/delete triggers; older runtime, budget, and evidence tables are not copied or rewritten.
- `/api/v1/v6/action-intents` accepts metadata-only proposals. It binds a task to a real, current Run, Campaign, confirmed Scope snapshot and Policy; only the hashes and IDs of Scope/Policy are stored. Missing Run and stale context fail closed. Caller text is restricted to opaque Agent ID, capability/operation names, a query-free resource identifier, a SHA256 argument hash, and a fixed reason code.
- The principal is the server-side local session principal; the Agent ID is explicitly marked as claimed. The record status is only `proposed`, with no dispatch or authorization side effect.
- Isolated 28→29 migration test proves backup/recovery and historical row retention. Intent tests prove immutability, redaction constraints, stale context denial, and no model-call or event write.
- Repository-wide regression after ActionIntent/schema changes: 846 passed, 4 skipped, 1 existing deprecation warning in 222.18 seconds. A subsequent Runner kind normalization adjustment passed the focused orchestration/intent/bridge suite: 18 passed. Compilation and diff checks passed.
- Source version/schema changed; the main user database, installed app, and running service were not upgraded or validated in this slice.

### Remaining P0.2 work

- Bind a verified research-worker AgentIdentity and distinguish it from the local operator session. Current endpoint records an Agent claim only.
- Add passive capture at the existing execution entry points behind a compatibility flag. Current V5 executions do not automatically create ActionIntent rows.
- Document the canonical contract's optional Run behavior for historical tasks with null `run_id`; new V6 proposals currently require a Run rather than inventing one.

## P0.3 CapabilityGrant service

- Source is now 0.68.22 / build 100 / schema 30. Additive V6 schema v2 adds immutable grant, revocation and use ledgers without changing V5 task tool grants or Runner capability advertisement.
- A grant derives principal, capability, operation, task, Run, Scope/Policy hashes and resource from one ActionIntent. The local session can issue a bounded TTL and max-use grant. Exact in-scope HTTP/browser reads may become active; wildcard, out-of-scope and effectful capabilities remain pending. Revocation is a separate immutable row.
- `capability_matches` requires the caller principal, current task/Run/Scope/Policy, resource and operation constraints, unexpired active grant, no revocation and remaining uses. It returns eligibility only and performs no dispatch. Legacy `tool_grants` and Runner capability labels are not authorization inputs.
- Isolated 29→30 migration and recovery tests preserve historical rows. Targeted tests cover wrong principal, expiry, revocation, one-shot exhaustion, stale scope, out-of-scope read and read/send separation.
- Final repository regression for this increment: 851 passed, 4 skipped, 1 existing deprecation warning in 223.75 seconds; focused V6 contract suite: 13 passed. No production database migration or installed App validation occurred.

### Remaining P0.3 work

- Bind grants to authenticated Worker principals once Worker AgentIdentity is authoritative. Current active grants are limited to the local session principal.
- Consume grant uses atomically with execution gateway dispatch; `capability_uses_v6` is storage ready but no current V5 execution is routed through it.
- Add the approval workflow for effectful or wildcard grants. `pending_approval` is deliberately unusable until then.

## P0.4 deterministic PolicyDecision overlay

- Source is now 0.68.23 / build 101 / schema 31. Additive V6 schema v3 adds immutable `policy_decisions_v6`; the existing `v5_events` stream receives a metadata-only `policy.decided` audit event correlated back to the original task.
- Pure `evaluate_policy` has no Runner or tool dependency. Identity, current Scope, existing domain guard, explicit deny rules, Grant eligibility and budget have ordered fail-closed precedence. Agent/classifier risk hints can only raise the decision from bounded allow to approval/quarantine; they cannot cancel a deterministic deny.
- `record_policy_decision` requires a caller-owned transaction and trusted legacy guard/budget facts. There is no public decision-writing endpoint accepting caller claims. The read-only endpoint lists recorded decisions.
- Focused tests prove deny precedence, unknown guard/budget rejection, wrong principal, missing Grant, immutability, audited task correlation, no model dispatch, and isolated 30→31 backup/recovery.
- Final repository regression for this increment: 855 passed, 4 skipped, 1 existing deprecation warning in 228.70 seconds; focused V6 contract suite: 17 passed. No main database migration or installed App validation occurred.

### Remaining P0.4 work

- Wire real domain guard and budget facts from each execution boundary. A stored decision is not currently consumed by model, HTTP, browser or other first-party execution.
- Establish approvals for decisions that require them; the current result is review-only and cannot be treated as permission.

## Next implementation order

1. Finish P0.2: bind trusted worker identity and capture intents at existing execution entry points. Historical tasks with null `run_id` remain on the legacy path until an explicit migration rule exists.
2. Finish P0.3–P0.4 at a real execution boundary: bind trusted Worker identity, domain guard results, Grant consumption and approval semantics to the deterministic decision.
3. P0.5–P0.6: wire the existing model and read-only HTTP execution boundaries to these decisions and emit actual execution transitions. Preserve the current `runtime_calls` budget ledger and existing domain guards.
4. Continue through evidence, evals, packs, incident handling, and the measured V6.5 scale ladder in the source roadmap. Do not mark V6 complete before its release gates pass.
