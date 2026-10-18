# Evidence next-step handoff

The project button “查看本次证据” now opens the selected Run ahead of the cross-run index. The index defaults to the selected Run's project, while the All Projects option remains available.

A state-derived next action uses the run-specific findings and actual execution checklist: active/failed runs → run queue; pending leads → independent verification; incomplete items → existing execution preflight; verified findings → report preview; a finished round without leads → project summary. Missing findings never claim completion. The panel lists incomplete items and their recorded reasons. Authorization, execution confirmation and verification remain explicit.

Validation:

- `node --check static/v5.js`, `static/v5-home.js`, `static/v5-workspace.js` passed.
- `.venv/bin/python scripts/check_v5_flow_ui.py`: 8 project states, 5 evidence outcomes, 4 team handoffs; isolated APIs, project preservation, ordering, 400px overflow, All Projects access and blocked execution confirmation.
- `.venv/bin/python -m pytest -q tests/test_v5_workspace_ui.py`: 53 passed, 1 dependency deprecation warning.
- `/Applications/Fieldwork.app` source-linked 0.68.75/build153 was relaunched. Actual native “查看本次证据” showed selected Run/project, four incomplete items and the next-step button; that button opened execution preflight. No execution-start button was used by this validation. Existing user run outcomes and data were retained.

Reproduce isolated UI checks:

```sh
cd /Users/lizekai/Documents/ChatGPT/SRC漏洞
.venv/bin/python scripts/check_v5_flow_ui.py
.venv/bin/python -m pytest -q tests/test_v5_workspace_ui.py
```

This change fixes the navigation and guidance. It does not resolve the existing tool failures or authorize third-party passive queries. No release version change, Git commit or push was made for this frontend slice.
