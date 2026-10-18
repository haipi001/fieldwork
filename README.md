# Fieldwork

**面向 Web/API、Web3 与 AI Agent 的安全研究工作台。**

Fieldwork 将授权范围、研究任务、工具与模型调用、独立验证和报告交付组织为可追溯的工作流。研究人员可以在本地管理目标材料、调查候选问题、协作执行任务，并从验证记录中追踪结论的来源。

项目采用 Python 本地服务、Web 工作台与 macOS 桌面外壳。源码、任务记录、证据材料和模型用量各有明确入口，便于复查研究过程和交接工作。

[文档](docs/README.md) · [安装指南](docs/guides/getting-started.md) · [架构](docs/architecture/README.md) · [路线图](docs/roadmap/README.md) · [贡献指南](.github/CONTRIBUTING.md) · [依赖与致谢](docs/reference/dependencies.md)

> **项目状态：持续开发。** 当前功能开发位于 [`codex/v6-final-control-plane`](https://github.com/haipi001/fieldwork/tree/codex/v6-final-control-plane)。`main` 保留既有业务代码基线并同步仓库结构与公共文档。下面的能力说明与安装步骤以开发分支为准；测试结果、原生安装状态和发布准备分别记录。

## 功能概览

| 领域 | 主要能力 |
| --- | --- |
| Web / API | 目标资料导入、工具编排、候选调查、对象权限验证和修复复测 |
| Web3 / Solidity | 源码分析、调用关系、Forge 属性测试和本地经济实验 |
| AI Agent Audit | 行为材料导入、策略重建、自述与遥测对账、事件验证和审计报告 |
| 研究编排 | 持久任务、角色配置、租约与心跳、检查点、并发及预算控制 |
| 证据与交付 | 来源关联、研究图谱、独立验证回执、历史记录和报告导出 |

```text
授权范围 → 研究材料 → 观察与候选 → 研究任务 → 独立验证 → 影响确认 → 报告与证据包
```

### Web / API 研究

导入目标资料后，可在工作台组织观察、调查候选、关联身份与对象权限条件，并安排适用的复验。对象权限问题的结论需要结合明确的业务授权规则；规则未知、结果不稳定或证据不足时，候选保留审查状态。

研究流程支持分阶段任务、检查点和修复复测。工具输出、HTTP 响应和人工补充材料均需保留来源，正式发现与报告受相应验证条件约束。

### Web3 / Solidity 研究

围绕本地合约源码组织 AST、调用关系、编译材料、属性测试和实验结果。Foundry 工具链用于适用的构建、复测与本地 EVM 实验，编译器版本需与项目配置匹配。

属性失败与稳定复现可以成为候选证据；攻击前提、业务影响、范围适用性和反证仍需进一步确认。工作台保留这些阶段之间的状态区别。

### AI Agent Audit

审计流程覆盖冻结任务与 Policy、导入行为材料、脱敏、自述对账、独立记录验证和事件报告。支持 JSON / JSONL 与 Demo Trace；可信采集器可登记公钥，签名导入通过 nonce 与递增序号限制重放。

采集覆盖取决于具体采集器、安装状态与授权。进程、TCP 和打开文件快照提供有限的观测信息；“没有记录”不能直接推断“没有发生”。数据模型和验证边界见 [AI Agent Audit 文档](https://github.com/haipi001/fieldwork/blob/codex/v6-final-control-plane/docs/architecture/AI_AGENT_AUDIT.md)。

## 任务、模型与验证

- **任务编排**：Research Group 和 AgentTask 持久保存，支持角色、优先级、并发配置、暂停、租约、心跳、检查点与重试。
- **模型运行**：local / cloud / hybrid / offline 路由结合上下文限制、Provider 状态和预算决策；调用用量与结算材料保留在账本中。
- **能力审查**：意图、授权、人工决定与撤销有独立记录。人工决定的支持范围有限，范围、预算、期限与风险条件继续参与执行判断。
- **独立验证**：Verification Receipt 绑定输入、执行上下文和 Evidence；输入变化、不确定结果或缺少有效绑定会阻断正式晋升。
- **研究记忆**：快照保存来源、状态和治理历史，可比较与停用。导入的内容保留其不可信标记，不自动成为模型指令。

模型输出或工具告警本身不会自动成为已确认发现。结果状态、验证依据与尚未确认的影响在工作台中分别展示。

## 快速开始

### 环境要求

| 项目 | 当前范围 |
| --- | --- |
| 桌面构建 | Apple Silicon，macOS 13+ |
| Python | 精确锁定的运行环境面向 CPython 3.13 |
| 原生工具链 | Xcode Command Line Tools，用于 Swift 构建与本地签名 |
| 浏览器检查 | 本机 Google Chrome 与 Playwright |
| 外部能力 | 模型 Provider、RPC 与研究工具按所选能力单独配置 |

### 获取源码并启动

```bash
git clone --branch codex/v6-final-control-plane https://github.com/haipi001/fieldwork.git
cd fieldwork
./scripts/bootstrap_local.sh
FIELDWORK_BUILD_PYTHON="$PWD/.venv/bin/python" ./scripts/build_macos_app.sh
open build/macos/Fieldwork.app
```

`bootstrap_local.sh` 创建虚拟环境、安装声明依赖并进行导入检查。可用 `--dev` 安装开发依赖，用 `--recreate` 重建虚拟环境；普通 bootstrap 不执行逐 wheel 哈希锁定。匹配平台的精确锁文件与独立安装工具见[安装指南](docs/guides/getting-started.md)。

App 关联本地源码与 Python 环境。移动或删除这些路径可能影响启动；当前构建采用本地 ad-hoc 签名，独立分发与公证仍在规划中。

### 首次使用

1. 创建研究项目，确认目标、授权范围及允许的操作。
2. 导入材料或使用隔离 Demo，查看观察与候选。
3. 检查工具和模型状态，设置预算；需要协作时创建研究队伍。
4. 调查候选、关联证据，安排适用的独立验证。
5. 核对攻击条件、影响与验证回执，再预览或导出报告。

业务 API 需要启动会话。桌面 App 为本地服务建立身份与 Cookie；普通浏览器直接访问服务端口不会自动获得该会话。CLI 客户端需要显式会话配置，详见[运行与维护](docs/guides/operations.md)。

## 数据与交付

默认数据目录为本地 `data/`。`FIELDWORK_DATA_DIRECTORY` 可在进程启动前指定符合权限要求的绝对目录；配置在进程内固定，不能用环境变量热切换已有数据。

工作台提供事件筛选、证据关系、报告预览、脱敏选项、数据库/证据备份与隔离恢复演练。Agent Audit 支持 Markdown、JSON、HTML 和带校验清单的 ZIP 证据包。平台报告使用固定字段配置，并保留缺失材料提示。

当前还有目录预检、离线文件暂存及部分引用重映射能力。**预检或暂存成功不等于迁移完成**：正式停机、完整路径审计、配置切换、启动与回滚需各自验收。详细规则见[存储配置](https://github.com/haipi001/fieldwork/blob/codex/v6-final-control-plane/docs/guides/STORAGE_LOCATION_CONFIGURATION.md)。

Git 仓库保存源码、测试与文档。业务数据库、凭据、运行日志、研究证据和本地 App 包由本地存储与备份流程管理。

## 项目结构

```text
.github/       贡献与安全报告指南
backend/       Python API、业务模块、研究控制平面与报告配置
collectors/    行为采集器
docs/          使用指南、架构、路线图、历史资料与验收记录
fixtures/      隔离研究夹具和示例材料
macos/         macOS 原生应用外壳
scripts/       环境准备、构建、验证与维护命令
static/        工作台前端资源
templates/     页面模板
tests/         自动化测试与验证夹具
website/       产品网站源码与资产
```

根目录 `app.py` 和开发分支中的 `desktop_server.py` 是兼容启动入口，业务实现位于 `backend/`。`FINAL/` 保留历史规格包及其内部校验结构。模块职责见[架构说明](docs/architecture/README.md)。

旧代码的引用关系与建议删除范围见[清理审查清单](docs/maintenance/cleanup-audit-2026-10-14.md)。历史文档与验收记录保留在专门目录中，不按文件日期推断代码是否弃用。

## 验证与贡献

```bash
./scripts/bootstrap_local.sh --dev
PYTHONPATH=backend:. .venv/bin/python -m pytest -q
```

涉及工作台交互或显示的修改，还可执行：

```bash
.venv/bin/python scripts/check_v5_workspace_ui.py
```

浏览器检查使用隔离数据库和示例数据，不调用目标或模型。原生安装、外部工具、采集覆盖与实际研究质量需要相应范围的独立验证。具体结果从[验收索引](docs/acceptance/README.md)查阅。

欢迎提交可复现的问题、文档改进和范围明确的 Pull Request。功能开发以 `codex/v6-final-control-plane` 为目标；贡献流程、测试范围和材料要求见[贡献指南](.github/CONTRIBUTING.md)。敏感漏洞请按[安全报告指南](.github/SECURITY.md)处理。

## 依赖与致谢

| 类别 | 主要项目与用途 |
| --- | --- |
| 本地服务与页面 | FastAPI、Uvicorn、Jinja2、python-multipart |
| 网络与浏览器 | Python HTTPX、Playwright、certifi |
| 模型与数据处理 | OpenAI Python SDK、PyYAML、packaging |
| 签名与验证 | cryptography；开发测试使用 pytest 和 jsonschema |
| 按能力选用的工具 | Foundry、Slither、Aderyn、Nuclei、Katana、ProjectDiscovery httpx、Subfinder、Semgrep、Gitleaks、Trivy 等 |

感谢相关项目及其贡献者。直接依赖、可选 Agent 适配、设计参考和各自来源见[依赖与上游项目](docs/reference/dependencies.md)。Python HTTPX 与 ProjectDiscovery `httpx` CLI 是两个独立项目；外部工具需另外安装，不能由依赖表推断为 App 内置能力。

工作台的 Python SBOM 来自声明与安装元数据。它不覆盖全部原生工具，也不代表包完整性或许可证合规已经完成验证。

## 路线图与限制

后续主要工作包括三域真实研究质量验证、多角色研究的成本与证据增益基准、采集器安装与覆盖验收、完整存储迁移及回滚，以及全供应链、独立打包和发布验收。

详见[开发路线图](docs/roadmap/README.md)。已有实现、测试通过和本机可启动分别提供不同范围的证据，整体规划仍在推进。

## 许可证

本仓库尚未指定项目级许可证。第三方依赖、工具、模板和参考材料遵循各自具体版本的许可证；项目许可证与第三方声明的完善情况见[依赖说明](docs/reference/dependencies.md)。
