# Pipeline Engine

## 状态机

```text
CREATED
→ TARGET_RESOLVED
→ SCOPED
→ MAPPED
→ ANALYZING
→ CORRELATING
→ VERIFYING
→ IMPACT_ASSESSED
→ ELIGIBILITY_CHECKED
→ REPORT_READY
→ EXPORTED
```

旁路状态：
- PAUSED
- BLOCKED_BY_SCOPE
- BLOCKED_BY_POLICY
- BUDGET_EXHAUSTED
- NEEDS_HUMAN_REVIEW
- FAILED_RECOVERABLE
- FAILED_FINAL

## Planner 不应该生成“无限开放任务”

每个 Task 必须有：
- objective
- capability
- target refs
- preconditions
- allowed actions
- stop condition
- budget
- expected evidence
- verification strategy

## Coverage Ledger

每个分析面：
- planned
- attempted
- blocked
- completed
- no-signal
- signal
- verified
- disproved

Planner 根据 Coverage Gap 决定下一步。

## Graveyard

低可信候选不立即永久删除。
进入 Graveyard：
- reason
- confidence
- disproof evidence
- resurrection condition

后续新证据可重新激活。
