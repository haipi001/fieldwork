# V5-FE-01：业务规则审阅与单候选受控复验

日期：2026-10-04。源码检查点：0.68.3 / build 81 / schema 25。

## 规划来源与本轮范围

依据 `/Users/lizekai/Downloads/fieldwork_v5_1_master_bundle` 中的 `DEVELOPMENT_FLOW.md`、`KNOWN_GAPS.md`、`research_v5_devkit.zip/TASKS.json` 与 `FRONTEND_PLAN.md`。遵守一轮一个 Task，本轮推进 V5-FE-01 的操作集成切片，沿用 FastAPI、SQLite 与现有 V5 页面。Task 和整个规划的完成度不由本切片重新定义。

## Changed files

- `final_core.py`：结构化草稿业务规则 API；新建草稿 Scope 版本；过期编辑和过期审阅确认拒绝；已确认项目不可通过此入口修改。
- `guided_research.py`：无网络预检；精确规则、共享主体、授权、当前输入、运行状态、剩余预算和活跃任务门；显式确认后启动单候选持久任务；每次请求前检查引用材料与授权；可恢复读取已审阅任务。
- `static/v5-http-rules.js`、`static/v5-candidate-workflow.js`、`static/v5.js`、`static/v5-workflows.css`、`templates/v5.html`：V5 规则编辑与只读冻结视图、分诊后执行计划、确认、进度、取消、失败和刷新后的持久任务查看。保留中英、明暗主题；权限类型不默认猜测为 owner_only。
- `tests/test_v5_http_workflow.py`、`scripts/check_v5_http_workflow_ui.py`：后端安全边界、真实 loopback HTTP 与隔离浏览器操作验收。
- `version.py` 与开发状态/交接文档：源码版本与能力边界更新；静态资源缓存版本更新。

## Migration

无数据库迁移，schema 25 保持。编辑草稿通过已有 `scope_snapshots` 表新增版本，不覆盖旧版本；未确认且无 Run 的 Traditional 草稿才可编辑。规则保存不授予执行权限，仍需审阅确认；确认绑定前端看到的 Scope ID。当前入口不修改已确认项目，新规则需要新研究草稿和新运行。

## Tests

- 全量 Python：699 passed / 3 skipped。既有 AnyIO/Starlette 依赖弃用提示仍在。
- 新增专项 19 项：草稿版本/冻结、重复/越界/凭据/无依据/冲突规则拒绝、授权确认、预算/身份/证据/Scope 变化阻断、首请求前取消、执行中材料变化停止。
- 实际本地 HTTP：先采集 3 次真实响应，计划读取不发请求；确认后两轮 10 次请求，保存读取事实和 denied 业务边界；正式 Finding 为 0。合法 allowlist 共享阻止本入口执行。
- 隔离 UI：未保存不能冻结、语言切换保留输入、保存与确认分别提交、计划只读、明确确认后启动、取消、刷新后查看持久任务、过期计划拒绝及只读冻结规则。390/1280 宽度与明暗主题无横向溢出，无 pageerror。
- 原 V5 UI：18 页 × 6 宽度 × 2 主题，共 216 次视图检查；原草稿到运行、任务控制、Inspector、已验证结果等检查通过。原只读分诊验收通过。
- Python 编译、JS 语法及 `git diff --check` 通过。

浏览器请求全部拦截；真实 HTTP 用例只连接临时 loopback 服务，数据库与凭据供应均在测试夹具中。候选构造和测试账号供应为 fixture，不能计为真实模型发现、独立执行器验收或安装版验收。截图位于忽略目录 `build/v5-http-workflow-acceptance/`。

## Benchmark delta

本轮没有模型或多智能体性能变化，无新的成本/质量 benchmark。三次采集与十次复验的请求记账由真实 HTTP 专项核对。

## Blockers

测试账号管理与登录仍需完整迁入 V5；该入口使用已有 ready 身份和已记录的对象/身份接口绑定。HTTP 独立进程执行、Receipt 与正式晋升尚未接通；本次受控请求仍在现有 Guided Worker 中执行。真实工具/模型发现、实际影响、三次清洁启动与故障恢复产品验收尚未完成。

源码与资源更新到 0.68.3；本轮未构建或覆盖安装桌面 App。上一安装检查点为 0.68.2 / build 80，不能把本轮隔离 UI 结果当作安装版验收。桌面启动器依赖工作区运行时，后续启动可能加载更新源码，原安装资源仍需按发布流程同步。

## Rollback

本轮前基线为 Git `557298a`，已与远端同步；回退源代码应优先用独立工作树或针对本提交的 revert，保留用户数据。没有删除用户数据或覆盖旧 Scope；新增草稿规则版本可由旧数据库结构读取。源代码整体回退到更早的业务规则门之前会恢复旧判定行为，不应用作发布回滚。

## Next task ID

V5-ORCH-02：由独立进程从真实 HTTP 引用材料重建最小复现和负对照，绑定当前输入、授权、过程观察和回执，并按正例、修复负例、合法共享反例验收。V5-FE-01 尚存的账号登录与深层配置操作保持待办。
