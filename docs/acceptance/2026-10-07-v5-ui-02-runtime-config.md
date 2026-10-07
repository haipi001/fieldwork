# UI-02：V5 模型配置、路由检查与持续研究策略版本

源码基线 0.68.15/build93/schema25，开发分支 `codex/src-deepening-20260924`；包含前一 UI-01 未提交增量。本文件记录 UI-02 的配置增量，不认定 UI-02 整包或可靠自动发现目标已完成。

## 用户可执行操作

模型与路由页新增 V5 Provider 保存、显式连接检查、启用/禁用；支持本机 OpenAI-compatible、Ollama、llama.cpp 与公共 HTTPS 云接口。保存配置不会发 Provider 请求，健康检查只在用户确认后连接模型服务。Key 只提交本机后端；保存后输入框清空，没有浏览器存储或回显。

用户可创建 Local/Cloud/Hybrid/Offline Profile，选择本机/云端/独立路线的 Provider，设置单次词元、累计费用、复杂度阈值与回退。每次创建产生不可变版本，已有研究组保持原引用。原四个禁用的“全局模式”按钮移除；页面明确队伍按 Profile 路由，Traditional 工具 Provider 是独立旧通道。

可显式检查并记录路由决策，看到 Profile、模型、路线、阻断原因、预算与时间；这不会调用模型或增加用量次数。路由账本分页读取真实记录。

可选择授权项目/Campaign，保存持续研究的启停、最小间隔、空闲触发、每日费用与每 tick 新任务上限。页面展示版本、配置 hash、Scope/Policy 绑定、下次检查与版本历史；显式 tick 按现有预算/间隔处理图谱变化并入队。

## 后端约束

- 新 Profile 保存所选 Provider 的模型/接口/元数据摘要。界面提交其刚读取的摘要，后端拒绝过时配置。Provider 模型变化后健康状态重置为 unknown；旧 Profile 的新路由不能默默使用改变后的 Provider。
- 新 Profile 的空 Provider 清单表示没有选择该路线，不能自动吸收后来注册的 Provider。历史 Profile 不回填摘要；API 标记旧版本未绑定 Provider 快照，页面选择器显示该限制。
- Local/Offline 的独立模型路线也受本机限制，修复了独立分支可能选择云模型的问题。敏感上下文仍强制本地，没有健康兼容 Provider 时返回 blocked。
- 组队任务的路由检查现在也验证当前项目、Scope、Policy 和 Run。原 Group Profile 不能被调用方覆盖。
- 持续策略向现有 v5_events 追加版本历史及授权绑定，重复保存相同配置不会增加版本。策略或授权绑定变化会取消旧持续研究任务，并同步 Runner 活跃数；新任务 capsule 带策略 hash。此事件表没有新增数据库级不可变触发器。
- 授权 Scope/Policy 变化后，旧持续配置的 tick 被拒绝，必须在当前授权下重新保存。旧策略、历史 Profile 和旧 Receipt 均不自动改写或晋升。

## 验证

- 模型/持续研究/Worker/前端相关专项：46 passed。
- 全量回归：772 passed，3 skipped，422.82 秒，既有 Starlette 弃用警告。
- 全量启动后最后补充 Profile 视图的旧版绑定标记及界面提示；该最终增量重新运行 Runtime/Continuous/Frontend 专项，38 passed。最后 CSS appearance 修正由后续浏览器检查覆盖。
- JS 语法检查与 `git diff --check` 通过。
- UI-01 跨页回归发现研究组读取中已提前启用创建入口；现等待组/任务读取成功后启用。最终 `check_v5_team_ui.py` 的创建、控制、刷新及八种显示组合再次通过。
- `scripts/check_v5_runtime_control_ui.py`：真实前端连接临时数据库的真实进程内 API，显式保存 1 Provider、1 Offline Profile、两版持续策略。真实本地 HTTP 健康靶场收到 1 次 GET，模型 POST 为 0；检查独立本地路由且用量调用数为 0。
- 浏览器演练策略修改取消旧任务、图谱增量入队、配置读取失败、语言切换保留未保存输入、刷新重载与 Key 清空；中英×明暗×1440/680 共八种组合，无 JS 错误或横向页面溢出。截图位于忽略的 `build/acceptance/v5-runtime-ui/`。

## 原生 App

临时源码与独立标识构建的实际 WKWebView 中，新增本机 Provider `http://127.0.0.1:1/v1`，没有 Key；该地址为明确不可用的测试端点。保存后状态 unknown，显式确认连接检查后为 unavailable。创建绑定该 Provider 的 Local Profile，单次词元 32768、费用上限 1000000 µ；路由检查返回 `blocked / local_mode_no_healthy_local_provider`，没有伪造模型健康或执行调用。

在原生页面为 fixture Campaign 保存关闭状态的持续策略，版本 1、每日费用 1000000 µ，显示配置 hash 与当前 Scope/Policy。正常退出后直接 socket 确认后端端口关闭；数据库保留 1 Provider、1 Profile、1 blocked 决策、1 策略版本及零 runtime_usage 调用。重启后实际页面再次显示同一 Profile、Provider 不可用状态、blocked 路由，以及相同策略 hash 和预算。原 UI-01 的 8 个排队任务仍保留。

没有覆盖 `/Applications/Fieldwork.app` 或写入生产数据库。原生页面读取了既有 Traditional Provider 的脱敏配置状态，但本次没有向其云模型发请求。此验证只证明配置、健康失败、路由阻断与持久化。

## 迁移、回滚与下一增量

无 schema 迁移；新摘要存入现有 Profile config_json，策略版本存入既有事件表，历史不可变 Profile/回执不回填。回滚界面可保留后端守卫。回滚新后端前先关闭持续研究并取消本增量队伍；新 Profile 字段须由新版本继续读取，不能让旧路由器忽略配置摘要后重新派发。

UI-02 仍需：实际研究 Worker 使用所选 Profile 并把真实调用写入路由/用量账本；组内与持续研究并发、每小时词元、运行时长预算的执行预检/预留与完成结算；持续研究云升级和故障回退的实际执行；安装版完整操作验收。UI-03 的真实发现到独立复验、其它 UI 包、三域与 QA-07 发布门仍在完整目标中。
