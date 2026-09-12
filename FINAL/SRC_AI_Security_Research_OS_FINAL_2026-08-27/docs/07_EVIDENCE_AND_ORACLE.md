# 07 — Evidence / Oracle / Verification

## 目标

降低误报。

## Verification Levels

### V0 — Observation
工具命中。

### V1 — Candidate
有合理漏洞假设。

### V2 — Reproduced
复现一次。

### V3 — Oracle Verified
机器 Oracle 按明确成功条件重复验证。

### V4 — Counterevidence Tested
同时验证常见反解释/mitigation。

最终 UI 的 VERIFIED 默认要求 V3；高风险报告推荐 V4。

## Oracle 类型

### HTTP Oracle
例：
- unauthorized user receives protected object
- response difference meets deterministic predicate
- controlled marker appears in sink

### State Oracle
- resource created/modified across unauthorized boundary
- persistence confirmed

### Code Oracle
- reachable source → sink path + runtime evidence

### Web3 Oracle
- invariant fails
- local fork reaches forbidden state
- attacker balance / protocol balance changes
- unauthorized state transition succeeds

## Proof Capsule

每个 verified finding 建立可重放 Capsule：

```text
proof/
  manifest.json
  environment.json
  steps.md
  requests/
  responses/
  script/
  evidence/
  expected.json
```

Proof Capsule 不能默认包含：
- real secrets
- production private keys
- unrelated personal data
