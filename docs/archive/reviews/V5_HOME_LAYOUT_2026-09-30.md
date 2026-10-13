# 首页参考图实现

用户要求：首页实现提供的截图布局及布局内所有功能。

## 已落地

- 保留现有 Vanilla HTML/CSS/JS 与 17 页导航；新增独立 `v5-home.js`、`v5-home.css`。
- 首页结构：项目标题与操作、项目选择、工作区导航、六项指标、图谱/当前焦点、Agent/证据/发现三栏、Sentinel/Runner 两栏、Checkpoint 时间线、事件流/研究助手。
- 项目选择约束 Run、产物、发现与图谱的数据范围；异步响应带代次检查，旧项目响应不会覆盖新项目。
- 暂停/恢复/停止复用已有确认流程；恢复能力未知时禁用。复制复制研究信息，不复制授权。
- 图谱按真实节点与边绘制，支持适配、放大、节点 Inspector；没有伪造攻击或证据关系。
- 首页与 Graph 页面增加“研究关系”和“证据引用”视图；按 Campaign、Hypothesis、Candidate、Evidence 引用、Canonical Finding 的显式 ID 关系投影，节点保留来源和状态；攻击路径分析仍待专用接口。
- 当前 Run 的 Artifact、事件、Checkpoint 与下一步来自真实接口。
- 本地事实助手回答进度/下一步/证据/发现，标注来源 ID 与快照性质；不伪装成模型回答。
- 只读请求 15 秒超时，避免单个接口无限挂起首页。

## 未完成的截图能力

- Orchestrator 的 Agent 调度/关联与 Runner 心跳 Registry。
- 全量研究/攻击路径/证据 Graph Overlay。
- 当前研究图仅覆盖已有接口的显式关系，缺少完整观察、Claim、独立复验和路径语义。
- Sentinel 24 小时聚合指标与跨 Run 事件聚合。
- 带研究上下文和权限控制的模型助手接口。
- 首页完整中英文切换。

因此本次是布局与已有接口的交付，不代表“全部功能完成”。没有发起真实扫描、复验、复制授权或终止现存任务。

## 验证

- 首页已接现有 Run SSE：运行中订阅、重连去重、Run 隔离、隐藏/离页关闭、终态关闭；`scripts/check_v5_home_events.py` 隔离测试通过。任务和证据最终状态仍由刷新读取，不能把事件连接状态当作任务健康状态。
- `scripts/check_v5_research_graph.py` 验证真实 ID 关系、正反证据、Canonical 晋升与跨项目隔离。浏览器验证首页和 Graph 页的三个模式均可切换；当前本机项目研究关系只有根节点，符合真实空数据。

- `tests/test_v5_frontend.py`：22 passed。
- 深浅主题与 400/800/1024/1280/1440px：首页无横向页面溢出。
- `scripts/check_v5_home_ui.py`：首页区块、项目事实查询、Scope 对话框、节点 Inspector、任务导航。
