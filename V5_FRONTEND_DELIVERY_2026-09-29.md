# Fieldwork V5 前端交付记录

2026-09-30 增量：Reports 预览后会读取后端 Proof Capsule 作为导出预检。Proof 不可验证时保留可见预览但禁用导出并给出原因；Proof 可用时根据完整度标明待补草稿或审核材料包，导出时后端仍重新校验。隔离浏览器验收覆盖两种完整度与 Proof 失败路径。

2026-09-30 增量：Findings 的已验证详情新增证据定位、后端 Proof Capsule 状态和受控定向复测计划。复测要求选择同一研究项目、非最近证据的已有新 Run，并由后端再次校验；计划创建的是待验证 Candidate，不自动宣称修复或晋升结果。400/1440px 隔离数据浏览器路径覆盖证明包成功/失败及复测提交。

日期：2026-09-29

## CHANGELOG

- 将 V5 从 6 个聚合页扩展为完整的 17 个产品页面，并保持既定一级导航。
- 保留 Vanilla HTML/CSS/JavaScript、FastAPI 静态资源路径和现有后端接口契约。
- Campaigns、Tasks、Findings、AI Runtime、Tools 继续绑定真实 API；未接入能力显示明确不可用或空状态。
- 新增 Command Palette、对象 Inspector、导航记忆、主题记忆、分页、图谱缩放和移动端导航；不伪装已写入全局 Runtime 路由。
- 统一 Design Tokens、Typography、Surface、Panel、Table、Badge、Empty/Error、Skeleton、Drawer、Dialog 与响应式规则。
- Findings 只展示 verified 数据；Candidate 仅在 Campaign 注意区出现，不冒充 Canonical Result。
- API Key 不写入 HTML、localStorage、控制台、事件、证据或报告。
- 键盘操作：详情 Inspector 现在声明为模态对话区域，Tab/Shift+Tab 在抽屉内循环，Esc 关闭后焦点返回原控件；Command Palette 导航后焦点落在新页面主区域，当前导航带 `aria-current=page`。720px 浏览器交互验证通过。
- Settings 按 General、Appearance、Workspace、Privacy & Data、Security、Secrets、MCP/API、Storage、Backup、Updates 与默认折叠 Advanced 分组。可操作项仅连接已有主题、语言偏好和真实业务视图；缺失的存储、备份、更新与全局 Runtime 配置如实标记。1440/720/400px 分组、主题和跳转浏览器检查通过。
- 以 720/640/512/400 CSS 像素检查 17 个视图的窄视口布局（覆盖桌面 200% 缩放的等效宽度）；未见页面级横向溢出或脚本错误。此项是等效视口检查，尚不等于真实浏览器 200% 缩放或屏幕阅读器验收。
- Hypotheses 接入真实 Research Campaign 列表与详情，并提供显式创建 Campaign、记录假设的表单；这两步不启动扫描或验证。列表提供指标、搜索、状态筛选、分页和 Inspector。当前后端不提供独立 Confidence、Agent、Verifier 字段，因此显示“未提供”，不把 priority 冒充可信度；Challenge/Create Task/Verify 在缺少安全流程时禁用。真实后端 7 个项目的空态与 1440/720/400px 布局已验证；写入仅通过浏览器拦截模拟，未创建真实记录。
- Research Graph 的 Observed Assets 层改用真实 `/api/v1/engagements/{id}/asset-graph`，显示节点、持久化关系边、数量、搜索、高亮、节点来源/Scope Inspector 与缩放/Fit；不会把 Observation 解释为已测试或 Verified。真实后端有项目返回 4 节点/3 边，1440/800/720/400px 浏览器交互通过。Hypothesis→Evidence→Verification 完整关系、Attack Path 与 Evidence Graph 仍待独立 Graph Overlay。
- Sentinel 与 Incidents 的本机 AI Audit 子视图改用真实审计列表与详情，排除演示审计。Sentinel 呈现监控状态、最近 24 小时事件数、异常与数据源覆盖；缺少 Local AI/Cloud escalation 计数则标为未提供。Incidents 将 anomaly、调查 Candidate、Verified Finding 分开，统一 Incident Registry 明确未接入。真实后端停止中的监控在 1440/720/400px 浏览器检查通过，未启动或更改监控。
- Verification 新增按 Candidate 读取真实详情的复验轨迹：Evidence 引用、Verification Jobs、Attempts，以及 `machine_receipt` / `machine_negative_receipt` 记录数。它只显示回执摘要，不声称有效期、Artifact 或 Scope 校验已通过；全局 Receipt Registry 仍待接口。真实候选在 1440/720/400px 空尝试状态已验收；机器回执路径以浏览器拦截模拟验证，没有发起真实复验。
- Tools 修正 `available || ready` 的错误合并：Installed、Executable、Configured、Sandbox、Readiness 分列展示；已安装但 `ready=false` 不再标成 ready，读取失败也不再报成空 Registry。Extensions 只列真实 Capability Registry 中的工具适配器和 Domain 数；Research Packs、MCP、Runner Adapters、Physical Runner 以及更新/权限/drift 明确未接入，不伪装为已安装扩展。18 项真实能力在 1440/800/400px 通过浏览器检查，`available=true, ready=false` 用拦截数据验证。
- 全局阅读尺度收敛：表格正文提升到 12px，侧栏导航、数据行、辅助说明和 Panel 标题分层增大；保留高信息密度。当前 V5 已依照用户后续要求弃用旧青柠主色，改用冷石墨与克制蓝灰；这一段原先的青柠描述仅代表当时阶段。修正 Tasks 标题下方误称可管理 Priority/Lease/Retry 的文案。17 页在 1440/1024/800/720/400px 的布局与脚本检查通过，桌面 Tasks 截图已目视复核。
- 语言偏好从单纯保存升级为实际切换基础导航、页面标题与说明、主要对话框标题及 `html lang`，并在刷新后恢复；1440/800/400px 浏览器往返切换通过。动态业务文案、数据状态与完整英文界面仍未完成，切换提示明确说明此边界。
- Reports 预览现在直接显示后端返回的必填与建议材料缺口，区分可供人工审核的材料包和待补草稿包；Finding 或平台变化立即清除旧预览及导出目标，迟到的旧预览响应不会覆盖当前选择。模板、语言和脱敏级别仍无可配置接口，页面明确说明；未触发真实报告导出。
- Campaign 首页修正 API 失败时“0 项 / 暂无待办”的误导状态：Campaign、Task、Candidate、Verified 计数按各自接口区分真实零值和不可用；人工待办在部分数据失联时保留已取得条目并标为“不完整”，不能据此判断没有风险。Trust Boundary 的“待复核”统一显示 Candidate 数量，不再先计算无意义百分比再覆盖。模拟全失败在 400/800/1440px、部分失败在 1024px 浏览器检查通过。
- Agents 与 Runners 的真实数据视图由简略名称卡改为专用高密度表：Agent 展示 Role/Model、Runtime、Task、Context/Tokens/Cost、Risk；Runner 展示 Type/Environment、Capabilities、Health/Heartbeat、Queue/Active、CPU/RAM、Version。缺失字段明确显示“未提供”，Agent 不被当作执行节点，缺失心跳不推断 Runner 在线。当前 `/api/v1/orchestration/status` 实际为 404，生产页仍显示未接入；有数据路径在隔离浏览器 400/800/1440px 验证，无伪造生产记录。
- 全局连接告警按 HTTP 状态和数据源区分：规划中尚未接入的 Orchestrator、Verification Registry 404 留在对应页面说明，不再让全部页面常驻“部分数据不可用”；其他接口故障及这两项的意外 5xx 仍触发告警并列出具体名称。真实服务两个 404、隔离模拟 Task Center 503 和 Orchestrator 503 均经浏览器验证。
- 顶栏新增真实研究域切换：Traditional SRC、Web3 Security、AI Agent Security 使用后端 `engagements`、`task-center`、`findings` 的 `mode` 契约；当前域持久化并驱动关联 Campaign、Graph、Hypotheses、Run、Evidence、Verification、Findings、Reports 数据。切换立即清空旧域记录与选择，旧异步响应被 token 丢弃；“新建研究”默认当前域，若手动改域创建则切至新记录所属域。系统能力与本机审计仍为全局，不伪称被域过滤。三个真实域接口均返回 200；400/800/1440px 切换、刷新持久化、无溢出与脚本错误已验证。
- 对真实首页截图复核后确认最终生效色板已是冷色深灰/蓝灰，经典偏绿黑值仅在较早 CSS 文件中、会被 V5 工作流样式覆盖。本轮收敛首页信息层级：两列不再把所有 Panel 拉成同高，桌面右列加宽以减少长候选标题的碎行；人工待办底部增加直达任务队列和复核队列入口。400/800/1024/1280/1440px 布局检查通过，400/1440px 跳转检查通过。
- 修复桌面侧栏收起按钮原先只切换 class 却没有布局样式的死交互：宽屏现在真实切换 252px 导航与 72px 图标轨，状态持久化，按钮更新展开状态与名称；所有导航项设置完整可访问名称，窄屏自动轨和移动菜单保持可用。1440px 点击、导航、刷新恢复，400/800/1024px 导航与无溢出检查通过。当前无头 Chrome 的缩放快捷键未改变页面倍率，因此本轮不把等效视口测试冒充真实浏览器 200% zoom 验收。
- 移动导航补齐关闭按钮、遮罩、Esc、焦点返回与 Tab 焦点循环；打开时主内容暂不可交互，选中页面后焦点进入主区域，窗口扩大后自动清理移动遮罩/焦点限制。400px 浏览器键盘与触控流程、400→1024px resize 检查通过；未改变一级导航信息架构。
- 17 视图可见控件名称审计未发现无名称表单或按钮；Command Palette 则补齐对话框标题、combobox/listbox/option 语义与随方向键更新的活动选项，空结果提供状态提示，并加入中文检索别名。浏览器验证中文“任务”搜索、Enter 导航、方向键选中、Esc 焦点返回，无脚本错误。
- 新增可重复的只读 Chrome 验收脚本 `scripts/check_v5_frontend_ui.py`：覆盖 17 个视图 × 400/800/1024/1280/1440px × 深浅主题的 170 次可进入、标题、页面级溢出与脚本错误检查，并回归命令面板和移动导航。2026-09-30 当前本机服务运行时全项通过；真实 200% 浏览器缩放、屏幕阅读器和千行性能仍未验收。

## 页面完成矩阵

| 页面 | 可进入 | 当前数据状态 | 主要交互 |
| --- | --- | --- | --- |
| Campaigns | 是 | Engagements / Tasks / Findings 真实 API | Current Focus、Scope 确认、执行预检、分页、Inspector |
| Research Graph | 是 | 真实 Asset Graph 节点/边；完整研究语义关系待 Overlay | 项目选择、节点搜索、高亮、缩放、Fit、Inspector |
| Hypotheses | 是 | Research Campaign/Hypothesis 真实 API；当前数据为空 | 项目/Campaign 选择、创建表单、搜索筛选分页、Inspector；高风险动作禁用 |
| Agents | 是 | Orchestration 有数据则渲染，否则空态 | Tabs、Inspector |
| Tasks | 是 | Task Center 真实 API | 搜索、筛选、分页、详情、暂停/恢复/停止确认 |
| Runners | 是 | Orchestration 有数据则渲染，否则空态 | 卡片、Inspector |
| Tools | 是 | Capabilities 真实 API，独立区分可执行与就绪 | 状态表格、Inspector |
| Sentinel | 是 | 本机 AI Audit 真实状态与数据源覆盖；跨域 Sentinel 待接 | 审计选择、刷新、状态与覆盖检查 |
| Events | 是 | 所选 Run 的真实事件；统一事件流待接 | Run 选择、刷新、Inspector |
| Incidents | 是 | 本机 AI Audit 真实调查候选与 Verified；统一 Incident Registry 待接 | 审计选择、刷新、候选/结果 Inspector |
| Evidence | 是 | 所选 Run 的 Observation / Artifact / Coverage；统一 Ledger 待接 | Run 选择、刷新、Inspector |
| Verification | 是 | 真实 Candidate 队列与单候选 Job/Attempt/机器回执摘要；全局 Registry 待接 | 搜索、筛选、分页、Run 产物、候选复验轨迹 |
| Findings | 是 | 仅 verified 真实数据 | 分页、Inspector |
| Reports | 是 | Verified Finding 报告预览/导出 API | 预览、导出材料包 |
| AI Runtime | 是 | Readiness / Capabilities / Traditional Provider 真实 API | Provider 配置、Tools 跳转；全局路由禁用 |
| Extensions | 是 | 真实工具适配器快照；完整 Extension Registry 待接 | Domain/适配器只读检查、Inspector |
| Settings | 是 | 本机显示偏好 | 主题、语言偏好；高级设置待接 |

## 已知限制

- Research Graph、Hypotheses、Orchestrator、Sentinel、Incidents、Extensions 和统一 Event/Evidence/Verification Receipt Ledger 的完整 V5 Overlay 后端仍在开发；前端不会伪造记录。
- AI Runtime 的全局 Cloud/Local/Hybrid/Offline 路由接口尚未接入，相应按钮禁用；Traditional 工具 Provider 已使用真实后端 Secret 配置契约。
- 新建研究草稿、Scope 确认、执行计划、Run 控制与 Verified Finding 报告预览/导出已在 V5 接入真实 V1 API。完整安全设置和其余高级流程仍需逐项迁入。
- 中英文切换已保留入口和关键反馈；完整逐字段英文目录仍需后续 i18n catalog 接入。
- 已使用本机 Chrome 的 Playwright 自动化核对 720/800/1024/1280/1440px 的多个 V5 页面；完整 17 页 UAT、200% zoom 和屏幕阅读器验收仍未完成。

## 2026-09-29 续行：去除经典工作台入口

- V5 侧栏、Settings 与新建研究弹窗已去掉返回经典工作台的链接。
- 新建研究表单直接向 `/api/v1/engagements` 创建草稿，录入 Domain、目标、授权说明与预算；草稿不会自动确认或运行。
- Reports 页面直接使用 `/api/v1/findings/{id}/reports/{platform}/preview` 和 `/export`，只允许从 Verified 列表选择来源。
- 本轮已通过 3 项 V5 测试和 JS 语法检查；内置浏览器确认新版研究表单显示正常。完整产品前端整改仍在继续。

## 2026-09-29 续行：Campaign 授权与执行预检

- Campaign Inspector 增加 ScopeSnapshot 核对入口，展示后端返回的目标、工作域、允许/禁止动作、授权说明和预算。
- 人工勾选确认后才调用 `/api/v1/engagements/{id}/confirm`；创建草稿不会自动确认。
- 已确认项目可在 V5 内调用 `/execution-plan` 预检，显示工具就绪、阻塞项、覆盖提醒、预算和禁止动作；预检未通过时禁用启动。
- 实际启动仅在用户再次勾选确认后调用 `/start`，然后进入新版 Tasks 页面。
- 真实项目上只读验证执行计划；未触发启动。Chrome 1440/1280/1024/800/720 宽度检查 Scope 弹窗无横向溢出、无页面脚本错误。
- Tasks 已接入真实 `/task-center` 队列的搜索、状态筛选、20 条分页、阶段进度、队列位置与最近事件。运行中可暂停或停止、暂停可恢复或停止、排队可停止；所有操作均经确认弹窗调用对应 `/runs/{id}` 接口，详情读取真实 Run。未伪造 Agent、Runner、Lease、Priority、Budget 或 Retry。
- 隔离浏览器模拟数据验证按钮状态、确认弹窗与 800px 布局；未操作真实任务。3 项前端测试和 JS 语法通过。
- Events 接入所选真实 Run 的事件记录；Evidence 分开展示同一 Run 的 Observation、Artifact 与 Coverage，并明确这些运行产物不会自动成为已签名、独立或 Verified 证据。跨 Run 统一 Event/Evidence Ledger 与 Verification Receipt 仍待后端提供。
- 修复地址栏 hash 在同标签切换时视图不更新。真实后端在 720/800/1024/1280/1440px 检查 Events 和 Evidence：无横向溢出、无脚本错误；运行记录只读。
- Verification 现使用真实 `/findings` Candidate 集合构建人工复核队列：状态、搜索、20 条分页、证据引用数、下一步建议和对应 Run 产物入口；Verified 数量来自独立 Verified 集合，Receipt Registry 仍明确未接入。Findings API 错误时，Verification/Findings 显示错误态，报告选择器禁用，避免把读取失败误报成“没有结果”。
- 真实后端含 82 条 Candidate 的浏览器验收覆盖 720/800/1024/1280/1440px，验证分页、搜索空态及跨页 Run 选择；无脚本错误或横向溢出。未触发复验或修改后端事实。
- 清理静默/误导性交互：Agents 六个二级标签现在可切换，缺失数据源逐项说明；Graph 的 Attack Path/Evidence Graph 在关系 API 缺失时禁用；Findings 删除同义筛选器并显式标明 Verified only；Runtime 模式按钮禁用且说明后端尚无 RuntimeProfile 写入接口，不再把本地显示偏好伪装成真实路由。Command Palette 现支持 ↑/↓/Enter；语言按钮仅声明保存偏好，完整英文文案仍列为未完成。
- 浏览器核对 720/800/1024/1440px 的 Agent 标签、禁用项、命令面板与布局；800px 浅色主题、方向键选择和 Enter 导航通过。无脚本错误或页面横向溢出。
- 对照用户提供的完整子页参考 HTML 截图，将 V5 中性色从偏绿黑调整为冷色深灰/蓝灰，保留青柠作为稀缺强调；浅色主题同步调整为冷中性。首页增加真实 Current Focus 区域，展示当前 Campaign、Scope 状态、关联 Run 阶段与后端 next_action，并提供 Scope、执行计划和任务入口。没有复制参考稿中的演示指标或伪造图谱。
- 真实后端浏览器截图与交互检查覆盖 720/800/1024/1280/1440px，验证 Current Focus、长标题换行、深浅主题、动态“查看任务”入口；无页面横向溢出与脚本错误。JS 语法、3 项前端测试、diff 检查通过。
- AI Runtime 接入现有 `/api/v1/traditional/provider` 读写契约。状态区只展示配置状态、模型、API Base 与“密钥已保存”布尔值；折叠表单要求 URL、Model、重新输入的密钥和人工确认。密钥不回显、不写入 localStorage，提交结束即清空输入；后端仍负责 URL 校验与受限权限配置文件。全局 Cloud/Local/Hybrid/Offline 路由保持禁用，不将 Traditional Provider 误称为 RuntimeProfile。
- 真实后端 Provider GET 只读检查覆盖 720/800/1024/1440px；PUT 由浏览器拦截模拟，验证提交体、成功反馈、密钥输入清空与浏览器存储无密钥。未覆盖真实 Provider 配置。新增前端契约测试，4 项测试通过。
- 完成 17 页逐页导航巡检：1440px 深色和 720px 浅色下全部视图可进入、标题存在，无页面脚本错误或页面级横向溢出；720px 移动侧栏能正常打开并在选页后关闭。发现 760px 以下 Command Palette 顶栏入口原本隐藏，现增加窄屏“搜索”按钮；720/390/320px 触屏入口、命令搜索和跳页均通过。此项证明导航与布局，不等于所有 17 页业务功能已完成。
- 完整整改仍未达到交付门槛：完整 Domain 工作流、Runtime 配置、全量中英与各 Overlay 数据源仍需继续实施。

## 后端尚未接入字段

### Research Graph / Hypotheses

- node / edge relation、node kind、origin agent、open question、graph health、counterevidence coverage、verification coverage
- hypothesis statement、domain、status、confidence context、evidence count、counterevidence count、agent count、verifier、updated time

### Agents / Tasks / Runners

- agent identity、role、model、runtime、current task、context usage、tokens、cost、risk、permissions、memory、MCP、trace、incident history
- task objective、research group、agent、runner、priority、budget、lease、retry、checkpoint；现有 Run 阶段进度和暂停/恢复/停止已接入，尚非完整状态机
- runner type、environment、capabilities、heartbeat、queue、active jobs、CPU、RAM、last seen、version

### Sentinel / Events / Incidents

- sensor health、24h event count、anomalies、investigations、local AI / cloud escalation、incident correlation
- event timestamp、source、actor、action、resource、decision、risk、trace、incident
- incident severity、status、event/evidence/hypothesis counts、verifier、alternative explanations、resolution

### Evidence / Verification

- evidence type、source、artifact、supports / contradicts、SHA256、signature、trust、scope、run、created_at
- independent runner、oracle、attempts、artifacts、scope snapshot、counterevidence、environment、result、receipt、staleness
- 当前 Verification 页可查看真实 Candidate 复核队列，但上述独立复验字段仍无统一接口；不得以 Candidate 状态代替 Receipt

### Reports / Extensions

- report source result、type、platform、template、language、redaction、quality gates、limitations
- extension type、version、capabilities、permissions、trust、update status、capability drift、configuration schema
