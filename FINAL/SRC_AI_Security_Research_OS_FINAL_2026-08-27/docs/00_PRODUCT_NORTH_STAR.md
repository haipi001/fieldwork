# 00 — Product North Star

## 一句话产品定义

**把一个授权目标交给 AI，系统自动把它变成可以验证、可以复现、可以提交的漏洞研究成果。**

用户输入的可以是：

### Traditional SRC
- URL
- Domain
- IP / CIDR
- API / OpenAPI
- Git repository
- Source archive
- Web application
- Authorized test environment

### Web3
- Immunefi / bounty program URL
- Git repository + commit
- Contract address + chain
- Smart-contract source
- Local Foundry/Hardhat project

未来 Runtime 可以继续扩展：
- Cloud
- Mobile
- Solana
- Move
- Cairo
- AI / LLM Security

但当前最终产品只暴露：
`传统 SRC` / `Web3 / Immunefi`

## 用户不需要做的事

用户不需要：
- 选择 nmap / nuclei / slither
- 写扫描命令
- 理解 MCP
- 写 Agent prompt
- 决定模型
- 决定每一步扫描策略
- 手工拼证据
- 手工转换 HackerOne / Bugcrowd / Immunefi 格式

这些由系统完成。

## 用户只需要做的事

1. 输入目标。
2. 提供或确认授权 / Scope / Program Rules。
3. 点击开始。
4. 在高风险或规则不确定时确认。
5. 查看 VERIFIED Findings。
6. 选择提交平台并导出。

## 五条产品铁律

### 1. Evidence-first
任何结论都要能够指向 Evidence。

### 2. Verify-before-report
工具命中只是 Observation / Candidate，不直接进报告。

### 3. Scope-before-action
没有 Scope 就不允许主动动作。

### 4. Domain runtime, shared core
传统 SRC 和 Web3 不能复制两套 OS。

### 5. Simple outside, rigorous inside
前端简单；内部数据、执行、安全和证据严格。
