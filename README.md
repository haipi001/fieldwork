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
| **证据与报告** | 来源关联、复验记录、影响材料与证据包导出 |

## 快速启动

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app:app --host 127.0.0.1 --port 8000
```

打开 [本地工作台](http://127.0.0.1:8000/new)。外部工具和模型需按使用场景配置，详见 [运行与维护](OPERATIONS.md)。

## AI Agent Audit（规划中）

顶部现已提供第三工作域 **AI Agent Audit**；当前为模式入口与规划页，审计功能尚未接入。新工作域将对账 Agent 自述与独立遥测，复用现有 Scope、Run、证据、验证和报告底座。

完整模型、验证门、API 计划、Demo、指标与分阶段验收见 [AI Agent Audit 架构](docs/AI_AGENT_AUDIT.md)。

## 当前阶段

持续开发中的个人研究工作台。候选不等于漏洞，属性失败仍需确认攻击条件与实际影响。桌面安装与不可信项目执行隔离正在完善，当前能力及缺口见 [综合评估](APPLICATION_ASSESSMENT_2026-09-11.md)。

## 开发与接力

- [完整开发计划](DEVELOPMENT_MASTER_PLAN_2026-09-11.md)：任务、依赖与验收标准
- [AI 接力说明](HANDOFF_TO_NEXT_AI.md)：代码入口与当前状态
- [仓库与网站维护](REPOSITORY_HANDOFF.md)：同步目录与发布方式
- [详细实现记录](docs/IMPLEMENTATION_NOTES.md)：原 README 的技术说明，部分历史陈述以最新评估为准

本仓库不包含生产数据库、凭据和运行日志。公开官网可自由访问；源码仓库目前为私有，需要访问授权。
