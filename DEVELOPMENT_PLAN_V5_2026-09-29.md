# Fieldwork V5 整体开发规划

更新时间：2026-09-29

2026-10-04 当前进度与实施顺序以 [阶段总计划](docs/DEVELOPMENT_STATUS_2026-10-04.md) 为准。源码 0.68.3 增加 V5 草稿业务规则审阅冻结、分诊后的单候选计划/授权执行/取消/持久状态；独立确认的整体验收仍未完成。规划来源为用户指定的 `fieldwork_v5_1_master_bundle`；本轮 V5-FE-01 切片和下一 V5-ORCH-02 见 [验收记录](docs/acceptance/2026-10-04-v5-fe-01-http-workflow.md)。

## 产品目标

Fieldwork V5 是 Evidence-first Autonomous Research & Verification OS。它在现有 FastAPI、SQLite、Traditional、Web3、AI Agent Audit、Evidence、Candidate、Verification 与报告能力上增加统一研究编排层，不进行整体重写。

当前优先级：UI 0.68.0 已交付；按用户最新要求，先补齐核心功能并跑通端到端流程，不再优先扩大页面或候选数量。以授权与预算、真实发现/证据、自动分诊、复验调度、独立判定和结果恢复为六项核心。详见 `docs/acceptance/CORE_WORKFLOW_DELIVERY.md`，全部完成必须通过实际靶场与安装版验收。

2026-10-02 用户明确最终验收要求：Fieldwork 必须可靠地自动发现并确认漏洞，并要求覆盖安装到本机。产品完成必须由实际运行的漏洞发现与独立确认验收证明；工具接入、单元测试、编排规模、模拟结果均不能替代该证据。详见 `docs/acceptance/RELIABLE_AUTONOMOUS_VULNERABILITY_GATE.md`。该目标尚未达成。

## 当前基线

- 旧业务路由仍保留，但 V5 已迁入研究草稿创建、Scope 确认、执行预检与启动、Run 暂停/恢复/停止以及已验证结果的报告预览/导出；身份、复验等深度操作仍需迁移。
- 新版 `/v5` 工作台承担跨域总览、Research Graph、Agent 编排、漏洞情报、信任层和 AI Runtime 控制面。
- 新前端只展示真实 API 数据。尚未接入的 V5 Overlay 能力显示明确空态，不使用模拟指标。
- API Key 不进入 localStorage、URL、前端日志、证据或报告。

## 阶段路线

### P0：安全与迁移门禁

目标：在 Multi-Agent 和持续研究扩大攻击面前完成基础安全闭环。

- API session/auth、Host/Origin、桌面身份绑定。
- 不可信执行隔离、资源与输出上限、取消和进程树回收。
- Secret Store 与敏感字段脱敏。
- SQLite 增量迁移、备份、回滚和兼容验证。
- 交付证据：威胁模型、迁移演练、失败关闭测试、回滚记录。

### P1：新版前端 Shell

目标：把 V4 视觉与 Runtime UX 合并到 V5 信息架构。

- 统一侧边栏、顶栏、Traditional/Web3/Agent Audit 域切换。
- 总览、Research Graph、Agent 编排、漏洞情报、信任层、AI Runtime 六个一级页面。
- 复用现有 `/api/v1/engagements`、`task-center`、`findings`、`runtime/readiness`、`capabilities`。
- 对 V5 Overlay API 使用渐进增强和显式未接入状态。
- V5 不再保留经典工作台入口；迁移现有操作时保持原后端契约与安全门禁。
- 当前状态：`/` 与 `/v5` 使用新版 17 页 Shell；旧业务路由尚存，但 V5 产品级迁移尚未完成。Events 与 Evidence 已读入所选 Run 的真实事件、Observation、Artifact、Coverage；统一 Ledger 与 Receipt 仍待后端。

### P2：V5 核心数据与 API

目标：安装并集成 Backend Overlay 的核心事实模型。

- ResearchNode、ResearchEdge、ResearchQuestion。
- ResearchGroup、AgentTask、Worker、Lease、Checkpoint。
- RuntimeProfile、RouteDecision、Usage Ledger。
- Verification Receipt。
- API 契约以 `/api/v1` 为唯一前端入口，保留 Legacy Bridge。
- 验收：迁移可重复、旧数据可读、API schema 与任务状态机测试通过。

### P3：编排与运行时

目标：形成可解释、可恢复、受预算控制的 Multi-Agent 执行链。

- Director 负责任务分解，Explorer/Critic/Verifier/Synthesizer 分工。
- 逻辑 Agent 与并发 Worker 分离。
- Lease、heartbeat、重试、暂停、恢复、取消、checkpoint。
- local-first 路由、cloud escalation、独立复验路径。
- 8/16/32 agents 基准，与 single-agent baseline 比较证据增益、成本、精度和重放率。

### P4：Domain Bridge

目标：三条现有研究链进入统一 Research Graph 与 Trust Layer。

- Traditional：Surface、Observation、Candidate、HTTP replay、工具输出。
- Web3：合约、调用图、Fork、状态差异、交易重放。
- Agent Audit：事件、策略、越界、记忆、供应链、攻击场景。
- 所有桥接保持 Observation-first，scanner match 不得直接晋升 Finding。

### P5：持续研究与漏洞情报

目标：用 delta-only、checkpoint 和策略升级实现低成本持续研究。

- 事件或传感器先经过确定性过滤和本地分类。
- 只在阈值触发时升级云端推理。
- Advisory source adapters 实现 terms、rate limit、cache、license、freshness、normalization。
- Intelligence Match 只进入适用性评估和验证任务，不直接产出 Finding。
- 验收：no-op tick 不耗费模型预算，budget stop 与恢复可证明。

### P6：产品收敛与发布

目标：形成可分发、可回滚、可审计的桌面产品。

- 全量中英文、深浅主题、键盘与屏幕阅读器验收。
- SSE 实时事件、断线恢复和节流。
- 版本/tag/commit 固定、许可证复核、SBOM 与第三方 Notice。
- 安装、升级、备份、恢复、卸载和诊断包。
- 独立 UAT、性能基准和发布阻塞清单。

## 前端信息架构

| 一级页面 | 核心问题 | 真实数据源 |
| --- | --- | --- |
| 总览 | 研究什么、正在执行什么、什么变化、什么已验证、哪里需人工处理 | Engagements、Task Center、Findings |
| 研究图谱 | 假设、证据、反证和验证如何关联 | Research Graph V2 |
| Agent 编排 | 谁在做什么、队列和失败在哪里、成本如何 | Orchestration、Runners |
| 漏洞情报 | 哪些情报适用于目标、哪些需要验证 | Intelligence Matches |
| 证据与复验 | 哪些只是 Candidate、哪些已经 Verified | Findings、Receipts |
| AI Runtime | 本地、云端、离线和独立复验如何路由 | Runtime、Capabilities |

## 完成定义

一个阶段只有同时满足以下条件才算完成：

- changed files 与迁移说明清楚。
- 单元、接口、前端语法和浏览器验收通过。
- 安全边界和失败模式有反例测试。
- 真实数据与模拟数据有明确标识。
- 回滚步骤可执行。
- 当前 blocker 与下一个 Task ID 已记录。

## 下一任务顺序

1. `V5-SEC-01`：已完成 Backend Overlay 集成前的安全基线核对，并修复 Host/Origin middleware 未挂载与最小 health 缺失；详见 `docs/acceptance/2026-09-30-v5-sec-01-baseline.md`。
2. `V5-SEC-02`：已完成启动级 API session 与桌面身份绑定的首轮实现：每次桌面启动随机凭据、HttpOnly Cookie、CLI Header、实例 ID/版本握手、旧凭据拒绝、外部导航隔离；真实安装版更新与端口预占整机验收仍待发布阶段执行。详见 `docs/acceptance/2026-09-30-v5-sec-02-session-binding.md`。
3. `V5-BE-01`：已完成增量 V5 Schema、Schema 19→20 备份/回滚副本演练和零自动晋升验证；详见 `docs/acceptance/2026-09-30-v5-be-01-additive-schema.md`。
4. `V5-BE-02`：已完成 Research Graph Node/Edge CRUD、分页、显式幂等 provenance bridge 与 Canonical Receipt guard；详见 `docs/acceptance/2026-09-30-v5-be-02-research-graph.md`。
5. `V5-BE-03`：已完成 AgentTask、ResearchGroup、Runner、原子 Lease/Heartbeat、Checkpoint、恢复、幂等和预算控制；详见 `docs/acceptance/2026-09-30-v5-be-03-orchestration.md`。
6. `V5-BE-05`：已完成不可变 Verification Receipt、独立 verifier task/Runner 绑定、输入快照 hash、Replay 和 Canonical promotion guard；详见 `docs/acceptance/2026-09-30-v5-be-05-verification-receipts.md`。
7. `V5-BE-04`：已完成 local/cloud/hybrid/offline Runtime Router、敏感任务本地约束、健康感知 fallback、Provider Secret 引用与不可变 Usage Ledger；详见 `docs/acceptance/2026-09-30-v5-be-04-runtime-router.md`。
8. `V5-FE-01C`：把 Graph、Orchestrator、Intelligence 和 Receipt 页面切换到真实 Overlay 数据。
9. `V5-ORCH-02`：固定本地 HTTP/包适用性 Oracle 已使用 macOS 沙箱与负向能力探针；通用/远端可信 Verifier、完整跨平台隔离仍未完成。见 `docs/acceptance/2026-10-01-v5-orch-02-macos-verifier-sandbox.md`。
10. `V5-ORCH-03`：跨组精简胶囊、确定性群体评估、rank/select、谱系、mutation/combine Worker 与 Evolution 控制面已接入。六轮跨进程演化、崩溃租约恢复、组预算截止、重试结果隔离及 32 候选容量边界已验证；真实 Provider 长时运行和更长代际压力验收仍待补。见 `docs/acceptance/2026-10-01-v5-orch-03-cross-pollination-evolution.md`、`docs/acceptance/2026-10-01-v5-orch-03-evolver-worker.md`、`docs/acceptance/2026-10-01-v5-orch-03-evolution-control-plane.md` 与 `docs/acceptance/2026-10-02-v5-orch-03-durable-evolution.md`。

11. `V5-SCALE-01`：修复 1100 代谱系递归溢出与重复祖先查询；本地/通用队列可越过 1000 条过期 Scope 任务找到有效任务，保留并发门禁。任务 API 与 Evolution 页面已可游标分页读取 1001 条任务，未读完时禁止汇集。首页/研究图谱可分页读完 1001 节点与 1201 关系，跨页反证边不再丢失。全负载、预算指标、Evolution Claim/历史及其他队列页面上限仍待补。见 `docs/acceptance/2026-10-02-v5-scale-01-history-and-queue.md`、`docs/acceptance/2026-10-02-v5-scale-01-task-pagination.md`、`docs/acceptance/2026-10-02-v5-scale-01-graph-pagination.md`。

## 回滚

- `/v5` 是独立入口，移除该路由和三个 V5 前端文件即可回滚。
- 经典 `/new`、`/run`、`/findings`、`/reports`、`/settings` 路由和现有静态资源未被替换。
- V5 数据迁移均为增量表；回滚应用时必须恢复该版本启动前自动生成并校验的数据库备份，不能让旧应用继续写入新版 Schema。
