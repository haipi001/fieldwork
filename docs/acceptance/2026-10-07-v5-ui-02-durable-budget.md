# UI-02 持久调用预算增量（2026-10-07）

## 当前版本与范围

源码 0.68.16/build94/global schema26/V5 schema6；/Applications 安装包 metadata 仍 0.68.15/build93。安装包引用可变开发源码，不能将包 metadata 当作运行后端版本证明。本轮未替换安装包或提交 Git。

本轮完成既有内置本地 critic/synthesizer/evolver 的调用预算接线，以及 Profile 预算配置 UI。UI-01 research-worker 新队伍执行、云模型传输、真实模型质量、自动发现和整体 UI-02 尚未完成。

## 实现

- 新 runtime_calls 表唯一绑定 task/attempt 与 route decision；身份、预留值与截止时间不可修改，不可删除，状态转换有数据库 trigger。
- reserve 在 BEGIN IMMEDIATE 下核对当前租约/attempt/Provider 配置，原子计算已知用量及 reserved/calling/unknown 额度，限制 Profile 并发与每小时词元、任务跨重试总词元、任务/项目/组费用与组调用并发。持续任务同时检查每日费用策略。
- reserved→calling 先提交再传输；未发送的 reserved 失败可 released。已进入调用但没有可信用量返回时 unknown，暂停内置 Worker 任务，保留预留额度并阻断同任务重发。
- 成功取得输出与用量后，Runtime usage 与 call settled 在同一事务提交。语义拒绝仍保留该次用量；同一 report 幂等，已结算调用不能再次 start。
- 过期 calling 转 unknown；过期 reserved 转 released。未知调用的词元/费用不随小时窗口自动释放。已知用量按滚动一小时计算。
- 模型不返回 usage 字段不再假定零用量。预算不足/未知用量在任务错误码中可见。
- Profile UI 新增调用并发、每小时词元、单次时限；只读 Profile 表也显示实际值。调用页显示最新50条状态、预留值、截止时间与 usage id，错误与空态有区别，中英切换重绘。用量摘要标为“已结算调用”，避免未知请求被显示为零次实际发送。

## 验收

1. python3 -m pytest -q：785 passed, 3 skipped，314.85秒。
2. 最后追加云费用并发预算契约测试及最终 UI 标签后，runtime_calls/schema/workers/runtime/frontend 专项57 passed；没有把未重跑的全量数字推算成786。
3. 浏览器 check_v5_runtime_control_ui.py 通过；真实 API 保存预算2/64000/20000，刷新与语言、失败态、8种显示组合，无 JS error。只有本机健康检查1次，模型请求0次。
4. check_v5_team_ui.py 通过，1/2/8任务队列、过期预览、错误/恢复/组控制、8种显示组合，无 JS error。没有自动模型/目标执行。
5. 隔离原生 FieldworkUI02Budget.app 实际保存 Native durable budget profile，退出/重启后 Profile 行显示并发2、小时词元64000、截止20000ms。中英切换显示一致。发现 WKWebView 缓存旧脚本后给主脚本添加 build 缓存版本0.68.16.94，最终重启取得正确预算列证据。
6. 并发 reserve 只允许一个调用占用单并发 Profile；跨重试剩余额度只剩5词元；未知调用占用小时额度并阻断重试；云费用纯契约 fixture 并发60+60不能突破90µ项目上限，未发云请求。
7. 生命周期真实慢响应/取消/SIGKILL 证据仍在相关测试与上一份监督器验收中；预算恢复测试证明 calling 不被释放为零成本。
8. 隔离原生数据库25→26启动迁移及正常重启通过，integrity ok，旧Provider/Profile/队伍8任务原样保留。
9. git diff --check 与 JS syntax 检查通过。

## 主数据库迁移核对

本轮结束读取主 data/src_control.db 发现 user_version=26；自动备份为 data/backups/20261007T074812Z-pre-schema-25-to-26.db/.json。确切触发启动进程尚未定位，不能断言是单纯 import（迁移位于 lifespan）。已只读核对备份/当前102张原有表；仅 app_metadata 的 app_version/schema_version 和 v5_schema_meta 版本发生变化，所有原有业务行均仍存在，当前/备份 integrity_check 都为ok。runtime_calls 当前0条。8000端口检查未发现监听。不能继续声称主数据库未改变。

## 迁移与回滚

新增表及 trigger，未改写历史业务材料或回执。Schema25→26备份恢复专项验证升级后旧记录保持原样，备份恢复后user_version25且没有runtime_calls。

实际回滚顺序：停止相关实例，保留当前数据库副本，回退对应Schema25代码，然后从上述迁移前备份恢复。恢复后核对integrity与历史行，使用匹配Schema25版本启动。Schema26源码启动会再次升级。回退源码需按本轮文件差异操作，保留此前UI-01/UI-02开发成果，不做盲目整仓reset。

## 剩余与下一包

- 给 research-worker 队伍接实际角色输出、证据引用和研究执行 UI；不能把现有结构化 worker 当作新队伍已经运行。
- 云端实际传输/失败回退/费用精确结算与持续升级尚待接线和实际模型证明；云预算契约测试不是云调用验收。
- 未知用量的人工核对与凭证化结算 UI、逐调用完整分页/筛选、任务/组所有视图统一累计用量仍待补。
- 调用时长目前每次调用上限，任务所有重试累计运行时长约束还需完善；外部Runner尚未强制使用调用预留契约。
- 安装版发布与主生产活动任务恢复、三域质量/发现闭环/规模门仍未完成。

保持原UI-00→QA-07与15 Task/8 Gaps范围，下一包继续UI-02并联验UI-01 research-worker。
