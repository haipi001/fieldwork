# FINAL Implementation Order

This order implements the 64-task dependency graph without entering broad rewrites prematurely.

## Gate 0 — F0 convergence (current stage)

Deliverables: the six `FINAL_*.md` files. Exit criteria:

- package integrity/contracts pass;
- current features, schemas, screens, reports and tools inventoried;
- every existing feature classified;
- migration order/risk accepted by user.

Stop after presenting F0. Do not start F1 automatically.

## Gate 1 — F1 canonical core

Recommended slices:

1. `F006–F007`: TargetSpec/TargetResolver + immutable ScopeSnapshot/ExecutionPolicy.
2. `F008, F013`: canonical Run state machine/events, Pause/Resume contract and compatibility read adapter.
3. `F009–F011`: Observation → Artifact/Evidence → Candidate → CanonicalFinding, with schema validation and conservative legacy migration.
4. `F012`: domain-neutral Entity/Relationship graph.
5. `F014–F016`: DomainRuntime/CapabilityRegistry and four-layer policy/transport enforcement.

F1 gate tests:

- target kind/fingerprint golden cases;
- no active capability without ScopeSnapshot;
- raw tool result cannot create CanonicalFinding;
- legacy verified demo data migrates to human review unless fixture-marked;
- every finding references immutable scope and evidence;
- policy denial at planner, executor, adapter and transport.

## Gate 2 — F2 five-workspace UX

Implement `F017–F023` after canonical APIs stabilize:

- five routes/workspaces and mode switch;
- one-field intake with Scope card;
- canonical Run timeline with default/expanded/expert layers;
- verified-first Findings;
- Reports platform picker/completeness shell;
- Settings/Tools health and expert drawers.

Keep current eight views through compatibility mapping until no-capability-loss E2E passes.

## Gate 3 — F3 Traditional SRC convergence

Implement `F024–F033`:

- TraditionalSrcRuntime and capability adapters;
- inventory/browser/HTTP/code result normalization into Observations;
- multi-role/tenant identity and business flow model;
- correlation/hypothesis;
- replayable HTTP/state Oracle, counterevidence and portable Proof Capsule;
- FINAL Coverage Ledger and Graveyard.

Use a local authorized fixture first. External tools remain optional/degraded until separately reviewed and installed.

## Gate 4 — F4 universal reporting

Implement `F034–F042`:

- CanonicalFinding-only ReportCompiler;
- required/recommended/conditional completeness and consistency;
- Generic SRC, CN SRC, HackerOne, Bugcrowd, Intigriti adapters;
- Markdown/JSON/HTML and applicable SARIF;
- redacted SubmissionPackage directory with manifests and artifact pointers;
- Reports workspace wiring.

No third-party auto-submission.

## Gate 5 — F5 Web3/Immunefi

Implement `F043–F055` only on the shared core:

- Web3Runtime and versioned ProgramSnapshot;
- local project/compiler normalization and protocol graph;
- static/invariant/fuzz adapters;
- production/public-testnet write denial;
- Anvil local fork manager without real private keys;
- state/balance/trace Oracle, impact/eligibility;
- Immunefi adapter with program overlay.

Acceptance target is a local-only deterministic Web3 fixture, not mainnet experimentation.

## Gate 6 — F6 hardening and completion

Implement `F056–F064`:

- deterministic-first model/tool cost router;
- durable checkpoints/resume;
- secret/PII redaction across context, logs and exports;
- traditional and local-Web3 E2E suites;
- scope/rate/network denial and adapter golden tests;
- non-technical user five-workspace acceptance;
- install/run/recovery/operator documentation.

## Dependency introduction policy

Before adding any external security tool:

1. record upstream URL, exact version/commit and installation method;
2. verify license and distribution/use restrictions;
3. check maintenance/release/security state;
4. define capability, input/output normalization, timeout/resource/network policy;
5. add health/degraded behavior and fixture/golden tests;
6. avoid vendoring whole repositories.

