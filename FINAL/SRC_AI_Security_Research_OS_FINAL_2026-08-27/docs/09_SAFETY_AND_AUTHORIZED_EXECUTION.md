# 09 — Safety / Authorized Execution

## 强制边界

产品用于授权安全研究、SRC 和 bug bounty。

### 所有主动测试必须存在
- Engagement
- ScopeSnapshot
- ExecutionPolicy
- audit trail

### 禁止默认行为
- scope 外资产
- destructive actions
- denial-of-service
- unbounded traffic
- unrelated third-party systems
- credential theft
- persistence
- data exfiltration beyond minimum proof
- real production funds movement

## Web3
默认：
- mainnet/public testnet write denied
- local fork only for state-changing PoC
- real private key denied
- third-party active testing denied

## Secrets
Secret Store 与 Agent Context 分离。

Agent 获得：
- logical credential handle
而不是：
- plaintext secret

日志自动 redaction。

## Approval Gate

以下 capability 默认要求人工批准或 program explicit allow：
- high-volume scan
- account creation at scale
- state-changing sensitive action
- destructive proof
- external system interaction
- any tool outside compiled policy

## Transport-level enforcement

安全策略至少存在：
1. planner
2. executor
3. adapter
4. transport

不能仅靠 system prompt。
