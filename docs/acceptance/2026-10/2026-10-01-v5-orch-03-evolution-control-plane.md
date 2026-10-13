# V5-ORCH-03 Evolution 控制面进展

日期：2026-10-01
版本：Fieldwork 0.61.0（Build 70 / Schema 24）

V5 新增独立 Evolution 视图，按当前授权域的 Engagement、Campaign、Research Group 读取持久化的研究群体、代际排名、保留选择、父代谱系、Evolver 任务和跨组传递记录；页面不内置示例评分。当前性直接采用服务端 `current` 判定，过期代不可在页面推进。启发式 score 明确标为研究调度信号，不能作为漏洞成立概率或独立复验结论。

研究员可显式选择同组 Claim 建立或重新播种群体，也可选择来源组 Claim 创建精简的跨组 Context Capsule 与目标组 Specialist 任务。`advance`、本地 Worker `tick` 和 `collect` 均经确认弹窗；本地 tick 通过 `population_id` 限定本代 Evolver 队列，不消费其他结构化任务。服务端原有 Scope/Policy、租约、预算、父代与结构化输出合同仍是最终裁决；前端禁用状态不替代后端验证。任何模型输出都只成为未验证 `draft`，不会自动晋升 Canonical Result 或 Finding。

验证：全量 `python3 -m pytest -q` 为 635 passed、3 skipped（1 条第三方弃用警告）。隔离临时数据库服务上的浏览器网络桩验收覆盖真实视图映射、旧代禁止推进、操作确认、定向 tick、播种、跨组传递、无 JS 异常及 390px/1280px 水平布局；V5 全视图浏览器验收为 18 个视图、6 种宽度、2 种主题，共 216 项视图检查，并通过隔离的草稿执行流程、Inspector、任务及 Finding 控制检查。测试桩拦截 Evolution 写请求，未触及正式数据库或外部目标。JS/Python 语法、`git diff --check`、未安装 bundle 构建和 deep/strict 签名校验通过。

边界：页面展示最多 500 条代际、传递和任务记录及图谱前 1000 节点，超限时需 API 操作；本地 Provider 仍由用户配置。跨多轮、预算耗尽、进程重启后的持久恢复与真实 Provider 长时间运行未完成整体验收，`V5-ORCH-03` 仍是进行中。
