# Fieldwork V5 前端验收记录

任务：`V5-FE-01`

日期：2026-09-29

复核：2026-09-30

## 交付范围

- 默认首页 `/` 与显式入口 `/v5` 使用 V5 Shell。
- V5 导航不再提供经典工作台回退入口；旧业务路由仍由后端保留，尚未完成的深度操作需继续迁移。
- 一级页面：总览、Research Graph、Agent 编排、漏洞情报、证据与复验、AI Runtime。
- 研究域：Traditional、Web3、Agent Audit。
- 数据源：现有 Engagement、Task Center、Finding、Runtime Readiness、Capability Registry。
- V5 Overlay 缺失时使用明确未接入状态，不生成模拟数据。

## V5-FE-01 完成条件

| 条件 | 证据 | 状态 |
| --- | --- | --- |
| new nav | 17 个规范页面可进入；经典工作台入口已从 V5 移除 | PARTIAL（多域工作流仍需迁移） |
| agent groups | Agent / Runner 有数据时的专用表已实现；真实 Orchestrator API 当前 404，Group 树和调度工作流未接入 | PARTIAL |
| runtime status | 绑定 `/api/v1/runtime/readiness` 与 `/api/v1/capabilities` | PASS |
| zh/en | 偏好切换已实现，但页面内容尚未全量翻译 | PARTIAL |
| dark/light | CSS token 双主题，偏好只保存主题代码 | PASS |

## UI 验收矩阵

| 项目 | 结果 |
| --- | --- |
| 400 / 640 / 800 / 1024 / 1280 / 1440px | 17 个视图在深浅主题下共 204 次自动检查，无页面级横向溢出 |
| loading | 主要异步列表有 skeleton；尚未逐项验证所有页面的最终形态匹配 |
| empty | 项目、任务、图谱、情报、结果与能力均有明确空态 |
| error | 核心接口失败显示连接横幅；Overlay 404 显示未接入状态 |
| keyboard focus | 所有导航、按钮、链接、选择器具有可见 `:focus-visible` |
| Inspector modal | 隔离浏览器验证主内容与侧栏 inert、Tab 留在详情层、Esc 关闭后焦点返回原数据行、命令快捷键不叠加两个弹层 |
| long strings | 数据标题与详情使用可换行布局，无横向溢出 |
| 1000+ rows | Findings 已用 1001 条隔离数据验证分页；其他页面及千行性能耗时尚未验收 |
| Findings 有数据交互 | 隔离 API 注入 1001 条 Verified 和 1 条 Candidate，在 400/1440px 验证 20 条分页、详情、生命周期、证据定位、历史 Proof 成功/失败状态、定向复测选择新 Run、报告跳转、转义与 Candidate 隔离；写操作被拦截，未写入真实数据。尚未记录性能耗时 |
| Reports 预览与导出门槛 | 隔离 API 验证待补草稿、材料齐全、平台切换重置，以及 Proof Capsule 校验失败时仅可预览不可导出；成功时允许导出，并保留后端二次校验。未写入真实报告或材料包 |
| 草稿 → Scope → 计划 → 任务 | 隔离 API 验证授权确认必填、草稿提交、Scope 核对与确认、阻塞计划禁用启动、解除阻塞后人工确认启动并跳转 Tasks，且新任务出现在队列；不向真实服务写入研究目标或启动扫描 |
| Tasks 控制 | 隔离 API 验证 409 拒绝提示与重试、暂停/恢复/停止确认、操作后队列状态更新。后端临时数据库回归确认不支持恢复的 Run 拒绝后仍保持 paused |
| Resume 能力 | Task Center 返回 resume_supported；V5 仅在明确支持时开放恢复。旧服务未返回能力字段时显示不可用；运行中的 8011 服务需重启才加载本轮 Python 修复 |
| V5 配色 | 浏览器检查深色强调色为冷蓝灰 #abcbe9、浅色强调色为 #315f88；旧青柠不再是实际生效的主色 |
| reduced motion | `prefers-reduced-motion` 下禁用非必要动画 |
| console | 真实浏览器验收 0 error |
| true 200% browser zoom / screen reader | 尚未完成；640px 与其他窄视口只验证重排，不能替代真实缩放或屏幕阅读器验收 |

## 自动验证

- `node --check static/v5.js`
- `.venv/bin/python -m pytest -q tests/test_v5_frontend.py`
- `.venv/bin/python scripts/check_v5_frontend_ui.py`（需运行中的本机服务；可用 `FIELDWORK_V5_BASE` 指定地址）
- `.venv/bin/python -m pytest -q tests/test_final.py::test_execution_plan_exposes_real_tools_budgets_degradation_and_scope_blockers tests/test_final.py::test_task_center_reports_remaining_stages_and_actionable_next_step`（临时数据库）
- `git diff --check`

## 安全边界

- 不在前端持久化 API Key 或 Secret。
- Runtime 模式按钮现禁用并明确标注后端 RuntimeProfile 写入接口未接入；不再将显示偏好呈现为真实路由切换。真实配置仍待接入。
- Intelligence Match 不直接晋升 Finding。
- Candidate 与 Verified 在界面中保持分层。

## 后续接线

- `V5-SEC-01`：Backend Overlay 前置安全基线。
- `V5-BE-01`：Research Graph 真实数据。
- `V5-BE-02`：ResearchGroup、AgentTask、Scheduler。
- `V5-BE-05`：Verification Receipt。
