# System Architecture

```text
┌──────────────────────────────────────────────────────────────┐
│                         Simple UI                            │
│ New Analysis | Run | Findings | Reports | Settings          │
└───────────────────────────┬──────────────────────────────────┘
                            │
                       API / Events
                            │
┌───────────────────────────▼──────────────────────────────────┐
│                    SECURITY RESEARCH CORE                    │
│                                                              │
│ Engagement ─ Scope/Policy ─ Planner ─ Executor               │
│      │             │          │          │                   │
│      │             │          │       Tool Runtime           │
│      │             │          │       Skill / MCP            │
│      │             │          │       Sandbox                │
│      │             │          │                              │
│      └──── Target Graph / Coverage / Budget / Recovery ──────┤
│                                                              │
│ Observation → Correlation → Verification → Finding           │
│       │             │             │            │              │
│     Evidence      Hypothesis       Oracle     Impact          │
└───────────────────────────┬──────────────────────────────────┘
                            │
                ┌───────────┴───────────┐
                │                       │
┌───────────────▼────────────┐ ┌────────▼──────────────────────┐
│ Traditional SRC Runtime    │ │ Web3 Runtime                  │
│ web/api/code/network/auth  │ │ program/contract/model/fuzz  │
│ browser/http/recon         │ │ fork/impact/eligibility      │
└───────────────┬────────────┘ └────────┬──────────────────────┘
                │                       │
                └───────────┬───────────┘
                            │
┌───────────────────────────▼──────────────────────────────────┐
│                      REPORT COMPILER                         │
│ CanonicalFinding + Evidence + ProgramSnapshot                │
└───────────────────────────┬──────────────────────────────────┘
                            │
┌───────────────────────────▼──────────────────────────────────┐
│ Platform Adapters                                             │
│ Generic | HackerOne | Bugcrowd | Intigriti | Immunefi       │
│ Markdown | HTML | JSON | SARIF                               │
└──────────────────────────────────────────────────────────────┘
```

## 关键点

### Shared Core 不允许知道具体工具名称
Core 只处理 capability：
- discover_assets
- crawl
- inspect_http
- analyze_code
- fuzz_input
- analyze_contract
- test_invariant
- local_simulation

Tool Registry 决定实际使用哪个 adapter。

### DomainRuntime

```python
class DomainRuntime(Protocol):
    mode: SecurityMode
    def target_kinds(self) -> set[str]: ...
    def capabilities(self) -> list[Capability]: ...
    async def build_model(self, engagement): ...
    async def propose_plan(self, engagement, objective): ...
    async def assess_impact(self, finding): ...
```

### Tool Adapter

所有 adapter 必须输出统一 `ToolResultEnvelope`。
禁止 orchestrator 直接解析 CLI stdout。

### Event-driven
Run 过程中发布：
- target.resolved
- scope.compiled
- observation.created
- candidate.created
- verification.completed
- finding.verified
- report.ready

前端只消费事件和状态，不依赖 Agent 内部 prompt。
