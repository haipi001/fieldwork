# 03 — Canonical Data Pipeline

## 为什么需要统一数据层

不同工具会输出：
- stdout
- JSON
- JSONL
- XML
- SARIF
- HTML
- screenshots
- HAR
- RPC traces

如果 Agent 直接读这些，会：
- Token 浪费
- 重复 findings
- 无法验证
- 报告不一致

所以统一：

```text
Raw Tool Output
     ↓
Tool Adapter
     ↓
Observation
     ↓
Entity Linking
     ↓
Evidence
     ↓
CandidateFinding
     ↓
Verification
     ↓
CanonicalFinding
```

## Observation

表示“看到了什么”，不是漏洞。

例：
- endpoint responded 403
- Nuclei matched template
- Slither found reentrancy pattern
- source contains dangerous sink
- invariant failed in fuzz trace

## Evidence

必须可追溯：
- source
- timestamp
- tool/version
- run
- target
- artifact hash
- location
- raw artifact pointer

## CandidateFinding

有 hypothesis，但尚未 VERIFIED。

## CanonicalFinding

必须包含：
- scope proof
- root cause
- affected asset
- reproduction
- impact
- evidence
- verification status
- confidence
- severity mapping
- platform metadata
