# Master Bundle完整范围审计（2026-10-04）

依据用户指定目录中的TASKS.json、DEVELOPMENT_FLOW.md、KNOWN_GAPS.md、MIGRATION_SECURITY.md。包内容完整性审计证明交接文件未丢失，不证明当前产品完成。当前源代码、相关验收文件和新沙箱实验已核对；没有以历史绿测试代替每项当前端到端验收。

## 全部命名Task与原done条款

| Task | 阶段 | 原验收条款 | 当前实现入口/证据 | 本次审计判定 |
|---|---|---|---|---|
| V5-SEC-01 | P0 | API/session/Host/Origin plan reconciled；untrusted execution boundary documented；no regression | session_auth.py、local_boundary.py、isolated_execution.py及桌面握手；SEC-01/SEC-02验收 | 存在实现和局部验收；本次未逐条重跑完整验收，整体完成未证明 |
| V5-BE-01 | P0 | migration additive；backup/rollback tested；no auto-promotion | v5_schema.py；additive-schema验收 | 存在实现和局部验收；本次未逐条重跑完整验收，整体完成未证明 |
| V5-BE-02 | P0 | nodes/edges CRUD；provenance bridge；canonical receipt guard | v5_graph.py；research-graph验收 | 存在实现和局部验收；本次未逐条重跑完整验收，整体完成未证明 |
| V5-BE-03 | P0 | persistent lease；priority；pause/cancel；crash recovery；idempotency；budget | v5_orchestration.py；orchestration验收 | 存在实现和局部验收；本次未逐条重跑完整验收，整体完成未证明 |
| V5-BE-04 | P0 | cloud/local/hybrid/offline；sensitive local；fallback；usage accounting | v5_runtime.py；runtime-router验收 | 存在实现和局部验收；本次未逐条重跑完整验收，整体完成未证明 |
| V5-BE-05 | P0 | receipt hash；independent runner/context metadata；promotion guard | v5_verification.py；verification-receipts及HTTP回执验收 | 存在实现和局部验收；本次未逐条重跑完整验收，整体完成未证明 |
| V5-FE-01 | P0 | new nav；agent groups；runtime status；zh/en；dark/light | templates/v5.html、static/v5*.js；控制面/语言主题/HTTP工作流UI检查 | 存在实现和局部验收；本次未逐条重跑完整验收，整体完成未证明 |
| V5-DOM-01 | P1 | existing tools emit core observations；no scanner auto-promotion | Traditional桥接；traditional-adapter验收 | 存在实现和局部验收；本次未逐条重跑完整验收，整体完成未证明 |
| V5-DOM-02 | P1 | AST/Forge evidence mapped；deployment context preserved | Web3桥接；web3-adapter验收 | 存在实现和局部验收；本次未逐条重跑完整验收，整体完成未证明 |
| V5-DOM-03 | P1 | telemetry mapped；claim contradictions mapped | Agent Audit桥接；agent-audit-adapter验收 | 存在实现和局部验收；本次未逐条重跑完整验收，整体完成未证明 |
| V5-CR-01 | P1 | bounded tick；checkpoint delta；local-first；budget；scope preserved | v5_continuous.py；continuous-research验收 | 存在实现和局部验收；本次未逐条重跑完整验收，整体完成未证明 |
| V5-INTEL-01 | P1 | source adapter interface；fingerprints；applicability；candidate not finding | v5_intelligence.py；intelligence-core验收 | 存在实现和局部验收；本次未逐条重跑完整验收，整体完成未证明 |
| V5-ORCH-02 | P1 | structured outputs；counterevidence first-class；verifier isolated | v5_workers.py、v5_verification.py、V5 HTTP传输/回执/正式结果/复测增量 | 存在实现和局部验收；本次未逐条重跑完整验收，整体完成未证明 |
| V5-ORCH-03 | P2 | capsule transfer；evaluator；rank/select；lineage | v5_evolution.py；cross-pollination/evolver/durable-evolution验收 | 存在实现和局部验收；本次未逐条重跑完整验收，整体完成未证明 |
| V5-SCALE-01 | P2 | bounded concurrency；stable recovery；budget metrics；no full transcript fanout | scheduler-benchmark、graph/history/task-pagination验收 | 存在实现和局部验收；本次未逐条重跑完整验收，整体完成未证明 |

所有15个Task及done条款保留为完整目标。上表没有把“文件存在”标为完成。现有API会话/Host/Origin实现已接线，因此旧SEC-01文档中“会话未完成”的结论不能当作当前事实，应结合后续SEC-02与源码；M1发布安全门仍未整体通过。

## Known Gaps完整清单与需要取得的证据

| 原缺口 | 实际所需证明 | 当前未闭合项 |
|---|---|---|
| 版本/许可证固定 | exact tag/commit、逐依赖license、SBOM、Notice | 发布范围所有依赖与参考项目未形成完整固定清单 |
| 安全M0/M1 | 会话、Host/Origin、桌面身份、资源/输出限制、取消/进程树、secret store | API/session已有实现；所有执行类型、安装版和旧HTTP确认路径的统一独立门待验收 |
| 前端集成 | Runtime/Orchestrator/Intel/Trust真实API、语言/主题和操作链 | 已有V5与隔离UI证据；全部真实数据/安装版操作链未完整复核 |
| 三域生产图 | Traditional/Web3/Agent Audit真实输入到图、证明及生命周期 | 现有桥接与局部用例不能替代三域生产链验收 |
| 多角色质量 | 8/16/32与single baseline，真实cost/evidence gain/precision/replay rate | 结构化worker不等于真实模型质量基准 |
| 情报源治理 | 每个adapter terms/rate/cache/license/freshness/normalization | core与适用性oracle已有实现；全部具体源治理与当前官方条款待核实 |
| 持续研究 | checkpoint/delta/no-op/budget stop/local-first/cloud precision | 已有局部验收；真实持续运行与模型精度未闭合 |
| 分布式规模 | 单机100→1000逻辑任务、稳定恢复/预算/并发瓶颈证据 | 有调度/分页基准，需匹配原条款和实际部署；10,000为后续P2/P3，不提前上分布式设施 |

## 本轮实际改变下一步的证据

- macOS Seatbelt的remote-ip规则不接受数字IP，但实际实验确认：继承的已连接TCP socket可使用，同时deny network*阻止新建连接。此能力可作为不开放通配出站的授权目标传输边界。
- 新通道由可信监督器按network_guard返回的数字IP连接，取消可中断连接等待；工作进程只继承该socket和匿名标准管道，禁止新建网络连接，校验peer IP/port，HTTPS验证原主机名。
- 已在本地真实HTTP/TLS负例、Scope/材料变化、正式保存/修复谱系测试中验证。非loopback真实授权目标尚未现场验收，不宣称外部生产链完成。
- 0.68.8审计时旧HTTP finalize=True仍在进程内确认；0.68.11已统一V5账本，0.68.12已加入判定期间取消/完整材料恢复。后续增量是当前证据，不能沿用已过时缺口或据此宣称完整计划完成。

## 下一行动与完成判据

继续V5-ORCH-02：实际主进程退出/安装冷启动、真实发现链及跨claim/三域独立验证。按原全部Task条款分别记录实现、当前直接验收、间接证据、缺失/不通过；所有明确要求有匹配范围的证据前，目标保持进行中。

## 0.68.9增量核对

旧HTTP finalize=True已强制独立请求与纯语义沙箱，正式成功/修复新收据再次核对独立结果。传统任务尚未统一V5任务/租约/不可变回执，旧修复谱系及输入全集/归档/中断材料还需统一升级；读取Oracle也未证明人工填写严重度或源码根因。此增量没有将V5-ORCH-02整体或其它Task标为完成。直接证据见acceptance/2026-10-04-v5-orch-02-traditional-http-independent.md。

## 0.68.10增量核对

HTTP逐响应非晋升检查点及引用已持久化；本地1/4/10响应取消、材料变化与模拟监督器退出后的实际恢复函数通过。此前“中断后所有已返回响应均只存在内存”的缺口已缩小；实际系统kill/安装版重启、写入失败边界、统一传统V5账本和及时判定取消尚未验收。整体V5-ORCH-02及15 Task不因这一步标为完成。直接证据见acceptance/2026-10-04-v5-orch-02-http-interruption-checkpoints.md。

## 0.68.11增量核对

传统正式HTTP的同步/后台结果已接入V5持久任务/租约/不可变回执及限定正式晋升、精确修复谱系；不再由独立但脱离V5账本的旧确认直接签发。执行期间原证据全集、归档/Run状态与认证授权加入守卫。全量740通过/3跳过及旧入口正例/报告/三种复测直接验证。完整任务重启后interrupted语义重试、判定期间取消、签发后新增证据全集仍未闭合；15 Task及真实发现/三域/模型/规模/安装发布目标不因本增量标为完成。证据见acceptance/2026-10-04-v5-orch-02-traditional-v5-ledger.md。

## 0.68.12增量核对

纯判定监督器已加入200ms取消/租约/当前材料检查和finally终止回收；完整传统interrupted来源可在严格10响应/Artifact/授权守卫下恢复语义判定，不重新HTTP。真实等待沙箱终止、判定期间取消和实际启动恢复函数验收通过；实际App冷启动/主进程kill、签发后新增证据全集守卫及全计划生产验收未完成，整体目标保持进行中。证据见acceptance/2026-10-04-v5-orch-02-verifier-cancel-recovery.md。

## 0.68.13增量核对

候选证据集合精确核对及所选claim反证上下文守卫已接入，新反证会阻断旧回执晋升/报告导出。guided实际派生证据有固定marker，负例自身派生反证只接受原样记录；不宣称跨claim/全项目反证已推断。751通过/3跳过回归基线及最终nullable来源专项2通过；完整历史迁移、真实发现、实际主进程kill/安装冷启动和全计划生产验收仍未完成。证据见acceptance/2026-10-04-v5-orch-02-evidence-context-guard.md。
