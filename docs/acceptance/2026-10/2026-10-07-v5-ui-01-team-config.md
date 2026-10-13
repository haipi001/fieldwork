# UI-01：研究组配置与真实任务清单增量

源码基线为 0.68.15/build93/schema25，开发分支 `codex/src-deepening-20260924`。本增量未提交，完整 V5 目标继续进行。没有新增数据库表或修改历史回执。

## 已实现

- 智能体清单按研究项目、Research Campaign、真实 Run 读取研究组及分页 AgentTask。提供创建入口、角色数量、同时领取上限、成本预算和已有模型 Profile 的选择。
- 创建分为只读预览与显式确认。后端校验项目已确认 Scope、当前 Policy、Campaign 状态、Run 归属/状态及非演示标记；逻辑任务总数 1–100，并发 1–32 且不超过任务数。
- 组与任务在同一个 SQLite 写事务中创建；预览哈希绑定当前授权和配置，重复提交幂等，改变计划后复用同一幂等键被拒绝。任务额度分配的总和等于组额度。
- 任务只进入持久队列，要求 `research-worker` 路线，没有目标工具授权。领取及持有租约后的写入继续检查当前 Scope、Policy、项目和 Run。暂停/恢复/取消复用真实组 API。
- 显示组预算、任务费用/词元/运行时间、租约持有者与到期、尝试次数和失败信息。任务分页此前遗漏用量，现按本页任务读取真实账本；零值不再覆盖已有费用。
- 配置修改会使预览失效；晚到的旧预览不会启用创建按钮。语言切换保留表单值并更新预览文案。错误读取与无记录使用不同状态。执行节点页保持独立渲染。

## 自动与浏览器证据

- `python3 -m pytest -q tests/test_v5_team_plan.py tests/test_v5_orchestration.py tests/test_v5_frontend.py`：39 passed。
- `python3 -m pytest -q`：769 passed，3 skipped，283.09 秒；既有 Starlette 弃用警告。
- `node --check static/v5-team.js`、`node --check static/v5.js`、`node --check static/v5-locale.js` 与 `git diff --check` 通过。
- `scripts/check_v5_team_ui.py` 使用仓库 `.venv` 中的 Playwright 与已安装 Chrome，真实前端连接临时数据库上的真实进程内 API。实际创建 1/2/8 个任务的三个组，共 11 个任务；演练晚到预览、语言切换保留输入、读取失败、暂停/恢复/取消、页面重载和执行节点页。
- 中英 × 明暗 × 1440/680 宽度共八种组合没有页面横向溢出或 JavaScript 错误。浅色新按钮对比和任务表窄窗溢出在检查中修复。截图在忽略的 `build/acceptance/v5-team-ui/`。
- 授权项目、Campaign 和暂停 Run 均为明确 fixture。没有模型调用或目标请求；上述结果证明配置、事务、清单和操作链，不证明漏洞自动发现。

## 原生 App 直接证据

用独立源码副本、独立标识与动态 localhost 端口构建并 ad-hoc 签名，使用临时 fixture 数据，没有覆盖 `/Applications/Fieldwork.app` 或复制生产数据库。

实际 WKWebView 显示“工具就绪”，智能体页空组有“创建研究组”按钮。原生输入研究员数量 8、同时领取上限 2，读取当前 Scope/Policy/Run 预览后勾选确认。创建后 AX 显示研究组 1、已加载逻辑任务 8、执行节点 0；组卡预算 1000000 µ，各任务额度 125000 µ，八条任务均为排队中且未领取。独立查询 fixture 数据库确认 1 组、8 任务。

初次将隔离源码放在 Documents 下时，Python 在解释器路径初始化读取中阻塞，没有进入后端。移动到系统临时目录后启动成功，没有改变系统权限。原生自动化还遇到重复 bundle identifier 的窗口绑定及 ScreenCaptureKit 瞬时错误；最终独立标识下读取并操作了真实窗口。该启动差异保留为环境限制，未据此宣称任意机器启动完成。

正常退出原生 App 后，后端进程日志记录 shutdown complete，直接 socket 检查端口关闭；数据库仍为 1 组/8 任务。重新启动同一临时 App 后，实际 WKWebView 自动回到智能体页，AX 再次显示同一研究组 ID、8 条排队任务、并发上限 2 与各任务预算。没有重新创建队伍或重发研究请求。

## 迁移、回滚和剩余工作

无 schema 迁移；幂等标记使用现有 app_metadata，任务和研究组使用现有表。历史 Finding/Receipt 不改写。

回滚界面时保留后端当前授权守卫；如需回滚后端，应先通过当前组取消 API 停止本增量创建的队伍，再回滚代码。生产数据未写入，本次隔离 fixture 可保留供重放。

UI-01 整包仍待当前安装版操作验收、当前模型 Profile 的实际执行路线及通用研究 Worker 接通。创建队伍不代表模型已经推理。后续 UI-02 补 V5 Provider/Profile 写入、健康与用量、实际任务路由和持续研究策略；UI-03 接真实发现到独立复验的闭环。Agent 安全/能力/记忆/MCP/攻击测试、其余 UI 包及 QA-07 保留在完整目标内。
