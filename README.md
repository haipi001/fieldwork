<div align="center">

# Fieldwork

### 让安全研究，有据可循。

Web3 与 Web/API 安全研究工作台

[产品官网](https://fieldwork-research.lizekai136.chatgpt.site/) · [支持范围](https://fieldwork-research.lizekai136.chatgpt.site/#scope) · [开发计划](DEVELOPMENT_MASTER_PLAN_2026-09-11.md) · [接力说明](HANDOFF_TO_NEXT_AI.md)

</div>

---

从目标与线索，到独立复验和报告，Fieldwork 将研究过程组织成一条可追溯的证据链。

```text
目标与范围 → 观察与候选 → 独立复验 → 影响确认 → 报告与证据包
```

| 研究方向 | 当前能力 |
| --- | --- |
| **Web3 / Solidity** | 源码绑定 AST、调用关系、Forge 属性复测、本地经济正反实验 |
| **Web / API** | 目标资料导入、工具编排、候选分诊、适用的对象权限复验 |
| **AI Agent Audit** | 离线行为导入、Policy 重建、自述对账、独立验证与事件证据包 |
| **证据与报告** | 来源关联、复验记录、影响材料与证据包导出 |

## 快速启动

```bash
./scripts/bootstrap_local.sh
```

需要从空环境重建时执行 `./scripts/bootstrap_local.sh --recreate`。脚本会安装锁定依赖并执行启动导入检查；开发验收环境使用 `./scripts/bootstrap_local.sh --recreate --dev`。

日常使用请从 Fieldwork 桌面 App 打开工作台。后端业务接口现在要求每次启动会话；直接运行 uvicorn 只适合带显式 `FIELDWORK_SESSION_TOKEN` 的 CLI/测试客户端，普通浏览器不会获得桌面 HttpOnly Cookie。外部工具和模型配置详见 [运行与维护](OPERATIONS.md)。

## AI Agent Audit

顶部第三工作域 **AI Agent Audit** 已实现离线审计闭环：冻结任务与 Policy，导入并脱敏 JSON / JSONL / Fieldwork Demo Trace 行为材料，对账 Agent 自述与独立遥测，确定性评估网络、文件、工具、权限和副作用边界，再由独立记录重建验证门确认事件。可信采集器可登记 Ed25519 公钥，签名导入使用 nonce 和递增序号阻止重放；私钥不进入 Fieldwork。报告可导出 Markdown、JSON、HTML 和带校验清单的 ZIP 证据包。

工作台支持中文与英文界面切换，语言偏好保存在本机；项目名称、用户输入、原始证据和报告内容不会被自动翻译。完整模型、验证门、API、Demo、指标与限制见 [AI Agent Audit 架构](docs/AI_AGENT_AUDIT.md)。

## 当前阶段

持续开发中的个人研究工作台。候选不等于漏洞，属性失败仍需确认攻击条件与实际影响。桌面安装与不可信项目执行隔离正在完善，当前能力及缺口见 [综合评估](APPLICATION_ASSESSMENT_2026-09-11.md)。

V5 编排 API 已支持 Research Group、持久 AgentTask、原子 Lease/Heartbeat、Checkpoint、重试和预算停止。编排器只分配声明式工作，不直接执行目标工具；Runner 的实际操作仍受 Scope、Policy 与隔离边界约束。

V5 Verification Receipt 由独立 verifier task 的当前租约签发，绑定输入快照、Runner/环境、Replay Contract、结果和 Evidence，并以 SHA-256 与数据库不可变约束保护。`inconclusive`、中断、自证、输入变化或无 binding 的记录不能晋升 Canonical Result。

V5 Runtime Router 已实现 local/cloud/hybrid/offline 的真实后端决策，敏感上下文强制本地，独立复验不降级到普通模型。Provider 凭据只以后台 secret ref 关联，Route Decision 与 Usage Ledger 不可变且受 Campaign/Task 预算约束。

## 开发与接力

- [完整开发计划](DEVELOPMENT_MASTER_PLAN_2026-09-11.md)：任务、依赖与验收标准
- [AI 接力说明](HANDOFF_TO_NEXT_AI.md)：代码入口与当前状态
- [仓库与网站维护](REPOSITORY_HANDOFF.md)：同步目录与发布方式
- [详细实现记录](docs/IMPLEMENTATION_NOTES.md)：原 README 的技术说明，部分历史陈述以最新评估为准

本仓库不包含生产数据库、凭据和运行日志。公开官网可自由访问；源码仓库目前为私有，需要访问授权。
