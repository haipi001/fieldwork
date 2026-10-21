# Native App reload acceptance — 2026-10-21

## Observed
- User confirmed Mac unlocked. Quit and relaunched installed `/Applications/Fieldwork.app` through native CUA. Old health endpoint stopped before relaunch; new endpoint returned ready.
- Source-linked App remains 0.68.76/build154/schema41; no independent binary build or installation this turn.
- All 18 navigation pages loaded natively without observed loading-error text. Campaign uses project title; Sentinel and Runtime use their existing display titles.
- All six Agent tabs (inventory plus five detail tabs) loaded. Attack-test adapter panel shows saved source versions/recovery controls.
- Selected the existing group owner project in Agent inventory. Visible controls include member count, concurrency, batch draft review, automatic resume, and execution/recovery status.
- Opened automatic-resume dialog: policy disabled, approved tasks 0, scheduler running, progress counters 0. Read-only preview rejected this legacy group because it has no explicit runtime Profile. No enable or research action performed.
- Opened execution/recovery dialog: not started, one queued task, zero unexpired leases and interrupted-review tasks, empty model-call ledger. Closed dialog and left App on group inventory.
- Main Provider/Profile row hashes and activity counters identical before/after/final; runtime calls 0; no enabled resume policy or active work.

## Limits / remaining work
- Native loading/read-only acceptance only. No new real model call, target execution, draft generation or full research flow accepted here.
- Resume preview missing-Profile error remains English in Chinese mode; localization gap recorded.
- Native modal contents were visually readable, but macOS AX tree omitted modal children; used screenshot-grounded coordinates for preview/close.
- Existing source-backed installation is not an independent distribution. Broader delivery gaps remain open.

## Evidence
- `build/native_resume_snapshot_20261021.py`
- `build/native-resume-before-20261021.json`
- `build/native-resume-after-20261021.json`
- `build/native-resume-final-20261021.json`
- Native CUA observations in current conversation; screenshots are not persisted as repo artifacts.
