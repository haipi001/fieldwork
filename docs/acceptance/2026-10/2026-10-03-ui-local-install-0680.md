# Fieldwork 0.68.0 UI completion

Release: 0.68.0, build 78, database schema unchanged at 25.

## Delivered

- Source-owned Chinese/English copy for all 18 views, including Evolution, verification histories/receipts, finding details/proof, Runtime, dialogs, failures and table headers.
- Language redraws retain form drafts, selected options, checkboxes, open details, focus and scroll. Project names, user input and original evidence remain unmodified.
- Language changes use cached data and do not issue API writes or refresh calls. Failed reads remain unavailable, rather than becoming empty or successful states.
- Fixed sidebar AI Agent Security entry, explicit lifecycle actions, separate current monitoring/history, consistent light/dark styles and responsive layout.
- Versioned frontend assets invalidate the native WebView cache. Graph localization retains paginated nodes, edges and continuation controls.

## Acceptance

- Full Python regression: 659 passed, 3 skipped (one dependency deprecation warning).
- Frontend source regression: 24 passed.
- Isolated layout/workflow smoke: 18 views × 6 widths × 2 themes = 216 checks; creation/scope/plan/start, task controls, finding details, keyboard navigation and modal focus.
- Locale matrix: 18 views, Chinese/English roundtrip, dark/light, 390/1280 px; zero script errors, overflow, untranslated Chinese UI text/visible attributes in English, or unexpected writes.
- Populated detail coverage: verification attempts/jobs/receipts, finding lifecycle/proof, Runtime providers/profiles, failed reads, modal drafts, original evidence preservation, zero language-triggered API calls.
- Dedicated monitor, Evolution and core locale fixtures passed. Graph fixture validates 1001 nodes/1201 relations, delayed endpoints, retries, main/home pagination and cached language redraw.
- Native installation: signed local bundle at `/Applications/Fieldwork.app`; health reports 0.68.0. Previous bundle preserved under `build/macos/install-backup-0680-2PZC1f/`.

## Boundaries

No real scan or monitor was enabled for acceptance. Native Runtime readiness/capability reads initially timed out and were honestly displayed as unavailable; after restart the local tool readiness response succeeded. Registry readiness does not prove a successful scan. The monitor-only service prototype is retained in source but not installed as a launch agent. Reliable autonomous discovery with independent confirmation remains a separate unfinished backend goal. The native launcher still depends on this repository and local Python; an old app bundle alone is not a complete source rollback.
