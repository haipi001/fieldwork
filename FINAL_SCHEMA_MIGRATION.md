# FINAL Schema Migration

## Principles

- Add canonical stores beside legacy tables first; migrate with idempotent scripts; switch reads; only then retire legacy fields.
- Every canonical record carries schema version, timestamps and stable IDs.
- Old Engagements default to `mode=traditional_src`.
- Old Findings without real verification evidence migrate to `human_review`, never automatically to `verified`.
- ScopeSnapshot is immutable. A policy/rules change creates a new snapshot.
- Large/raw artifacts live in an artifact store; SQLite stores pointers and hashes.

## Canonical target schema

### `target_specs`

`id, kind, canonical_value, mode_hint, fingerprint, metadata_json, schema_version, created_at`

### `engagements_v2`

`id, mode, target_spec_id, active_scope_snapshot_id, execution_policy_id, objective, budget_profile, status, created_at, updated_at`

### `scope_snapshots`

`id, engagement_id, version, source_type, source_hash, captured_at, allowed_assets_json, denied_assets_json, allowed_actions_json, denied_actions_json, rate_limits_json, disclosure_rules_json, custom_rules_json`

### `execution_policies`

`id, schema_version, active_testing, rate_limit_json, network_json, denied_capabilities_json, approval_required_json, created_at`

For Web3, add immutable `program_snapshots` containing program URL/rules hash, assets/impacts, known issues/audits, severity, primacy, PoC and feasibility rules.

## Canonical run/pipeline schema

### `analysis_runs`

`id, engagement_id, scope_snapshot_id, mode, state, current_phase, budget_id, checkpoint_id, parent_run_id, created_at, started_at, paused_at, stopped_at, failure_reason, schema_version`

Canonical states follow `PIPELINE_ENGINE.md`; compatibility mapping:

| Legacy | Canonical |
|---|---|
| draft engagement | CREATED engagement, no run |
| queued | CREATED |
| running | phase-derived state; legacy unknown phase → ANALYZING |
| completed | needs migration inspection; not automatically REPORT_READY |
| stopped | PAUSED only when resumable checkpoint exists; otherwise FAILED_RECOVERABLE |
| interrupted | FAILED_RECOVERABLE |
| budget_exhausted | BUDGET_EXHAUSTED |
| failed | FAILED_RECOVERABLE or FAILED_FINAL after reason review |

Add `tasks`, `checkpoints`, `run_events`, `tool_invocations`, and versioned `budgets/cost_events`. Current `events`, `executions`, `budgets`, `audit_log` are source tables for migration.

## Observation, graph and evidence

### `observations`

Implements `Observation.schema.json`: run, kind, target refs, adapter/tool/version/invocation source, summary, structured JSON, evidence IDs, created time.

### `artifacts`

`id, run_id, relative_path, sha256, size, mime_type, redaction_state, safe_to_export, created_at`

### `evidence`

Implements `Evidence.schema.json`: run, kind, artifact pointer/hash, target refs, redaction state. Counterevidence is an Evidence relation/type, not a separate competing truth store.

### `entities` / `relationships`

Domain-neutral entity graph plus domain-specific typed metadata. Migrate `surfaces` into URL/endpoint/role entities and relationships. Do not force Web3 contracts into URL columns.

### `coverage_entries`

`run_id, area, target_ref, state, reason, evidence_ids, updated_at`; use FINAL vocabulary. Add `candidate_graveyard` with disproof and resurrection conditions.

## Findings and verification

### `candidate_findings`

Derived from observations/correlation, containing hypothesis, input observation IDs, confidence and status.

### `verification_attempts`

`id, candidate_id, oracle, environment, attempt, result, assertion_json, evidence_ids, counterevidence_ids, created_at`

### `canonical_findings`

Stores/validates the FINAL CanonicalFinding schema and binds `scope.snapshot_id`. Impact and eligibility are typed subdocuments or separately versioned assessment tables.

Legacy migration:

1. Preserve old row ID as `legacy_source_id`.
2. Create target/scope references from the engagement manifest.
3. Migrate ledger rows into artifacts/evidence with hashes and `legacy-migration` source.
4. Create a CandidateFinding.
5. If legacy oracle is only `deterministic-demo-oracle`, set `human_review` unless the local demo fixture is explicitly being migrated as fixture data.
6. Populate missing CanonicalFinding fields as `null`/missing with completeness errors; never invent root cause, impact or reproduction.

## Reporting

### `report_adapter_versions`

`platform, version, config_hash, required/recommended/conditional fields, template pointer, created_at`

### `report_previews`

`id, canonical_finding_id, adapter_version_id, program_snapshot_id, completeness_json, consistency_json, rendered_artifact_ids, status, created_at`

### `submission_packages`

Implements FINAL package schema and points to generated directory/file artifacts. Legacy `submissions.report` Markdown blob migrates to draft preview only and never becomes Ready without canonical validation.

## Migration execution and rollback

1. Snapshot SQLite and run integrity check.
2. Create `schema_migrations` and canonical tables in one transaction.
3. Migrate Engagement/Scope/Policy, then Runs/Events/Budgets, then Observation/Artifacts/Evidence, then Candidates/Findings, then Reports.
4. Record source row IDs, per-table counts, hashes and rejected rows.
5. Dual-read comparison and golden fixtures.
6. Switch writes to canonical tables; keep legacy read-only for one release.
7. Rollback switches readers back; never delete source tables in the same release.

