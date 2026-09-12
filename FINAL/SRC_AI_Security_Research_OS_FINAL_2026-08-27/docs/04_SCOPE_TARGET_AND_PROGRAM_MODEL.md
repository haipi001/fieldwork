# 04 — Target / Scope / Program Model

## TargetSpec

系统先识别 Target，再选择 Runtime。

### Traditional SRC target kinds
- URL
- domain
- IP/CIDR
- API spec
- Git repo
- source archive
- web app

### Web3
- Immunefi program URL
- repo
- contract
- local project

## Engagement

任何主动分析都必须属于 Engagement：

```text
Target
+ ScopeSnapshot
+ ExecutionPolicy
+ Mode
+ Objective
+ Budget
= Engagement
```

## ScopeSnapshot

不可覆盖旧版本。

字段：
- source
- captured_at
- hash
- allowed assets
- denied assets
- allowed actions
- denied actions
- rate limits
- credentials/test accounts
- disclosure rules
- program custom rules

Finding 永远绑定创建时的 ScopeSnapshot。

## Web3 ProgramSnapshot

额外包含：
- assets in scope
- impacts in scope
- known issues
- previous audits
- severity system
- primacy mode
- PoC requirements
- feasibility rules
- prohibited activities

## Scope Gate

Action 执行前：

```text
Planner proposes action
  ↓
Policy Engine
  ↓
Scope Engine
  ↓
Tool Adapter
  ↓
Transport guard
```

必须多层，而不是只靠 prompt。
