# SRC AI Security Research OS — FINAL 总开发包

版本：2026-08-27  
用途：交给 Codex 作为**最终产品规范 + 增量迁移规范 + 实现任务源**。

## 这个包是什么

这是前面所有 `SRC AI Agent OS`、传统 SRC、Web3 / Immunefi、P0–P6、双模式前端等设计的**最终收敛版**。

它不再把产品定义成“很多安全工具的 UI”，而定义为：

> **一个从目标输入到可提交漏洞材料输出的 Autonomous Security Research OS。**

最终用户只需要完成：

```text
输入目标
  ↓
确认 Scope / 规则
  ↓
开始分析
  ↓
系统自动建模、枚举、测试、归一、关联
  ↓
验证漏洞
  ↓
分析影响 / Eligibility
  ↓
生成报告
  ↓
导出适配不同平台的提交材料
```

## 最终前端原则

最终用户不面对 42 个页面。

仅保留 5 个一级工作区：

1. **新建分析**
2. **分析过程**
3. **漏洞结果**
4. **报告中心**
5. **设置与工具**

并保留全局模式切换：

`传统 SRC | Web3 / Immunefi`

底层专业模块：
- Recon
- API
- Code
- Protocol Graph
- Static
- Fuzz
- Fork
- Evidence
- Oracle
- Coverage
- Eligibility

全部作为“分析过程”的内部阶段或可展开高级面板，不作为默认一级导航。

## 最终系统核心

```text
Target
  ↓
Target Resolver
  ↓
Engagement + Scope Policy
  ↓
Planner
  ↓
Domain Runtime
  ↓
Tools / Skills / MCP
  ↓
Raw Outputs
  ↓
Normalization
  ↓
Entity / Observation Graph
  ↓
Correlation / Hypothesis
  ↓
Oracle / Verification
  ↓
Canonical Finding
  ↓
Impact / Eligibility
  ↓
Report Compiler
  ↓
Platform Adapter
  ↓
Submission Package
```

## 两个 Domain Runtime

```text
Shared Security Core
├── TraditionalSrcRuntime
└── Web3Runtime
    └── Immunefi Program Adapter
```

两种模式必须共用：
- Engagement
- Scope / Policy
- Agent Runtime
- Tool Registry
- Evidence
- Oracle
- Findings
- Budget
- Checkpoint / Recovery
- Memory
- Report Compiler

## 报告哲学

**Scanner output ≠ Finding。**  
**Candidate finding ≠ Verified finding。**  
**Verified finding ≠ Bounty-eligible finding。**

报告只允许消费：
- VERIFIED
- 或显式标记为 HUMAN_REVIEW 的 finding

系统内部统一使用 `CanonicalFinding`。
HackerOne、Bugcrowd、Intigriti、Immunefi、通用 SRC、SARIF、Markdown、HTML 等都只是 Renderer / Adapter。

## 给 Codex

首先阅读：

1. `codex/FINAL_MASTER_PROMPT.md`
2. `codex/AGENTS_FINAL.md`
3. `docs/00_PRODUCT_NORTH_STAR.md`
4. `docs/01_ONE_CLICK_USER_FLOW.md`
5. `docs/02_SIMPLE_FRONTEND_SPEC.md`
6. `architecture/SYSTEM_ARCHITECTURE.md`
7. `reports/UNIVERSAL_REPORT_MODEL.md`
8. `migration/FINAL_MIGRATION_PLAN.md`

**不要一次性重新开发全部系统。**
先执行 F0 审计和迁移阶段。
