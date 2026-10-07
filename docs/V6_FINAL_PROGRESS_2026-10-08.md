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

V6 is in development. P0.2 through P2.5 and all release gates beyond baseline remain open. This file records source progress only; it is not evidence that the installed app or a running service has been upgraded.

## Next implementation order

1. P0.2: define immutable, passive ActionIntent capture with explicit principal, resource, verb, scope and policy binding. Resolve the current schema mismatch where `ActionIntent.schema.json` requires `run_id` but existing `agent_tasks.run_id` may be null; do not fabricate a run.
2. P0.3–P0.4: add time-bound CapabilityGrant and deterministic policy decisions. A Runner's advertised capabilities must never authorize an action.
3. P0.5–P0.6: wire the existing model and read-only HTTP execution boundaries to these decisions and emit actual execution transitions. Preserve the current `runtime_calls` budget ledger and existing domain guards.
4. Continue through evidence, evals, packs, incident handling, and the measured V6.5 scale ladder in the source roadmap. Do not mark V6 complete before its release gates pass.
