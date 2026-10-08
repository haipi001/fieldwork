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

V6 is in development. P0.2–P0.5 are in progress; P0.6 through P2.5 and all release gates beyond baseline remain open. This file records source progress only; it is not evidence that the installed app or a running service has been upgraded.

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
- Extend atomic grant use beyond the built-in research model gateway. That path now consumes a one-use grant at the `calling` transition; other execution paths remain on their legacy gates.
- Add the approval workflow for effectful or wildcard grants. `pending_approval` is deliberately unusable until then.

## P0.4 deterministic PolicyDecision overlay

- Source is now 0.68.23 / build 101 / schema 31. Additive V6 schema v3 adds immutable `policy_decisions_v6`; the existing `v5_events` stream receives a metadata-only `policy.decided` audit event correlated back to the original task.
- Pure `evaluate_policy` has no Runner or tool dependency. Identity, current Scope, existing domain guard, explicit deny rules, Grant eligibility and budget have ordered fail-closed precedence. Agent/classifier risk hints can only raise the decision from bounded allow to approval/quarantine; they cannot cancel a deterministic deny.
- `record_policy_decision` requires a caller-owned transaction and trusted legacy guard/budget facts. There is no public decision-writing endpoint accepting caller claims. The read-only endpoint lists recorded decisions.
- Focused tests prove deny precedence, unknown guard/budget rejection, wrong principal, missing Grant, immutability, audited task correlation, no model dispatch, and isolated 30→31 backup/recovery.
- Final repository regression for this increment: 855 passed, 4 skipped, 1 existing deprecation warning in 228.70 seconds; focused V6 contract suite: 17 passed. No main database migration or installed App validation occurred.

### Remaining P0.4 work

- Extend real guard and budget inputs beyond the built-in research model gateway. That path now records a decision and consumes its grant before `calling`; other model, HTTP, browser and tool paths remain ungated by V6.
- Establish approvals for decisions that require them; the current result is review-only and cannot be treated as permission.

## P0.5 research model execution gateway

- Source is now 0.68.24 / build 102 / schema 32. Additive V6 schema v4 adds immutable model gateway binding and start ledgers. It preserves the existing `runtime_calls` budget ledger and V5 events.
- The built-in research Worker receives a server-assigned research principal and Agent ID. During call reservation, the gateway binds a metadata-only `model.call` ActionIntent, a one-use active Grant, the selected route and the existing task/Run/Runner. At the `calling` transition, the existing research guard is checked, a deterministic V6 PolicyDecision is recorded, and the Grant is consumed in the same transaction as the start record.
- Denied starts release the reserved call without outbound model execution. The bridge maps existing `call.*` events into model-call transitions while retaining their original event IDs and task correlation.
- The local HTTP research fixture executes eight tasks and verifies eight corresponding bindings, starts, Grant uses and allow decisions, with no findings or receipts. Targeted tests also cover revocation before start, changed Scope, one-use replay denial and isolated 31→32 migration/recovery. The focused suite passed 34 tests; repository-wide regression passed 859 tests, skipped 4, with one existing deprecation warning in 227.90 seconds.
- This is a source-level slice. The main database, installed app and running service have not been upgraded or validated for schema 32.

### Remaining P0.5 work

- Gate the structured local model-call path and any other first-party model execution route using the same authoritative identity and decision contract; validate the native path through a complete browser-backed execution when that environment is available.
- Cover cancellation, provider failure and unknown usage recovery at the gateway, and expose complete immutable model-call lineage in the UI.
- Keep historical tasks without a Run on the legacy path until an explicit migration rule exists.

### P0.5 native model extension

- Source is now 0.68.25 / build 103 / schema 32. The native discovery model path uses the same reservation, intent, one-use Grant, policy decision and atomic `calling` transition as the research Worker. Its principal and Agent IDs are distinct from the research Worker and are assigned by the built-in code path.
- Native binding requires the registered built-in Runner, current Run/Scope/Policy and the existing observation/artifact input guard. The Runner registration rejects an occupied ID and marks the built-in identity explicitly. A direct native model gateway test covers reservation, start, one-use consumption and replay denial without requiring a browser installation.
- Focused gateway/native/research/runtime/policy/Grant tests: 28 passed, 1 skipped. Repository-wide regression: 860 passed, 4 skipped, 1 existing deprecation warning in 225.65 seconds. Python compilation and diff checks passed.
- The structured local model path still has no-Run legacy tasks, and other first-party execution routes need gateway integration. The full browser execution test depends on the local Playwright/Chrome environment and remains separate from the direct model gateway test.

### P0.5 Run-bound structured model extension

- Source is now 0.68.26 / build 104 / schema 32. Structured critic and synthesizer task creation accepts an explicit current, confirmed Run. The Run participates in idempotency identity; a missing, synthetic, stale or mismatched Run is rejected. Tasks created without a Run retain their existing legacy behavior until an explicit migration rule exists.
- The built-in local structured Worker now binds Run-backed model calls to a distinct Worker principal and Agent ID through the same V6 intent, one-use Grant, policy and `calling` transaction. The legacy Scope/input guard and Run status are rechecked before and during model execution. An end-to-end local Worker fixture verifies settled call, gateway start and Grant use; a mismatched idempotency replay is rejected.
- Historical no-Run structured tasks, complete execution-event lineage and other model execution routes remain open. P0.6 HTTP/browser and later phases are still pending.
- Focused Worker/gateway/native/research/call/orchestration suite: 46 passed, 1 skipped; Evolution suite: 8 passed. Repository-wide regression: 861 passed, 4 skipped, 1 existing deprecation warning in 227.91 seconds.

## P0.6 native browser read gateway

- Source is now 0.68.27 / build 105 / schema 33. Additive V6 schema v5 creates immutable browser HTTP execution and receipt rows; it does not duplicate the existing Run request budget or network transport. The native browser's document GET callback uses the existing execution policy, DNS/private-address guard, Run budget and supervised pinned-socket transport, with V6 authorization before the transport.
- Each actual document GET receives a bounded existing `agent_tasks` record tied to its Run, a built-in browser Runner, a metadata-only ActionIntent, one-use Grant, deterministic PolicyDecision and immutable execution event. The response receipt stores only HTTP status, body hash, byte count and outcome; URL query values are hashed in the intent argument hash rather than stored as resource text. The V6 RuntimeEvent bridge resolves the task, Run, Runner, principal and Agent for these execution events.
- A local HTTP server test verifies one real pinned GET, sandbox attestation, budget consumption, execution lineage and receipt. Cancellation before transport records failure without a network request. Direct tests cover stale Run, embedded credentials, audited V6 denial without Grant use, replayed receipt and isolated 32→33 backup/recovery. Focused gateway/transport/native/bridge suite: 23 passed, 1 skipped before the added denial test; the gateway suite then passed 6 tests.
- Repository-wide regression after this increment: 866 passed, 4 skipped, 1 existing deprecation warning in 283.20 seconds.
- The full Playwright browser journey is still skipped in this environment. At this stage, formal HTTP replay, guided HTTP, other browser/API paths, artifact-to-receipt links and complete cancellation/unknown-outcome coverage remained P0.6 work. The main database, installed app and running service have not been upgraded to schema 33.

### P0.6 formal and guided HTTP replay extension

- Source is now 0.68.28 / build 106 / schema 33. Each GET in formal HTTP replay and guided object-read replay keeps the existing candidate/Run binding, business authorization checks, source checkpoint checks, DNS/Scope guard, request budget and supervised transport. A Run-bound V6 read task, independent HTTP replay principal/Agent, one-shot `network.request` Grant, PolicyDecision, execution event and metadata-only receipt are added before and after the request.
- The existing formal replay contract permits an authorized confirmed Scope and candidate on a completed or demo Run. The gateway follows those V5 Run states for this path; native browser reads continue to require a live non-synthetic Run. An ActionIntent argument hash covers the complete request URL and headers without persisting credential values; the task records replay round and role so baseline, attack and negative control calls remain distinguishable.
- The real guided HTTP workflow now proves ten recorded requests, ten Grant uses, ten allow decisions and ten completed receipts. Cancellation tests prove receipt counts stop with requests sent. Business-rule and stale-evidence regression scenarios retain their prior outcomes. Focused HTTP/business-boundary/gateway/native/bridge suite: 50 passed, 1 skipped.
- Repository-wide regression: 867 passed, 4 skipped, 1 existing deprecation warning in 285.00 seconds. The older cancellation fixture now supplies a valid response hash, and the guided live fixture persists the confirmed test Scope that its requests use; the cancellation check also scans new V6 rows for plaintext credentials.
- At this stage, non-GET state replay, standalone HTTP exchanges, guided page fetches, full browser execution, artifact-to-receipt linkage and unknown-outcome recovery remained open. No main database, installed app or running-service upgrade has been performed.

### P0.6 Run HTTP workbench and guided page extension

- Source is now 0.68.29 / build 107 / schema 33. Manual HTTP workbench GET, replayed exchange GET, Campaign workflow and recovery GET, and guided same-origin page GET now use a third first-party Run-bound read identity. Each request retains its existing Scope/DNS, rate, budget and cancellation guard, then receives a V6 `network.request` Intent, one-use Grant, PolicyDecision, execution event and immutable response receipt. The request header values are only represented by the Intent argument hash; the task captures a bounded source class (`manual`, `replay`, `campaign_workflow`, `campaign_recovery` or `guided_page_read`) without persisting dynamic Campaign IDs in that field.
- A real local server test confirms manual and replay GET produce separate receipts; guided page tests confirm their source lineage. A body-bearing GET is denied before budget use or network traffic because it is outside this read-only contract. Prior HTTP replay tests now count their own `http-replay-reader` tasks, separately from the three workbench setup exchanges.
- Campaign scheduling, workflow, iteration and recovery regression paths continue to pass with normalized Campaign source classes. Repository-wide regression: 869 passed, 4 skipped, 1 existing deprecation warning in 282.16 seconds.
- Other read-only methods, effectful HTTP methods, full Playwright browser journey, Artifact/Observation/Evidence linkage and unknown-outcome recovery remain open. The main database, installed app and running service have not been upgraded or validated for this source version.

### P0.7 first HTTP replay artifact lineage

- Source is now 0.68.30 / build 108 / schema 34. Completed, Run-bound HTTP replay GET actions are linked immutably to the existing replay Artifact. The link writer checks that each action has a completed receipt and that the action and Artifact belong to the same Run; it runs in the Artifact insertion transaction. The RuntimeEvent bridge exposes the linked Artifact ID on the corresponding completed execution event. Existing Artifact → Observation → Evidence rows remain the source of truth.
- The real ten-request replay fixture verifies all ten event-to-Artifact links and the established replay artifact contents. Cross-Run links and deletion of stored links are rejected. Built-in read Runner kinds now normalize to local placement in the V6 runner view.
- An isolated schema 33 → 34 backup/upgrade retains historical Intent rows and adds an empty lineage table. Repository-wide regression: 870 passed, 4 skipped, 1 existing deprecation warning in 287.93 seconds; the revised migration case passed separately after the full run.
- This is partial P0.7 coverage. Workbench and browser reads do not yet produce an Artifact chain; proof polarity and VerificationReceipt binding remain separate work. The main database, installed app and running service have not been upgraded to schema 34.

## Next implementation order

1. Finish P0.2–P0.5 across the remaining first-party paths: capture intents, bind trusted Worker identity, feed actual guards and budgets into policy, consume Grants, and record immutable execution lineage. Historical tasks with null `run_id` remain on the legacy path until an explicit migration rule exists.
2. P0.6: wire the existing read-only HTTP execution boundaries to these decisions. Preserve the current runtime budget ledger and domain guards.
4. Continue through evidence, evals, packs, incident handling, and the measured V6.5 scale ladder in the source roadmap. Do not mark V6 complete before its release gates pass.
