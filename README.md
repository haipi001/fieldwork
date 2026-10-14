# Fieldwork

**面向 Web/API、Web3 与 AI Agent 的安全研究工作台。**

Fieldwork 将授权范围、研究任务、工具与模型调用、独立验证和报告交付组织为可追溯的工作流。项目采用本地服务与 macOS 桌面界面，支持研究材料管理、任务编排及证据审查。

[文档](docs/README.md) · [快速开始](docs/guides/getting-started.md) · [路线图](docs/roadmap/README.md) · [贡献指南](.github/CONTRIBUTING.md) · [依赖与致谢](docs/reference/dependencies.md)

> **开发状态：** 项目处于持续开发阶段。当前实现位于 [`codex/v6-final-control-plane`](https://github.com/haipi001/fieldwork/tree/codex/v6-final-control-plane)；`main` 保留既有代码基线并同步公共文档。以下功能和安装步骤以开发分支为准。

## 功能

| 领域 | 能力 |
| --- | --- |
| Web / API | 目标资料导入、工具编排、候选调查、对象权限验证和修复复测 |
| Web3 / Solidity | 源码分析、调用关系、Forge 属性测试和本地经济实验 |
| AI Agent Audit | 行为材料导入、策略重建、自述与遥测对账、事件验证和审计报告 |
| 研究编排 | 持久任务、角色配置、租约与心跳、检查点、并发及预算控制 |
| 证据管理 | 来源关联、研究图谱、独立验证回执、历史记录和报告导出 |

```text
授权范围 → 研究材料 → 候选与任务 → 独立验证 → 影响确认 → 报告与证据包
```

验证结果绑定输入、执行上下文与证据。未确认的候选保留其状态和审查依据；模型输出或工具告警本身不会自动成为已确认发现。架构概览见 [Architecture](docs/architecture/README.md)。

## 快速开始

当前桌面构建面向 **Apple Silicon macOS 13+**，需要 Python 环境和 Xcode Command Line Tools。精确锁定的运行环境面向 **CPython 3.13**。

```bash
git clone --branch codex/v6-final-control-plane https://github.com/haipi001/fieldwork.git
cd fieldwork
./scripts/bootstrap_local.sh
FIELDWORK_BUILD_PYTHON="$PWD/.venv/bin/python" ./scripts/build_macos_app.sh
open build/macos/Fieldwork.app
```

App 关联本地源码与 Python 环境，并为后台服务建立会话。目前构建产物采用本地签名；独立分发与公证仍在规划中。环境重建、模型配置、外部工具和数据目录设置见 [安装与使用指南](docs/guides/getting-started.md)。

## 项目结构

```text
.github/      贡献与安全报告指南
backend/      Python API、业务与研究控制平面
docs/         使用指南、架构、路线图和历史记录
macos/        macOS 原生应用外壳
collectors/   行为采集器
static/       工作台前端资源
templates/    页面模板
scripts/      环境、构建与维护工具
tests/        自动化测试与验证夹具
website/      产品网站
```

Python 业务模块位于 `backend/`；根目录仅保留启动兼容入口。模块职责见 [架构说明](docs/architecture/README.md)；历史计划集中在 [文档归档](docs/archive/README.md)，验证记录集中在 [验收索引](docs/acceptance/README.md)。

## 开发与贡献

欢迎提交问题、文档改进和范围明确的 Pull Request。开始修改前，请阅读 [贡献指南](.github/CONTRIBUTING.md)，并基于当前开发分支开展工作。

```bash
./scripts/bootstrap_local.sh --dev
PYTHONPATH=. .venv/bin/python -m pytest -q
```

浏览器验收、测试范围和提交约定见贡献指南。安全问题请按 [安全报告指南](.github/SECURITY.md) 处理。

## 依赖与致谢

Fieldwork 使用 FastAPI、Uvicorn、Jinja2、HTTPX、Playwright、OpenAI Python SDK 等 Python 项目，并按研究能力接入 Foundry、Slither、Nuclei、Semgrep 等外部工具。

感谢相关项目及其贡献者。直接依赖、可选适配器、设计参考和各自用途见 [依赖与上游项目](docs/reference/dependencies.md)。

## 状态与限制

当前已有本地开发和隔离验收记录。完整研究质量基准、采集覆盖、正式存储迁移与回滚、完整供应链和发布验收仍在推进，详见 [路线图](docs/roadmap/README.md)。Git 仓库保存源码、测试和文档；业务数据库、凭据、运行日志及研究证据由本地存储流程管理。

## 许可证

本仓库目前尚未指定项目级许可证。第三方依赖、工具、模板及参考材料遵循各自具体版本的许可证；项目许可证与第三方声明的完善情况见 [依赖说明](docs/reference/dependencies.md)。
