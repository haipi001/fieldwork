# V5-SCALE-01：任务列表分页与演化页可达性

版本：0.64.0 / Build 73 / Schema 24。

`GET /api/v1/orchestration/tasks` 保留原有 `items`，新增 `page.has_more`、`page.next_cursor` 与 `page.limit`。游标绑定 Campaign 与 status 条件，按 `priority DESC, created_at, id` 排序逐页读取；非法游标或跨条件复用返回 422。游标是定位令牌，不是授权凭据。分页不提供跨请求数据库快照：新插入且排在游标之前的任务需要刷新首屏才能看到。

Evolution 控制面先取 500 项，按需显式继续加载。未读完时显示已读数量，并关闭下一代汇集入口，避免把部分任务误判为全部成功；再次刷新会从第一页读取。浏览器演练构造两页共 1000 条无关任务、第三页为目标 Evolver 任务，确认其状态可见，刷新和操作后仍可继续加载。

验证：`tests/test_v5_task_pagination.py` 构造 1001 条任务，覆盖排序、无重复、条件绑定、非法游标及分页中插入高优先级任务；`scripts/check_v5_evolution_ui.py` 使用拦截的浏览器数据完成 1001 项演练。测试不涉及真实目标或实际漏洞发现，也不是并发/延迟压测。

无 Schema 迁移，不改写用户数据。仍需图谱及其他队列页面的完整分页可达性、100→1000 任务并发/恢复/预算指标和真实 Provider 长时验证。已安装应用未更新，本条仅覆盖工作区与未安装构建。

终验：全量 651 passed、3 skipped；0.64.0 Build73 未安装 App 构建、deep/strict 签名与语法/差异检查通过。
