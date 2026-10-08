# V6 unknown model consumption reconciliation

`POST /api/v1/v6/model-calls/{call_id}/reconcile`

Body: `{"artifact_id":"review-id","confirmed":true}`. Existing local session boundary applies.

The existing Artifact must belong to the call Task's recorded Run and have kind `runtime.usage_review`. Its file hash must match the stored hash. JSON material has exactly these fields:

```json
{
  "call_id": "call-id",
  "decision_id": "decision-id",
  "provider_id": "provider-id",
  "review_kind": "operator_review",
  "input_tokens": 10,
  "output_tokens": 5,
  "cost_micros": 0,
  "runtime_ms": 50
}
```

Counts and duration are nonnegative strict integers. `review_kind` is `operator_review` or `provider_record`; this is a recorded operator source statement, not authenticated provider attestation. Material cannot include unrelated or raw secret fields. The three IDs must match the stored call. Local routes cannot report cloud cost.

Only `unknown` calls with recorded Run ownership are eligible. The transaction atomically records immutable usage/timing, settles the call, stores an append-only reconciliation with Artifact hash and local principal, and emits `call.reconciled`. Actual consumption is recorded even if it exceeds the original reservation; it must not be clipped to hide overspend. Existing budget accounting consumes actual usage.

Repeated identical review returns the original reconciliation. Another review or changed material conflicts. Tasks are not resumed, retried or dispatched. Missing Run legacy calls remain unresolved through this endpoint. The existing worker/provider usage-report path remains available; this endpoint supplies explicit operator evidence review.

Evidence import UI, paged reconciliation read/integrity UI, authenticated provider receipt adapters and historical null-Run handling remain open. This implementation does not finish P2.2 or the full release gates.

## Read audit

`GET /api/v1/v6/model-usage-reconciliations?limit=50&offset=0` returns immutable reconciliation metadata, recorded usage/runtime and current integrity; maximum limit is 100. It rechecks same-Run Artifact kind/hash/file contents, Task/Call ownership, settled usage/timing and the usage report binding. No storage URI or raw material is returned. A changed or missing material produces `missing_or_changed`; booked usage is retained. Pagination reflects current records.

## Import review

`POST /api/v1/v6/model-calls/{call_id}/usage-review` accepts the exact ReviewMaterial JSON above and returns a generated Artifact ID/hash. It requires an unknown call, existing Run and matching call/decision/provider IDs. Storage filenames are generated internally; no path/content outside the strict material is accepted. Import leaves consumption unknown and does not settle or dispatch anything. File/DB failures clean up the newly written file. A separate confirmed reconcile request is required.
