# UI-01 / UI-02 研究队伍 Worker 执行增量（2026-10-07）

## 交付范围

新增 v5_research_worker.py，研究组可显式启动本机模型分析固定图谱输入。它接通此前 queue-only 队伍的实际调用路径；并非工具自动发现、云执行、独立复验或模型质量完成证明。

## 实现

- preview/commit 固定最多20个已有 observation/evidence/counterevidence/claim 的节点摘要哈希，反证优先；创建仅排队，之后才显式启动。模型返回草稿不得作为 source 输入扩散成自证。
- 新队伍可配置每任务总词元与单次调用时限，包含在 preview hash/任务预算中；角色 researcher/explorer/specialist 分别进入模型角色提示。旧队伍第一次显式启动时固定输入材料，没有自动启动旧队伍。
- POST /api/v1/workers/research/groups/{id}/start 返回202，后台持久租约任务按组/Profile/Runner并发约束执行。相同实例重复启动返回 already_running；跨实例启动标记与任务租约/调用预留阻断重放。进程重启不会自动恢复模型发送，需显式启动并通过授权/输入/未知用量检查。
- 每个真实调用经过当前 Scope/Policy/Run、输入哈希、Provider快照及 durable call 预算；结果引用必须来自所给 observation/evidence，所有给定反证必须保留。未知用量暂停，不自动重试；语义拒绝也保留真实用量。
- 只生成 draft claim 或非证据 open_question，写入 task/group/role/decision/call/Scope/Policy/input hashes 来源。摘要、草稿数量与待研究问题数显示在 Agent 任务行，可查看记录及引用。generic complete API 禁止绕过团队结果校验。
- 组暂停现在撤销正在执行的租约，模型监督器检测到暂停后中断连接；组取消原有中断语义保留。应用退出停止研究线程/调用，并将本实例节点标为 offline。
- 先验证脱敏模型 context <=32KB，再预留/开始调用，超限材料不制造“已经发送”的调用。并发测试暴露零等待 SQLite 读锁导致正常任务中断，调整租约检查 busy_timeout 为有界50ms；仍由墙钟监督器限制模型传输。
- 前端增加“分析已有图谱”显式确认、每任务预算字段、启动错误与草稿状态。已完整读取并确定无可运行任务的组禁用启动按钮。刷新同时读取真实 Runner 注册状态；标为已登记在线研究节点，历史/异常退出存活探测仍是后续门。

## 验收证据

- 全量 python3 -m pytest -q：790 passed, 3 skipped，242.71秒；这是最后小改动之前的全量结果。
- 材料预检、来源字段、退出/错误显示最终变更后的相关专项65 passed。包含真实 HTTP 模型协议 fixture：8任务（4研究员/2探索员/2专题员）、Profile并发2、实际峰值2、8次返回/结算、8 draft claim与问题；没有 canonical result、正式Finding或 verification receipt。
- 伪造引用/遗漏反证输出被拒绝，已用词元保留；输入修改阻断再启动。没有本机服务时保持 queued/attempt0。大材料不建立调用预留。组暂停实际慢响应，服务观测连接断开、任务暂停、调用 unknown、不写研究结果。
- Chrome check_v5_team_ui.py 最终通过：1/2/8队列、过期预览、语言保留、错误/控制/刷新、8中英明暗宽窄组合，无 JS errors。此脚本没有调用模型，不作为实际执行证据。
- 隔离原生 FieldworkResearchWorker.app，通过界面点击并确认“分析已有图谱”，真实后端执行原有8 queued任务。各任务保存1问题/0假设、17输入/12输出词元，合计136/96，8个 settled调用，正式Finding0。
- 原生退出/重启后8个草稿与用量保留、启动按钮禁用；测试 HTTP 服务 model count仍8，没有重放。服务使用预设协议响应，明确标为 Protocol fixture：这是实际 HTTP/进程/UI/账本接线证明，不是模型推理质量或自动发现证明。隔离测试 Run本身为暂停的人工fixture，显式组分析不会恢复旧目标Run/发送目标请求。
- 原生fixture服务已停止、Provider禁用、测试实例退出；fixture registry 旧实例offline清理属于测试清理，不作为一般异常进程存活探测证明。
- git diff --check、JS syntax 通过。

## 迁移、发布与回滚

本轮无新增DDL，仍源码0.68.16/build94/schema26。新增模块与router、已有JSON字段和队伍UI；/Applications安装包未替换，Git未提交。主Schema26迁移/备份情况见前一份durable-budget验收。

回滚本轮研究模块/router、团队输入/预算/启动UI与本轮组暂停差异；保留前包预算与结构化 Worker。先停止研究实例并保存当前DB。已生成草稿不是正式Finding，不删除历史记录来冒充回滚。旧Schema25备份恢复需要对应Schema25代码，不能仅回退DB。

## 剩余与下一包

1. 云模型实际调用、凭据/公网端点约束、模型故障回退和精确费用；本轮本机HTTP协议fixture不能替代实际模型质量。
2. 实际工具自动发现和增量 observation→多角色分诊→隔离独立复验→正式结果/报告；不能用预置候选/协议答案做通过证据。
3. 图谱输入选择/较大材料分块、团队间结果交换和协调去重；当前固定最多20节点，页面应保留支持范围说明。
4. Provider输入词元精确预检、累计重试总时长、未知用量凭证化核对、外部Runner使用预算契约的强制门；目前返回合计超额会被拒绝，但不能据此宣称服务端从未消耗超过估计值。
5. 一般异常退出的Runner存活探测、执行启动标记租约/崩溃故障注入和完整安装版验收。
6. 原15 Task/8 Gaps、UI-03至QA-07保持全部范围，整体目标未完成。
