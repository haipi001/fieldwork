# 05 — Traditional SRC Runtime

## 目标

覆盖常见授权 SRC / Bug Bounty：

- Web
- API
- Authentication
- Authorization / IDOR
- Business Logic
- Source Code
- Secrets / dependencies
- Network service inventory
- Modern browser flows

## 分析面

### Asset Discovery
- hostnames
- services
- routes
- APIs
- JS assets
- technologies

### Identity Model
- anonymous
- user A
- user B
- privileged role
- organization/tenant boundary

### Request Model
- method
- route
- parameters
- body
- auth context
- response schema

### Business Flow
- signup
- login
- reset
- create/update/delete
- payment/order
- upload/export
- invite/share
- admin flows

## 分析策略

1. passive inventory
2. low-risk discovery
3. auth-aware crawling
4. code-assisted mapping when source exists
5. candidate probes
6. business-logic hypotheses
7. deterministic/controlled reproduction
8. counterevidence

## 工具输出从不直接成为 VerifiedFinding

Nuclei/ZAP/Semgrep/CodeQL 等输出先进入 Observation。
只有 Oracle 验证后才进 Verified。
