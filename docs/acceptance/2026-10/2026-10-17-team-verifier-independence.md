# 研究组生产者与复验独立性


## 2026-10-17 研究组产出与独立复验的生产者绑定

检查发现team_research草稿仅task_id，复验入口依赖producer_task_id，存在同组独立性检查缺口。新草稿增加producer_task_id/producer_runner_ref；复验对新旧team_research都从真实任务结果claim_ids/精确source_ref/项目/Run与runtime_calls任务调用账本解析生产者，拒绝不一致属性。历史缺少producer字段但真实谱系完整可兼容，不伪造历史runner身份。请求记录保存真实producer，已有同组和同runner拒绝门因此生效。

进入复验需冻结20以内输入仍存在、未改、未归档，全部草稿支持证据及冻结输入中的反证必须携带；修改草稿counterevidence_ids不能丢弃冻结反证。拒绝时不新增复验任务/请求。一般非team claim行为保留。未自动构造可执行复验契约、未执行目标请求、未产生正式发现。

29 passed、1既有warning，build/team-verifier-boundary-final-tests.log；包含新/历史同组拒绝、独立请求保存生产者、遗漏反证/改草稿反证列表/变化或删除材料/伪造任务或runner来源拒绝，既有草稿与复验/研究worker回归。首轮使用不同模块的节点哈希导致有效材料误判，修正为实际research输入摘要算法后通过，失败日志保留。diff检查通过。无schema迁移/UI页面删除或凭据变更。

主库0活动Run/执行任务/模型调用/活动监控后正常退出、确认端口关闭、启动来源链接App加载修复。未向主库新增研究或复验任务。完整UI-00到QA-07及15Task/8Gaps继续未完成；下步可执行复验契约的用户交接及真实自动发现/三域闭环仍需推进。
