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
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app:app --host 127.0.0.1 --port 8000
```

打开 [本地工作台](http://127.0.0.1:8000/new)。外部工具和模型需按使用场景配置，详见 [运行与维护](OPERATIONS.md)。

## AI Agent Audit

顶部第三工作域 **AI Agent Audit** 已实现离线审计闭环：冻结任务与 Policy，导入并脱敏 JSON / JSONL / Fieldwork Demo Trace 行为材料，对账 Agent 自述与独立遥测，确定性评估网络、文件、工具、权限和副作用边界，再由独立记录重建验证门确认事件。可信采集器可登记 Ed25519 公钥，签名导入使用 nonce 和递增序号阻止重放；私钥不进入 Fieldwork。报告可导出 Markdown、JSON、HTML 和带校验清单的 ZIP 证据包。

工作台支持中文与英文界面切换，语言偏好保存在本机；项目名称、用户输入、原始证据和报告内容不会被自动翻译。完整模型、验证门、API、Demo、指标与限制见 [AI Agent Audit 架构](docs/AI_AGENT_AUDIT.md)。

## 当前阶段

持续开发中的个人研究工作台。候选不等于漏洞，属性失败仍需确认攻击条件与实际影响。桌面安装与不可信项目执行隔离正在完善，当前能力及缺口见 [综合评估](APPLICATION_ASSESSMENT_2026-09-11.md)。

## 开发与接力

- [完整开发计划](DEVELOPMENT_MASTER_PLAN_2026-09-11.md)：任务、依赖与验收标准
- [AI 接力说明](HANDOFF_TO_NEXT_AI.md)：代码入口与当前状态
- [仓库与网站维护](REPOSITORY_HANDOFF.md)：同步目录与发布方式
- [详细实现记录](docs/IMPLEMENTATION_NOTES.md)：原 README 的技术说明，部分历史陈述以最新评估为准

本仓库不包含生产数据库、凭据和运行日志。公开官网可自由访问；源码仓库目前为私有，需要访问授权。
