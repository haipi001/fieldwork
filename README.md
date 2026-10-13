<div align="center">

# Fieldwork

### 让安全研究，有据可循。

**AI Security Research OS · Web/API、Web3 与 AI Agent Audit 安全研究工作台**

[产品官网](https://fieldwork-research.lizekai136.chatgpt.site/) · [最新开发源码](https://github.com/haipi001/fieldwork/tree/codex/v6-final-control-plane) · [文档导航](https://github.com/haipi001/fieldwork/blob/codex/v6-final-control-plane/docs/README.md)

</div>

## 项目是什么

Fieldwork 将授权范围、研究任务、工具与模型调用、独立复验、证据和报告组织到一个本地工作台。研究人员可以从项目和线索开始，追踪候选的来源，安排有预算约束的研究任务，再根据独立验证结果决定是否形成正式发现。

```text
项目与授权范围 → 观察与候选 → 研究任务 → 独立复验 → 影响确认 → 报告与证据包
                       ↕              ↕             ↕
                    来源与图谱     策略与预算      回执与历史
```

候选、模型结论、工具告警和属性失败都需要相应证据与业务条件。系统保留无法确认的结果和阻断原因，不将它们自动标记为已确认漏洞。

## 分支与当前状态

> GitHub 首页默认展示 `main`。持续开发代码位于 [`codex/v6-final-control-plane`](https://github.com/haipi001/fieldwork/tree/codex/v6-final-control-plane)。下文描述该开发分支的能力；使用命令前请切换到该分支。首页说明更新不代表开发代码已经合并至 `main`。

| 项目 | 开发分支状态 |
| --- | --- |
| 源码版本 | `0.68.73`，build `151`，数据库 schema `41` |
| 产品阶段 | 持续开发，适合授权、受控的个人研究与验证 |
| 工作台 | 三个研究域，项目、队伍、任务、图谱、证据、事件、策略与维护入口 |
| 最近完整快照 | `9a7efb0`；其验收为 1020 项通过、3 项跳过及 144 页面/显示组合通过 |
| 后续增量 | 存储协作锁、离线文件暂存、部分数据库文件引用重映射；2026-10-13 回归 1054 项通过、3 项跳过，浏览器 144 显示组合通过 |
| 完整规划 | 尚未完成；源码实现、自动测试、原生 App 验收与发布准备分别记录 |

上述测试数量对应指定快照，不是所有后续提交或任意安装环境的保证。最新验证和未闭合项见 [文档导航](https://github.com/haipi001/fieldwork/blob/codex/v6-final-control-plane/docs/README.md)。

## 三个研究域

| 方向 | 已有能力 | 使用边界 |
| --- | --- | --- |
| **Web / API** | 目标资料导入、工具编排、候选调查与分诊、适用的对象权限复验、修复复测与证据关联 | 对象权限结论需明确业务授权规则；模糊结果保持人工审查 |
| **Web3 / Solidity** | 源码绑定 AST、调用关系、Forge 属性复测、本地经济正反实验与源码材料关联 | 需要匹配工具链、攻击前提及实际影响；属性失败不能直接替代漏洞证明 |
| **AI Agent Audit** | 冻结任务与 Policy、离线行为导入、Agent 自述对账、独立记录验证、事件报告及证据包 | 采集覆盖取决于具体采集器与授权；没有记录不能证明行为没有发生 |

AI Agent Audit 支持脱敏 JSON、JSONL 与 Demo Trace 导入。可信采集器可以登记 Ed25519 公钥；签名导入通过 nonce 和递增序号防止重放，私钥不进入 Fieldwork。macOS 进程、TCP 与打开文件快照提供有限观测证据；完整浏览器与系统级采集仍需要各自的安装及端到端验收。详见 [审计架构](https://github.com/haipi001/fieldwork/blob/codex/v6-final-control-plane/docs/AI_AGENT_AUDIT.md)。

## 工作台与执行控制

- **项目与范围**：管理研究项目、目标材料及允许的操作范围，并把范围关联到后续研究与验证。
- **队伍与任务**：持久 Research Group / AgentTask、角色和并发配置、优先级、暂停、租约、心跳、检查点、重试与预算停止；逻辑任务规模已有 100/1000 任务验证。
- **研究图谱**：组织观察、候选、节点与关系，检索路径和来源，关联任务上下文与证据。
- **独立复验**：Verification Receipt 绑定输入快照、当前 verifier 租约、环境、Replay Contract、结果与 Evidence；材料变化、自证、不确定或中断结果阻断正式晋升。
- **模型运行**：local / cloud / hybrid / offline 路由、敏感上下文本地约束、用量账本与费用对账；Provider 凭据通过后台 secret reference 关联。
- **能力与策略**：意图申请、授权审查、撤销及不可变人工决定；预算、范围、域、期限和风险门继续生效。支持的人工批准范围有限，不自动启动执行。
- **记录与交付**：事件过滤、证据关系、报告预览、脱敏选项与导出；Agent Audit 可导出 Markdown、JSON、HTML 和含校验清单的 ZIP。
- **维护与供应链**：数据库/证据备份、隔离恢复演练、恢复前差异检查、扩展包保存与版本回退、Python 依赖许可证元数据及 CycloneDX SBOM。

本地扩展包目前以禁用状态保存，保存、回退和静态预检不代表代码已经获准运行。Python SBOM 也不代表完整原生/外部工具供应链、包完整性或许可证合规已经验证。

工作台提供中英文界面；语言切换不会自动翻译用户输入、项目名称、原始证据或报告内容。

## 开始使用

### 1. 获取开发分支

```bash
git clone --branch codex/v6-final-control-plane https://github.com/haipi001/fieldwork.git
cd fieldwork
```

已有仓库先保留本地改动，再切换和更新分支。当前原生构建面向 Apple Silicon macOS；其他操作系统不能据此视为已完成原生适配。

### 2. 准备 Python 环境

```bash
./scripts/bootstrap_local.sh
```

脚本创建 `.venv`、安装 `requirements.txt` 并进行导入检查。需要开发依赖时使用 `--dev`；需要重建虚拟环境时使用 `--recreate`。普通 bootstrap 使用版本约束文件，**不使用逐 wheel 哈希锁定**。

对于 **macOS arm64 + CPython 3.13**，仓库另有 [`requirements-macos-arm64-py313.lock`](https://github.com/haipi001/fieldwork/blob/codex/v6-final-control-plane/requirements-macos-arm64-py313.lock) 与锁定运行环境安装工具。使用方式和已验证范围见 [干净锁定环境验收](https://github.com/haipi001/fieldwork/blob/codex/v6-final-control-plane/docs/acceptance/2026-10-13-clean-locked-runtime.md)；其他平台需自己的匹配锁文件。

### 3. 构建并打开本地 App

在安装 Xcode Command Line Tools、准备上述依赖后：

```bash
FIELDWORK_BUILD_PYTHON="$PWD/.venv/bin/python" ./scripts/build_macos_app.sh
open build/macos/Fieldwork.app
```

目前生成的 App 关联当前源码目录与 Python 环境；移动或删除这些路径会影响启动。构建使用本地 ad-hoc 签名，尚不是独立分发、公证完成的安装包。

日常从桌面 App 进入工作台。后台接口要求启动会话，桌面握手设置 HttpOnly Cookie；普通浏览器直接打开 uvicorn 地址不会自动获得此会话。CLI 与测试客户端需要显式 `FIELDWORK_SESSION_TOKEN`。详细配置、外部工具与模型接入见 [运行与维护](https://github.com/haipi001/fieldwork/blob/codex/v6-final-control-plane/OPERATIONS.md)。

### 4. 完成一次研究

1. 创建项目，确认目标与授权范围。
2. 导入材料或使用隔离 Demo，查看观察和候选。
3. 配置可用工具与模型；需要时创建队伍并检查策略、预算及运行条件。
4. 调查候选、关联证据，安排适用的独立复验。
5. 核对影响、授权规则与验证回执，再预览和导出报告。

## 数据、备份与迁移

默认数据存于本地 `data/`。`FIELDWORK_DATA_DIRECTORY` 可在进程启动前指定已存在、当前用户所有且权限私有的绝对目录；无效显式配置停止启动，不静默回退。完整规则见 [存储位置配置](https://github.com/haipi001/fieldwork/blob/codex/v6-final-control-plane/docs/STORAGE_LOCATION_CONFIGURATION.md)。

设置页提供目录/容量预检；离线工具可暂存文件并校验摘要，对部分操作性文件引用进行重映射。**预检或暂存成功不等于完成迁移**：外部写入者停机、全部路径审计、配置切换、真实 App 启动与回滚仍需要完整验收。不要把暂存目录直接当成已激活数据目录。

Git 同步保存源码、测试与文档，不包含生产数据库、凭据、运行日志、研究证据或 App 包。业务数据备份应使用专门的备份流程，不能用 Git 提交代替。

## 验证与开发

在准备开发依赖后：

```bash
PYTHONPATH=. .venv/bin/python -m pytest -q
.venv/bin/python scripts/check_v5_workspace_ui.py
```

浏览器检查脚本使用隔离数据库和本机 Google Chrome，执行真实工作台/API 操作及页面显示组合检查，不调用目标或模型。原生安装、外部工具及实际研究质量需各自验收；自动测试通过不直接代表发布准备完成。

## 仓库导航

| 位置 | 用途 |
| --- | --- |
| `app.py`、`final_core.py` | 应用入口与共享业务逻辑 |
| `v5_*.py`、`v6_*.py` | 图谱、编排、独立验证、模型运行和控制平面 |
| `agent_*.py`、`web3_*.py`、`traditional_*.py` | 三个研究域与采集/工具接口 |
| `storage_*.py`、`evidence_backups.py` | 存储配置、协作锁、暂存与备份 |
| `static/`、`templates/` | 工作台前端 |
| `macos/`、`collectors/` | 原生外壳和采集器 |
| `tests/`、`fixtures/`、`benchmarks/` | 回归、隔离夹具与基准材料 |
| `scripts/` | 环境准备、构建、验收和维护命令 |
| `docs/`、`docs/acceptance/` | 当前导航、设计说明和按日期保留的验收证据 |
| `FINAL/`、根目录日期文档 | 历史规划/交接材料，不能单凭文件存在判断当前实现 |
| `website/` | 公共产品网站源码 |

模块数量随功能增长，主要阅读问题是根目录技术文档与源码混放、多个历史规划入口并存。当前通过 [文档导航](https://github.com/haipi001/fieldwork/blob/codex/v6-final-control-plane/docs/README.md) 区分入口和历史证据。后续目录迁移需同步维护 Python 导入、工具脚本、测试和历史引用，避免直接移动文件造成启动或验证断链。

## 接下来仍需完成

- 三个研究域的真实授权输入到独立证明、修复复测和交付的完整质量验收。
- 多角色研究相对单角色的成本、证据增益、精度与重放质量基准。
- 全部执行类型与采集器的安装、覆盖和恢复证据。
- 正式存储迁移、完整恢复切换及回滚流程。
- 全供应链版本/来源/许可证清单，以及独立打包、签名、公证与发布门。

完整目标保留在 [Master Bundle 范围审计](https://github.com/haipi001/fieldwork/blob/codex/v6-final-control-plane/docs/MASTER_BUNDLE_PROGRESS_AUDIT_2026-10-04.md) 和 [UI/核心交付计划](https://github.com/haipi001/fieldwork/blob/codex/v6-final-control-plane/docs/V5_UI_AND_CORE_DELIVERY_PLAN_2026-10-04.md)。已有实现与局部验收持续积累，整体规划保持进行中。
