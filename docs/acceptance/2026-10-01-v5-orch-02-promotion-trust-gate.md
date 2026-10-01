# V5-ORCH-02 Promotion Trust Gate

日期：2026-10-01
版本：Fieldwork 0.56.0（Build 65 / Schema 23）

本轮收紧 V5 Verification Receipt 的正式晋升边界：外部 Runner 仍可签发不可变、可审计的 `runner_attested` 回执，但其环境与结论是自报，不能据此创建新的 `canonical_result` 或把漏洞情报匹配标为 `verified`。晋升必须同时满足服务端观察到的独立子进程、当前 Claim/Evidence 与 Scope/Policy、肯定或反驳的完整结果及声明的 Oracle。回环 HTTP 状态码回执要求 Claim 显式标为 `http_status_relationship`，且绑定同一 Replay Contract hash；不能被挪用于任意 Claim。漏洞情报只接受其领域专用的 `package_applicability_v1` Oracle，回环状态码复测不能证明包适用性。

Receipt API 新增 `integrity.promotion_eligible`，把哈希完整、当前输入匹配、进程观察和可晋升资格区分开。V5 回执列表把外部自报显示为“仅记录 · 未证明独立执行”；Research Graph 对已存在但现不满足晋升条件的 Canonical 节点只读标注 `canonical_trust.current=false`，前端显示 `stale_verification`，不删除历史记录。

限制：当前可信本地执行仅覆盖回环 HTTP 状态码关系，尚无包适用性独立 Oracle，也没有通用/远端受信任 Verifier 或完整 OS 沙箱。历史外部自报回执仍保留审计价值，但不再具有新的正式晋升资格；已存在的 Canonical 节点在读取时会被重新评估并标记失效。`V5-ORCH-02` 整项仍未完成。

验证：`python -m pytest -q` 为 620 passed、3 skipped（1 条第三方弃用警告）；新增历史 Canonical 只读失效用例在全量启动后补入，单独重跑 1 passed。`node --check static/v5.js`、`node --check static/v5-research-graph.js`、`python -m compileall -q` 和 `git diff --check` 均通过。`./scripts/build_macos_app.sh` 生成未安装的 `build/macos/Fieldwork.app`，`codesign --verify --deep --strict` 通过，包内版本为 0.56.0 / Build 65。未覆盖 `/Applications/Fieldwork.app`，未触发真实外部目标验证。
