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

### P0.7 Run HTTP exchange and guided page metadata lineage

- Source is now 0.68.31 / build 109 / schema 34. Manual and replayed workbench GET, Campaign workflow/recovery GET and guided page GET now write a metadata-only `http.exchange_metadata` Artifact and an `http.exchange` Observation when their existing exchange is persisted. The same transaction links the completed V6 gateway action to the Artifact. The Artifact contains exchange ID, response status, response SHA256 and byte count; it does not copy request URL, headers, body preview or credentials. The link writer verifies the response metadata against the immutable gateway receipt.
- Existing HTTP replay Artifact lineage remains distinct. Focused real-server and guided fixtures verify completed event → Artifact → Observation, file hashes and absence of the request origin in metadata. No Evidence polarity or finding is inferred from a page fetch or exchange alone.
- Repository-wide regression after the core changes: 870 passed, 4 skipped, 1 existing deprecation warning in 316.60 seconds. A subsequent file-cleanup guard and rollback test passed with the focused workbench/guided checks (3 passed). The rollback fixture proves that a missing gateway action leaves no exchange, Artifact, Observation or orphan metadata file.
- Native browser reads, other first-party Artifact producers, and VerificationReceipt binding remain P0.7/P0.8 work. Source changes have not been installed or checked against the running service.

### P0.7 native browser page artifact lineage

- Source is now 0.68.32 / build 110 / schema 34. The native browser's pinned document GET records its completed gateway action ID; the page-observation Artifact and Observation are committed with immutable links from every document GET contributing to that page. A missing or failed action blocks the page Artifact transaction and cleans up its file. The existing same-Run and completed-receipt checks apply.
- The real local Chrome/Playwright two-page discovery test now runs in this environment after installing the already-declared Playwright dependency. It verifies two page artifacts linked to two bounded GET actions, along with the existing model ledger and no candidate promotion. A direct pinned-transport test verifies RuntimeEvent → Artifact → Observation for a 403 response. This proves that fixture path only; it does not establish broad browser compatibility or release readiness.
- Repository-wide regression: 872 passed, 3 skipped, 1 existing deprecation warning in 292.54 seconds. The full native browser fixture is included in this run.
- VerificationReceipt binding, evidence polarity across other producer types, complete unknown-outcome recovery and later phases remain open. Main database, installed app and running service have not been upgraded to this source version.

## Next implementation order

### P0.8 success receipt authority and evidence binding

- Source 0.68.33 / build 111 / schema 34 adds Policy ID/content hash and candidate Evidence row hashes to server-issued success receipts. Issuance captures them under an immediate transaction; validation requires the current engagement Scope/Policy to match the Run and rejects changed policy contents, changed/missing Evidence or missing legacy bindings. Historical receipt rows are retained but cannot establish these new checks without re-verification.
- Core changes passed repository regression (874 passed, 3 skipped, one existing warning in 309.36 seconds). Subsequent transaction/current-Scope checks passed the focused receipt/business-boundary suite (26 passed). Independent verifier identity/context and immutable V6 receipt-binding storage remain open; this does not complete P0.8. Installed app and service were not upgraded.

### P0.8 immutable success receipt binding

- Source 0.68.34 / build 112 / schema 35 adds an immutable binding from each server-issued success receipt to its Candidate, Run and complete receipt payload digest (including oracle, attempts, Scope/Policy/Evidence/Artifact bindings and expiry). The binding is inserted in the receipt transaction. Validation rejects absent bindings or any changed receipt content; the existing receipt table remains the sole result store.
- Focused receipt/business-boundary tests: 27 passed. Isolated schema 34 → 35 backup/upgrade preserves a historical receipt and adds no fabricated bindings; update/delete of new binding rows are rejected. Independent verifier identity/context and negative/fix receipt binding remain open. No installed app, main database or service upgrade was performed.
- Repository-wide regression: 877 passed, 3 skipped, one existing deprecation warning in 293.16 seconds.

### P0.8 fixed receipt binding

- Source 0.68.35 / build 113 / schema 35 extends current Policy/Evidence snapshots and immutable receipt digests to server-issued repaired-negative HTTP receipts. Issuance and lifecycle closure use one immediate transaction. The existing V5 repeat-confirmation path validates the stored immutable binding before returning a prior fixed receipt.
- Real HTTP positive/control/fix and V5 receipt regression suite: 29 passed. Independent verifier identity/context across all producer types remains open; this is not complete P0.8. Main database, installation and service state were not upgraded.
- Repository-wide regression: 877 passed, 3 skipped, one existing deprecation warning in 274.66 seconds.

### P0.8 V5 HTTP independent verifier identity/context snapshot

- Source 0.68.36 / build 114 / schema 35 binds the existing independent HTTP verifier's receipt digest, actual verifier/Runner IDs, request/task IDs and context digest into success and fixed receipt payloads. Context covers the stored environment, input hashes and replay contract. Stored receipt validation rechecks that snapshot through the existing V5 receipt integrity validator. No verifier identity is invented for paths lacking a V5 independent receipt.
- Real HTTP positive/control/fix, business-boundary and V5 receipt suite: 44 passed in 123.78 seconds. Other oracle paths and complete P0.8 acceptance remain open; this source has not been installed or validated against the running service.

1. Finish P0.2–P0.5 across the remaining first-party paths: capture intents, bind trusted Worker identity, feed actual guards and budgets into policy, consume Grants, and record immutable execution lineage. Historical tasks with null `run_id` remain on the legacy path until an explicit migration rule exists.
2. P0.6: wire the existing read-only HTTP execution boundaries to these decisions. Preserve the current runtime budget ledger and domain guards.
4. Continue through evidence, evals, packs, incident handling, and the measured V6.5 scale ladder in the source roadmap. Do not mark V6 complete before its release gates pass.

### P0.9 deterministic regression comparison core

- Source 0.68.37 / build 115 / schema 35 adds a pure comparison core for measured Eval metrics: false Verified, policy bypass, candidate precision, replay success, cost and evidence gain. Missing, negative, non-finite and invalid-rate measurements fail closed. False Verified and bypass always fail; baseline precision loss and cost growth without evidence gain are explicit gates, including zero-cost baselines.
- Focused comparator tests: 8 passed. This does not execute fixtures or establish a release gate result. EvalScenario/EvalRun persistence, subject/version metadata, real result ingestion, API and seed regression scenarios remain required P0.9/P0.10 work. P0.8 other oracle identities also remain open.

### P0.9 immutable EvalScenario/EvalRun records and read views

- Source 0.68.38 / build 116 / schema 36 adds immutable versioned scenario manifests and measured EvalRun records in the existing database. Trusted writers bind a registered scenario, Run, existing Artifact/hash, explicit model/profile/prompt/policy/pack/scheduler metadata and measured metrics; metrics/subject must match the captured Artifact. Baseline comparison requires the same scenario version and intact baseline Artifact. No execution queue or result-write HTTP endpoint is added.
- Read-only V6 scenario/run endpoints expose persisted records. Focused storage/comparison/receipt-schema suite: 10 passed, including scenario version conflict, record immutability, capture mismatch, Artifact tamper and API write rejection. These are storage fixtures, not real security Eval outcomes. First-party measured scenario runners and seed regressions remain required.
- Repository regression: 886 passed, 3 skipped, one existing warning in 258.81 seconds. Isolated 35 → 36 backup/upgrade preserves historical receipt bindings and creates empty Eval tables.

### P0.10 deterministic Policy seed runner

- Source 0.68.39 / build 117 / schema 36 executes twelve local Policy scenarios through the real deterministic evaluator: bounded read, identity/Scope/Grant/budget rejection, unknown and explicit legacy guards, credentials, production signing, control mutation, external writes and classifier quarantine. It stores actual decisions and measured mismatch/bypass counts in a hashed Artifact and immutable EvalRun with evaluator-code hash. A policy-specific gate checks only these measured invariants; it makes no claim about finding precision or false Verified.
- Fixture writes use a savepoint and clean the Artifact on failure. Focused seed/comparison/storage tests: 10 passed. An injected allow-all evaluator is detected as a failed Eval, while the actual evaluator passes twelve decisions. SRC/Web3/Agent end-to-end seed sets, legitimate-negative coverage across domains and the full false_verified=0 release gate remain open. This source has not been installed or run against main data.

### Eval read integrity

- Source 0.68.40 / build 118 / schema 36 verifies current Artifact bytes/hash and scenario manifest digest when exposing EvalRun records. Missing or changed materials produce status `invalid`; the original measured result stays available for audit. Focused Eval storage/seed/runtime-view tests: 7 passed, including Artifact tamper changing the query status. No release or main-data validation is claimed.

### P1 first-party Pack manifests

- Source 0.68.41 / build 119 / schema 36 adds three versioned, hashed manifests referencing existing SRC, Web3 and Agent Audit adapters/verifiers and actual Artifact kinds. A read-only Pack endpoint exposes capabilities, roles, report/Eval mapping and explicit unsupported paths. All manifests state partial integration; empty domain Eval suites stay empty until real seeds exist. These declarations grant no execution authority.
- Adapter existence and read-only/mutation-isolation checks with runtime-view regression: 6 passed. Domain convergence, complete capability boundaries, independent verification and Pack quality gates remain unfinished.

### P1.6 V6 Run containment boundary

- Source 0.68.42 / build 120 / schema 37 adds immutable operator containment for a Run and atomically revokes its existing Grants. Current-Intent checks reject subsequent V6-authorized operations after containment, including newly minted Grants. The endpoint reports its explicit boundary; it does not assert that legacy execution paths or already-started external requests were stopped. Repeated containment is idempotent.
- Gateway/Capability/model regression suite: 21 passed, including real control API revocation and new browser-action denial. Incident state-machine linkage, recovery/unblock, legacy-path parity and in-flight cancellation remain required P1.6 work. No main-data containment or application upgrade was performed.
- Repository regression: 889 passed, 3 skipped, one existing warning in 272.72 seconds. A subsequent revocation reason-label correction passed the containment case separately. Isolated 36 → 37 backup/upgrade preserved historical Eval scenarios and created an empty containment table.

### P1.6 read transport containment checkpoints

- Source 0.68.43 / build 121 / schema 37 rechecks Run containment before and after workbench/guided GET transport and in the pinned browser/HTTP replay supervisor callback. A containment interruption records a cancelled gateway receipt and prevents result persistence. The existing urllib paths cannot be interrupted while blocked inside their synchronous call; supervised paths check during worker execution. These checks cannot retract already-transmitted requests.
- Focused HTTP gateway/guided/workflow suite: 45 passed. A real local transport fixture contains the Run immediately after authorization and proves no target request is sent and the receipt is cancelled. The post-response check was limited to containment after initial regression exposed changed legacy cancellation semantics; prior cancellation checkpoints now pass unchanged. Model/legacy-path cancellation and recovery remain open.

### P1.6 supervised model containment

- Source 0.68.44 / build 122 / schema 37 checks Run containment in the shared model-authority callback used by supervised local/cloud model transport. The existing child lifetime and usage accounting paths handle interruption; uncertain provider consumption remains unknown and no output is admitted.
- Real streaming model fixture proves containment disconnects within two seconds, retains one unknown call and writes no usage settlement or research node. Transport/gateway/Worker suite: 28 passed; full research/native-discovery focused suite: 8 passed. No repository-wide rerun or installed-service validation was performed. Legacy execution paths, Incident linkage and recovery remain open.

### P1.6 Agent Audit incident response state history

- Source 0.68.45 / build 123 / schema 38 binds append-only operator response events to existing Agent Audit incident candidates and same-Run, hash-checked Artifacts. Transitions enforce DETECTED → TRIAGED → CONTAINED → INVESTIGATING → REMEDIATING → RECOVERED → CLOSED with expected-state concurrency checks. CONTAINED requires an actual V6 Run containment record. The API identifies these as operator response records; recovery/closure does not resume the Run or certify external-agent remediation.
- Agent Audit/Incident suite: 58 passed; subsequent full-sequence and containment checks: 2 passed. Tests reject skipped/stale states, absent containment, changed evidence and history deletion, and prove closure retains containment. Isolated 37 → 38 backup/upgrade preserves historical containment. Other domain incidents, evidence-specific remediation verification, recovery release and UI remain open. No main-data state was changed or installed app upgraded.

### P1.6 reviewed containment release and repeated containment

- Source 0.68.46 / build 124 / schema 39 derives active containment from append-only containment/release events with legacy-row fallback. All V6 Grant, read, model and Incident checks use the same state view. Release requires the expected event generation, current confirmed Scope/Policy, no active/unknown model calls, recovered/closed linked incidents and intact same-Run operator review evidence. Previously revoked Grants stay revoked; subsequent re-containment is supported.
- Focused HTTP/Incident/streaming-model suite: 15 passed, proving release/re-containment, stale-generation rejection and unknown-consumption blocking. This is operator-reviewed release, not automated proof of external-agent remediation. Main data and installed service were not changed.
- Repository regression: 892 passed, 3 skipped, one existing warning in 264.10 seconds. Subsequent unresolved-read release blocking and current-event response changes passed 4 focused checks. Isolated 38 → 39 backup/upgrade retains effective historical containment through the fallback state view. Unsettled read executions also block release.

### P1.7 runtime aggregation backend

- Source 0.68.47 / build 125 / schema 39 provides a read-transaction snapshot of real Group/Task/Runner/model-call states, task roles, current leased Runner load, HTTP receipts/unsettled executions, effective containment, event counts and Grant issuance/revocation/use counts. It reads existing ledgers and adds no parallel runtime store.
- Focused summary/runtime-view/Pack suite: 7 passed. The lifecycle fixture verifies running→completed counts, Runner load, unsettled→completed receipts and containment/revocation. UI integration and broader P1.7 acceptance remain open. No installed-service or main-data validation was performed.

### P1.7 runtime snapshot UI

- Source 0.68.48 / build 126 / schema 39 adds real runtime summary counts to the existing model/routing page, including Group/Task/Runner/model-call/HTTP-receipt state breakdowns, unsettled reads, containment, events and Grant counts. Refresh uses the existing UI lifecycle and preserves model configuration; API failure removes the count table and shows the read error. States localize with Chinese/English switching.
- Real Chrome UI acceptance using isolated APIs/DB passed count equality, updated task counts, injected summary failure, language switching and dark/light 1440/680-width layout checks; no page errors or model calls. Initial fixture failures were corrected by adding the V6 API route and current schema initialization. JS syntax validation passed. Full Groups/Agents aggregation and P1.8 controls remain open; the installed app was not upgraded.

### P1.8 Eval record UI

- Source 0.68.49 / build 127 / schema 39 adds a read-only Eval table in the runtime page, showing scenario/version, model/profile, scoped status, failure reasons and Artifact integrity. Loading, empty and failed reads have distinct states. A record does not provide a run or approval control; the status applies to its measured scenario.
- Real Chrome acceptance executes a Policy seed into the isolated database, displays passed, then tampers its Artifact and verifies refresh displays invalid. Existing language/theme/1440/680 layout and runtime configuration checks pass with no page errors or model calls. JS syntax passed. Policies/Incidents/Evidence UI and full Eval suite acceptance remain open; no main-data or installed-app changes were performed.

### P1.8 Policy decision audit UI

- Source 0.68.50 / build 128 / schema 39 adds a paged read-only PolicyDecision endpoint joined to its immutable Intent, and a recent-decision table in the runtime page. It exposes Task/Run/action/resource/decision/reasons without accepting authorization claims or performing the operation. Loading/empty/error states are explicit and labels localize.
- Policy API regression: 4 passed, including real persisted record correlation and pagination. Chrome acceptance displays a server-recorded denied gateway fixture and passes existing refresh/Eval-tamper/language/theme/narrow-layout checks with no page errors or model calls. JS syntax passed; full repository regression was not rerun. Incident/Evidence UI and complete release gates remain open.

### Unified evidence lineage read contract

- Source 0.68.51 / build 129 / schema 39 exposes paged same-Run Artifact lineage from existing tables: gateway actions/completed RuntimeEvent IDs, Observations and directly or indirectly associated Evidence. Stored evidence polarity is preserved with support/counter/context normalization where known. Current file hashes produce explicit integrity status; local storage paths and raw content are not returned.
- Real workbench/replay and runtime-summary targeted tests: 3 passed; after indirect Evidence support and event ownership hardening, two real HTTP lineage tests passed again. Ten formal replay events reach the replay Artifact/Observation/support evidence, and metadata tamper is surfaced. This read contract does not create missing producer links, establish finding validity or finish P0.7 across all domains. UI and broader release acceptance remain open.

### P1.8 Evidence lineage UI

- Source 0.68.52 / build 130 / schema 39 adds a Run-scoped read form and material table in the runtime page. It displays Artifact kind/ID/hash/integrity plus recorded event/Observation/Evidence links, with explicit loading, empty and error states. Reset invalidates pending reads and clears prior Run results. The current UI shows the first 50 records and identifies additional pages.
- Real Chrome acceptance verifies intact→changed material, missing Run clearing old rows, and Chinese/English dark/light 1440/680-width layout with the evidence table rendered. No page errors or model calls; JS syntax passed. Interactive pagination, Incident controls, complete producer lineage and release gates remain open; no main-data/installed-service changes were performed.

### Incident response evidence integrity on read

- Source 0.68.53 / build 131 / schema 39 rechecks every immutable response event against its same-Run Artifact metadata and actual file bytes in a database read snapshot. History exposes per-event integrity and aggregate not_recorded/intact/missing_or_changed without changing recorded operator response state. No raw material or local path is exposed.
- Incident regression passes with real Agent Audit evidence: file replacement, file removal and stored Artifact hash mutation are surfaced; restoring intact material restores the read integrity status. Append-only response records and transition gates remain enforced. Full repository regression and Incident UI remain open; no installed-app or main-database changes.

### P1.8 Incident response history UI

- Source 0.68.54 / build 132 / schema 39 adds a candidate-scoped response-history read form in Runtime, displaying operator state, effective Run containment, aggregate/per-event evidence integrity, Artifact ID/hash and time. Loading/empty/error states and reset invalidate stale reads. Scope text identifies the operator authority and bounded containment, without claiming verified remediation.
- Real Chrome acceptance creates/analyzes an isolated Agent Audit fixture through actual APIs, records TRIAGED, reads intact evidence, tampers material and observes missing_or_changed, then verifies a missing incident clears the previous state and rows. The screenshot/layout loop now reopens Incident and Evidence tables after reload to test rendered data in both languages/themes at widths 1440/680. JS syntax passes. Incident action controls, full domain response convergence and complete release gates remain open.

### Evidence lineage UI pagination

- Source 0.68.55 / build 133 / schema 39 enables previous/next pages of 50 materials, shows the current range and queried Run, and disables navigation while loading or at page boundaries. Navigation binds to the returned Run rather than unsubmitted form edits; a new submission starts at offset zero. Existing reset/read generation prevents stale async results.
- Real Chrome isolated-API acceptance includes 52 same-Run materials, verifies 50/2 page counts and first/last button states, changes the Run input before paging to prove navigation stays on the queried Run, then returns to the first page. Existing tamper/missing Run/Incident/configuration/language/theme/narrow-screen checks remain covered. No installed app or main database changes; paged views reflect current data rather than a frozen multi-request snapshot. Full release gates remain open.

### Incident transition history integrity gate

- Source 0.68.56/build134/schema39 requires intact previously recorded response evidence before advancing operator response state. A new intact review cannot bypass a damaged history. Rejected transitions append no event and retain the current state.
- Incident regression: 1 passed, covering distinct intact replacement review after historical material tamper, exact history count and state retention, followed by restoration and legitimate progression. Full release/containment recovery audit remains open.

### Containment release response-evidence gate

- Source 0.68.57/build135/schema39 checks all response events for incidents attached to the Run inside the release transaction, including recorded Run ownership and current Artifact/file integrity. RECOVERED/CLOSED status with damaged historical evidence cannot authorize release using a distinct intact review.
- Targeted regression: 2 passed (11 deselected), using actual analyzed Agent Audit response history and current confirmed test authority; missing historical file keeps containment active, restoration permits release, and existing revoked-grant/read-denial behavior passes. No full repository regression or installed-state validation; complete release gates remain open.

### Post-Incident/evidence compatibility regression

- Authoritative tested source: cfbeed9 / 0.68.57/build135/schema39. `PYTHONPATH=. python3 -m pytest -q tests/test_v6_*.py`: 48 passed, 1 existing warning (6.35s). Full `PYTHONPATH=. python3 -m pytest -q`: 893 passed, 3 skipped, 1 existing Starlette/httpx deprecation warning (266.81s). No code changed during these runs.
- This verifies current automated compatibility/regression coverage after Incident history/release and paged evidence UI changes. Skipped checks and unimplemented plan items remain outside this evidence. Installed app, live service, domain Eval metric completeness, fault-injected 10/32/100/256/500/1000 ladder and three clean 1000-task runs are not established by this result.
- Roadmap next priority: P2.2 explicit human/provider-backed reconciliation for unknown model consumption; current v5_runtime_calls.py retains unknown consumption and v5_workers.py pauses affected tasks, but no explicit reconciliation path is present. Full goal remains active.

### Unknown-consumption reconciliation transaction foundation

- Source 0.68.58/build136/schema39 factors existing usage settlement into an active-transaction-only helper; the existing API still owns its BEGIN IMMEDIATE. The reconciliation path can now atomically persist an audit alongside immutable usage and call settlement. No unknown call is automatically released or replayed.
- Runtime ledger regression: 18 passed, 1 existing warning (5.74s), including rollback after a simulated review-persistence failure: call remains unknown, no usage/report persists and reserved tokens remain held; subsequent valid settlement retains idempotency. The actual evidence-bound reconciliation endpoint/audit storage and UI remain unimplemented, so P2.2 is not complete.

### Evidence-bound unknown consumption reconciliation

- Source 0.68.59/build137/schema40 (V6 schema12) adds append-only model_usage_reconciliations_v6 and explicit local operator POST /v6/model-calls/{id}/reconcile. Same-Run runtime.usage_review material must hash-match and exactly bind call/decision/provider plus strict measured usage/duration. Only unknown calls are eligible; settlement and audit are one transaction, tasks remain unchanged. Contract and limitations are in V6_MODEL_USAGE_RECONCILIATION.md.
- Runtime/reconciliation/HTTP gateway regression: 22 passed; after audit-write failure injection, reconciliation test passed again. It verifies rollback leaves unknown/no usage, tamper and wrong-provider rejection, confirmation, immutable audit, repeat idempotency, actual usage accounting and paused-task retention. Full migration/repository acceptance and review import/read UI remain open; no main DB/app upgrade performed.

### Reconciliation read audit

- Source 0.68.60/build138/schema40 adds paged read audit with current material, ownership, usage/timing and usage-report consistency checks, retaining actual booked usage when integrity fails. No local URI/raw material is exposed.
- Regression covers intact/changed/restored/missing review material, pagination bounds/empty offset, recorded token/runtime and retaining settled state after tamper. Reconciliation and runtime-summary targeted tests pass; full repository acceptance/UI remain open.

### Reconciliation explicit confirmation and Run existence

- Source 0.68.61/build139/schema40 rejects integer/string/null substitutes for explicit JSON boolean confirmation and requires the Task Run to actually exist before reconciliation. Cross-Run material remains rejected.
- Reconciliation regression adds real API rejection checks for cross-Run evidence, nonexistent Run, non-boolean confirmation, boolean/string/negative/fractional token counts, alongside the existing atomic/idempotency/integrity checks. Full UI and release acceptance remain open.

### Runtime reconciliation audit UI

- Source 0.68.62/build140/schema40 reads the latest 50 reconciliation records in Runtime and renders call/Run, actual tokens/cost/runtime, operator-declared source, Artifact and current integrity. Missing usage is explicitly identified; empty/loading/error states remain distinct. Language changes rerender labels.
- Browser acceptance covers actual empty API response, injected 503 read failure clearing rows and recovery, alongside existing runtime/evidence/Incident/layout checks. Populated reconciliation UI data has not yet been browser-verified; evidence import/action controls and pagination remain open. No model or production-data actions.

### Populated reconciliation browser acceptance

- Browser acceptance on source 0.68.62/build140/schema40 now creates a leased model-call reservation using the existing configured local provider, marks the fixture dispatch unknown without sending a model request, attaches a same-Run review and invokes the real reconciliation API. No additional provider/profile configuration is introduced.
- Real Chrome verifies populated audit tokens 10/5, duration50ms, intact→changed review integrity, 503 clearing a previously populated table, and successful reread. The rendered audit table remains visible during both language/theme and 1440/680-width overflow checks. No page errors, zero model requests. This closes the earlier populated-table browser verification gap, not the evidence-import/action UI or full release gates.

### Structured usage review import

- Source 0.68.63/build141/schema40 adds explicit review import for unknown calls with existing Run and exact call/decision/provider binding. Strict material is stored as a hashed same-Run Artifact using generated filenames; the API returns no local URI. Import leaves usage unknown and does not dispatch. Failed transaction cleans up only its newly created file.
- Targeted tests use the actual imported Artifact for reconciliation and subsequent read-integrity checks, reject wrong provider/extra raw fields, verify hash/Run ownership and unchanged call state before reconciliation. Full UI and release gates remain open.
