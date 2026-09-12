# Agent Model

## 不做“20 个 Agent 开会”

最终模型：

### 1. Orchestrator
负责：
- objective
- scope
- plan
- budget
- task graph
- final state

### 2. Domain Analyst
TraditionalSrcAnalyst / Web3Analyst
负责：
- domain modeling
- hypothesis generation
- targeted reasoning

### 3. Verification Agent
负责：
- reproduction plan
- oracle
- counterevidence
- confidence

### 4. Report Agent
只读取 verified structured data。
不参与发现漏洞。

## Specialist 以 Tool / Skill 形式存在

例如：
- XSS
- access control
- SSRF
- source analysis
- Solidity accounting
- oracle
- ERC4626

优先是 Skill，而不是长期 Agent。

## Model Router

### Tier 0
No LLM:
- parsing
- hashing
- dedup
- transforms
- CVSS calculation
- schema validation

### Tier 1
cheap/fast model:
- classification
- summarization
- result triage

### Tier 2
reasoning model:
- business logic
- cross-evidence correlation
- protocol modeling
- exploit hypotheses

### Tier 3
highest reasoning:
只在：
- high-value ambiguous candidate
- multi-step logic
- economic/invariant reasoning
- report final consistency review
