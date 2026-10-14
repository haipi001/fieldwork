# Repository source layout acceptance — 2026-10-14

## Scope

Move Python business modules from the repository root into backend/ while preserving absolute module names. Root app.py and, on the development branch, desktop_server.py forward to the implementations for existing launch configurations. Resource/data paths remain repository-root based; sibling sandbox workers remain backend-relative. Native launch, bootstrap, maintenance scripts and pytest/subprocess import paths are updated. Two example files move to fixtures/examples/.

The main branch retains its own existing business baseline: 37 moved modules, one root Python launcher. The development branch moves 99 modules, with two root Python launchers. This does not merge development features into main or install an App.

## Clean checkout findings

Six fixed report adapter JSON configurations were excluded by the generic reports/ ignore rule. They are now tracked source assets in backend/report_adapters/ and used by the report renderer. Runtime databases, reports, credentials and build output remain excluded. Original FINAL package files are unchanged.

Deployment-alignment tests previously depended on ignored Forge output. A scoped fixture now builds the trusted local Vault fixture using its pinned Solidity 0.8.24 configuration; absent Forge skips those optional fixture cases. Generated build output is not committed.

## Verification

- Development full suite: 1056 passed, 3 skipped, 2 warnings in 292.46s.
- Main full suite: 409 passed, 1 warning in 48.39s.
- Final cross-directory import/resource and scoped deployment-fixture tests: 4 passed on each branch.
- Isolated workspace browser check: 18 navigation pages, 5 Agent tabs, 144 page/display combinations passed; zero model calls.
- macOS builds on both branches passed; bundle metadata and ad-hoc signatures verified. No installation or installed-App acceptance is claimed.
- Relative documentation links and staged whitespace checks passed.

The refactor was performed in isolated checkouts. Concurrent source/UI edits in the original working directory are outside this snapshot and remain there. Full roadmap, independent distribution and release readiness are unchanged by this layout work.
