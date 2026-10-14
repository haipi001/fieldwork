# 架构概览

Fieldwork 由本地 Python API、Web 工作台和 macOS 应用外壳组成。桌面应用启动本地服务并建立会话；研究操作通过范围、策略、预算和证据验证门进入业务流程。

| 层 | 实现入口（开发分支） | 职责 |
| --- | --- | --- |
| 应用 | `backend/app.py`、`backend/desktop_server.py`、`macos/` | 生命周期、会话握手和原生界面 |
| 业务 | `backend/final_core.py`、`backend/traditional_*.py`、`backend/web3_*.py`、`backend/agent_audit.py` | 研究域、候选、发现与报告 |
| 编排 | `backend/v5_orchestration.py`、`backend/v5_workers.py`、`backend/v5_research_worker.py` | 持久任务、租约、检查点和执行协作 |
| 验证 | `backend/v5_verification.py`、`backend/verification_receipts.py` | 独立回执、证据绑定与结果晋升 |
| 控制平面 | `backend/v6_*.py` | 意图、能力、策略、审批及事件关联 |
| 模型运行 | `backend/v5_runtime.py`、模型网关相关模块 | 路由、预算、用量与结算 |
| 数据维护 | `backend/storage_*.py`、`backend/evidence_backups.py` | 数据位置、存储协作与备份/迁移准备 |
| 界面 | `static/`、`templates/` | 研究操作、记录审查和维护入口 |

源码以[开发分支](https://github.com/haipi001/fieldwork/tree/codex/v6-final-control-plane)为准。更细的专题文档与历史设计从[文档首页](../README.md)进入。

`FINAL/` 是保留原结构的历史规格包，包含清单和摘要校验材料。它不参与当前服务启动；保留内部结构便于核对原始交接资料。
