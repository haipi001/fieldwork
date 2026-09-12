# Final Acceptance Matrix

## UX
- U01 用户只通过 5 个一级工作区完成完整流程。
- U02 顶栏可切换传统 SRC / Web3。
- U03 新建分析默认不要求选择工具。
- U04 分析过程默认不展示命令行噪音。
- U05 Findings 默认只列 Verified。
- U06 Candidate 明确分区。
- U07 报告中心显示材料完整度。

## Pipeline
- P01 Target 能被 canonicalize。
- P02 没有 Scope 时主动动作被阻断。
- P03 Tool output 先进入 Observation。
- P04 Candidate 不自动变 Verified。
- P05 Verified Finding 必须绑定 Evidence。
- P06 Finding 必须绑定 ScopeSnapshot。
- P07 Run 可 Pause / Resume。
- P08 崩溃后可从 checkpoint 继续。

## Reports
- R01 CanonicalFinding 可生成 Generic SRC。
- R02 可生成 HackerOne preview。
- R03 可生成 Bugcrowd preview。
- R04 可生成 Intigriti preview。
- R05 Web3 finding 可生成 Immunefi preview。
- R06 缺 required fields 时不能显示 Ready。
- R07 Renderer 不编造缺失数据。
- R08 Evidence export 自动 redaction。
- R09 结构化 JSON 与 Markdown 内容一致。
- R10 SARIF 只导出适用的静态/代码类 findings，不替代完整报告。

## Traditional SRC
- S01 Web/API target inventory。
- S02 multi-role identity model。
- S03 controlled candidate verification。
- S04 Oracle replay。
- S05 counterevidence。
- S06 Proof Capsule。

## Web3
- W01 ProgramSnapshot versioning。
- W02 source/deployment/commit evidence。
- W03 compile/ABI/AST/bytecode model。
- W04 protocol graph。
- W05 invariant/fuzz。
- W06 local fork state mutation。
- W07 production RPC writes denied。
- W08 public testnet writes denied。
- W09 impact / eligibility。
- W10 Immunefi PoC/report completeness。

## Safety
- A01 out-of-scope blocked。
- A02 rate limit enforced。
- A03 destructive capability requires explicit approval/policy。
- A04 secrets not included in Agent context/log export。
- A05 real Web3 private key not needed。
- A06 third-party active tests denied unless explicitly allowed。
