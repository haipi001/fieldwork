# Toolchain Strategy

## 核心原则

### 不造轮子
成熟 CLI / library 通过 Adapter 复用。

### 不绑定单工具
Planner 调 capability，Tool Registry 选 implementation。

### 不信 scanner
所有 scanner result 先归一为 Observation。

### Heavy tool optional
如果机器未安装某工具：
- capability health 显示 degraded
- 不导致整个 SRC 模式不可用

## Traditional SRC 推荐 Capability Map

### Recon / Inventory
- ProjectDiscovery subfinder
- httpx
- katana
- nuclei
- nmap / naabu（按 Scope）

### Browser / HTTP
- Playwright
- HTTP proxy adapter
- OWASP ZAP automation/API（可选）
- Strix browser/proxy ideas

### Code / Dependency / Secret
- Semgrep
- CodeQL
- Gitleaks
- Trivy
- OSV / OSV-Scanner

### Verification
- custom deterministic oracles
- replayable HTTP proof capsule
- pentest-ai oracle/proof-capsule ideas

## Web3

- Foundry / Forge / Anvil
- crytic-compile
- Slither
- Aderyn
- Echidna
- Medusa
- Chimera concepts
- crytic/properties
- Halmos
- Kontrol
- Certora adapter if appropriate
- revm later for embedded EVM

## Orchestration references

### Strix
参考：
- autonomous pentest agent graph
- Docker sandbox
- browser / proxy / terminal tools
- validated PoC/report flow
- skills/runtime separation

### pentest-ai
重点参考：
- machine oracle
- re-run verification
- candidate vs verified
- portable proof capsule
- large tool wrapper registry

### PentAGI
参考：
- autonomous multi-step pentest orchestration
- self-hosted control plane
- observability / agent workflow

### Argus
参考：
- search / decision strategy
- capability modeling
- budgeted exploration
- candidate graveyard/resurrection

### SWE-agent
重点参考：
- Agent-Computer Interface
- context-efficient tool interfaces
- concise deterministic command feedback

### Anthropic Cybersecurity Skills
参考：
- security knowledge/skill packs
- on-demand specialization
