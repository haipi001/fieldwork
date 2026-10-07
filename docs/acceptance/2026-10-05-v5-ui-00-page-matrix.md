# UI-00：18 页真实状态与操作入口矩阵

基线：2026-10-05 源码 0.68.15/build93/schema25；已安装版同版本。源码导航与数据读取见 `templates/v5.html`、`static/v5.js`、`static/v5-home.js` 及各 V5 子模块。此表描述实际接线，不把空数据当接口失效，也不把原型展示数字当运行事实。

| 页面 | 实际数据源 / 已有操作 | 当前空态、占位或缺口 | 本轮优先级 |
| --- | --- | --- | --- |
| 研究项目 Campaigns | Engagement、Run、Candidate、Finding 及原生新建/授权/执行计划 | 首页 Agent 总数是全局 AgentTask，不是所选项目；没有组队入口 | P0 |
| 研究图谱 Graph | 所选项目资产、研究关系、证据引用 | 攻击路径按钮禁用，缺专用分析接口 | P1 |
| 研究假设 Hypotheses | 项目内 Campaign、假设创建及列表 | 需先选择项目/Campaign；此禁用条件不是缺口 | 已接 |
| 智能体 Agents | Orchestrator 状态快照中的研究组、逻辑 AgentTask、Runner | 无任务时只见 Inventory 空态；无法创建组队；Security/Capabilities/Memory/MCP/Attack 标签只有未接入说明 | P0 |
| 研究演化 Evolution | Campaign/Group 群体、传递、代际任务与显式确认操作 | 仅操作已存在研究组；不能配置通用队伍规模 | P2 |
| 运行队列 Tasks | Task Center 的 Run、状态、事件及暂停/恢复/停止 | Priority/Lease/Retry 的 Run 操作未接；与 AgentTask 队列分开 | P0 |
| 执行节点 Runners | Orchestrator Runner 注册快照 | 无完整注册、权限、能力及生命周期管理界面 | P2 |
| 工具能力 Tools | Capability Registry 与本机探测 | 工具就绪不代表有目标执行授权或有效结果 | 已接 |
| 安全监控 Sentinel | 本机监控启停/采集、审计历史 | 退出 App 后常驻和自动独立复验尚未接通 | P1 |
| 运行事件 Events | 所选 Run 的事件 | 跨系统统一事件流未接 | P1 |
| 调查线索 Incidents | 本机 AI Audit 异常、调查候选及可信结果 | 尚无跨域统一调查工作台 | P1 |
| 证据 Evidence | 所选 Run 的产物和证据详情 | 范围局限所选 Run；需补跨域关系跳转 | P1 |
| 独立复验 Verification | Candidate、复验轨迹、回执及限定 HTTP 受控复验 | 其它漏洞类型的专用验证器及完整自动发现入口仍缺 | P0 |
| 确认发现 Findings | Verified 结果、生命周期、证据和报告跳转 | 仅显示有后端当前证明的结果；无结果是正常空态 | 已接 |
| 报告 Reports | 已验证结果预览、证明包与材料导出 | 模板、报告语言、脱敏级别不可配置 | P1 |
| 模型与路由 Runtime | readiness、V5 Provider/Profile/用量读取、Traditional Provider 配置 | 全局模式按钮禁用；V5 Provider、Profile、任务路由和持续研究预算没有完整写入界面 | P0 |
| 扩展 Extensions | Capability Registry 的工具适配器快照 | Research Pack、MCP、Runner Adapter 与物理 Runner 管理未接 | P2 |
| 设置 Settings | 语言/主题等页面偏好 | 数据保留/导出、存储迁移、备份恢复状态、更新检查、高级权限写入未接 | P2 |

## 统一空态规则

1. 后端连接失败：显示接口错误和重试；不能写成“没有记录”。
2. 已连接但未配置：显示配置入口或明确的前置条件。
3. 已配置但没有记录：显示真实零值和可执行的下一步，不填演示任务。
4. 能力未接入：标明未接入；不启用没有后端门禁的按钮。
5. 列表有上限/分页：显示已加载数量与是否仍有后续页，不把已加载数当全库总数。

## UI-01 前置事实

- `/api/v1/orchestration/status` 当前最多读取 500 个全局 AgentTask 与 500 个研究组；不能用它证明某一项目或全部历史任务的完整数量。
- 既有 `POST /api/v1/orchestration/groups` 与 `POST /api/v1/orchestration/tasks` 是分开的；页面不能靠连续请求冒充原子组队创建，失败会留下半成品。
- Built-in structured worker 只处理带专用结构化合同的 critic/synthesizer/evolver。通用 researcher/ explorer 任务即使入队，也不能据此宣称模型已在研究。
- UI-01 应先补当前授权、Run/Scope/Policy 绑定与原子幂等创建；Agent 页按选定项目/Campaign 读取真实组与分页任务，并明确显示“等待执行节点”状态。
