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

继续V5-ORCH-02：安装配置核验、其它执行类型父死亡清理、真实发现链及跨claim/三域独立验证。实际开发包原生冷启动、桌面父死亡/正常退出、HTTP监督器SIGKILL、新进程恢复及活跃HTTP/纯判定子进程父死亡已取得下述直接证据。按原全部Task条款分别记录实现、当前直接验收、间接证据、缺失/不通过；所有明确要求有匹配范围的证据前，目标保持进行中。

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

## 0.68.13后续：实际监督器SIGKILL验收

三个独立进程场景已验证1/10、10/10材料提交及V5已领取判定租约后的SIGKILL。新Python进程实际恢复来源任务、保留Artifact字节/哈希/引用；fixture短租约按墙钟过期后重新领取并以真实沙箱判定完整材料，HTTP不重发、Finding不自动生成。这是HTTP监督器和恢复入口直接证据，不能替代App/ASGI冷启动、活跃子进程孤儿清理、提交中途终止或断电验收。源码/Schema没有变化。证据见acceptance/2026-10-04-v5-orch-02-process-sigkill-recovery.md。

打包脚本修复旧Info.plist模板版本被原样沿用的问题；统一读取version.py后实际开发包元数据0.68.13/build91，构建及ad-hoc签名检查通过。未覆盖/Applications/Fieldwork.app，其仍0.68.2/build80；不因此认定安装发布门完成。

## 0.68.14：活跃子进程父死亡

HTTP与判定工作进程加入只读管道EOF存活守卫，父死亡不依赖finally。真实HTTP等待和真实沙箱等待的父监督器SIGKILL均在3秒内结束子进程；既有取消/恢复/隔离专项25及新增2项通过。仅证明这两类受信任worker，不扩展为其它工具/任意不可信原生代码/整个进程树或安装版冷启动完成。无schema迁移，历史回执保持原SHA。证据见acceptance/2026-10-04-v5-orch-02-parent-lifetime.md。

## 0.68.14后续：完整ASGI冷启动

三套临时数据经6次真实uvicorn完整lifespan启动与3次SIGKILL后重启；传统/guided任务、Run状态和过期V5租约恢复，材料/SHA/引用保留，不重发HTTP，不误签回执/正式Finding。会话轮换拒绝旧令牌，Host/Origin与版本/实例标识直接验证。默认后台循环已启用，第一轮空调度健康；尚未证明真实到期作业、默认安装数据/桌面WKWebView/完整后台周期。实际ASGI冷启动已取得直接证据，不继续作为完全缺失项；桌面冷启动和完整规划仍未完成。见acceptance/2026-10-04-v5-orch-02-asgi-cold-start.md。

## 0.68.15：实际原生冷启动与桌面后端

构建包携带本机启动配置，Swift不再写死个人路径；只读stdin存活管道绑定桌面与uvicorn后端，实际原生父SIGKILL后后端0.931秒停止，重启WKWebView加载V5并正常退出关闭端口。冷启动暴露工具串行探测15秒超时，现改为8线程有界并发、共享冷缓存与每工具8秒总预算，实际UI工具就绪。仅证明隔离开发包实际启动，未覆盖/Applications安装/生产数据历史、独立分发或发布签名；全15 Task/8 Gaps仍未完成。证据见acceptance/2026-10-04-v5-orch-02-native-cold-start.md。

## 0.68.15后续：实际安装与已有数据保留

源码f2f0e45推送后继续备份/安装，/Applications实际版本0.68.15/build93。原生V5加载、工具就绪、health及会话边界直接验证；六类历史材料逐行SHA/行数与备份一致、schema25/integrity正常、暂停监控未激活。旧App与数据库有本地备份。没有活动生产任务，因此不是生产活动任务kill恢复证明；独立分发/发布签名、公证、真实发现/三域/模型/规模和完整规划仍未完成。见acceptance/2026-10-04-native-install-06815.md。

2026-10-13：补齐独立锁定运行环境验收。已安装0.68.73/build151指向36依赖私有环境；干净全量1016通过/3跳过及安装安全4通过，18页144组合和36组件SBOM验证通过。本轮九表原有行逐条保留，产物/观察各新增3条，触发者未归因；未宣称全表不变。完整规划继续。见 docs/acceptance/2026-10/2026-10-13-clean-locked-runtime.md。


### 2026-10-18 指定 Run 资料与研究输入隔离

补齐后建 Campaign 的登记资料预览/关联，保持私有并保留反证；绑定 Run 加显式共享资料，跨 Run 冻结任务暂停，反证超限拒绝。32项相关后端、隔离真实API界面和一次实际Nuclei→DeepSeek草稿连接通过，安装App入口已重启确认。完整规划仍开放。详见 `docs/acceptance/2026-10/2026-10-18-campaign-recorded-materials.md`。


### 2026-10-18 草稿登记来源核对

草稿审阅不再把图谱摘要匹配称为原始材料一致；新增受存储锁保护的登记来源/有界文件SHA核对、缺失与未登记状态及跨Run输入过滤。33项相关测试和3组11任务真实API界面通过，App正常重启。不是独立行为复验，完整规划保持开放。见 `docs/acceptance/2026-10/2026-10-18-draft-source-integrity.md`。


### 2026-10-18 草稿到同Run复验衔接

新增草稿审阅到同Run候选/阻塞项的只读入口；API核对项目归属，保留分页之外的历史Run，项目改变清除上下文。真实API界面及已有流程衔接回归通过；不生成候选、不启动复验，完整规划仍开放。见 `docs/acceptance/2026-10/2026-10-18-draft-verification-handoff.md`。


### 2026-10-18 当前累计代码完整回归

默认完整回归1452通过/11跳过/2警告（504.06秒），18页/5Agent页签/144显示组合通过，359个源与测试文件运行前后SHA一致。包含backend目录布局及最近资料导入/草稿核验/复验衔接改动；跳过项不算完成，完整业务门继续开放。见 `docs/acceptance/2026-10/2026-10-18-current-complete-regression.md`。


### 2026-10-19 浏览器对照后独立复验可达性

修复reproduced候选被影响审阅状态阻断独立复验的问题，保留默认状态并新增受审阅路径；普通历史不得充当隔离执行。真实浏览器正例经独立回执/显式晋升生成限定报告，修复负例refuted、合法共享阻断；3例通过，78相关回归和分诊UI通过。预置候选、0模型调用，完整规划继续开放。见 `docs/acceptance/2026-10/2026-10-19-browser-independent-report-continuation.md`。


### 2026-10-19 Run级受审阅独立复验批次

独立复验页新增批次计划/提交，最多3条及剩余请求预算；材料、选中名单与回执状态绑定预览指纹。实际逐候选检查点与产物核对，多候选回执不再受旧总量10次假设阻断；当前回执跳过、余项保留并推进下批。最终8项批次测试、隔离UI及原生空批次预览通过。下一批需重新审阅，不宣称全自动接力。见 `docs/acceptance/2026-10/2026-10-19-reviewed-replay-batches.md`。完整规划继续开放。


### 2026-10-19 复验批次分页与执行范围审阅

支持100条之后的分页，分页位置绑定计划与Worker，切页撤销旧授权；选中项可展开Scope、业务规则及请求角色/URL/身份，不显示凭据。101条分两页及尾页真实独立复验、当前9项批次回归、批次及原单条UI检查通过；App原生0候选分页状态已核对。分页不是质量或自动接力验收，完整规划保持开放。见 `docs/acceptance/2026-10/2026-10-19-reviewed-batch-pagination.md`。


### 2026-10-19 草稿到未验证候选

新增受审阅转换：成功任务/当前Traditional授权/活动Campaign、冻结图谱与登记文件核验，保存原证据与反证polarity；显式目标、幂等与事务回滚。45相关测试和隔离真实API界面通过。实际DeepSeek两次分别schema_mismatch/输出截断，已结算并暂停、0Claim/0候选，未通过真实模型衔接验收；完整目标继续开放。见 `docs/acceptance/2026-10/2026-10-19-research-candidate-handoff.md`。


### 2026-10-19 Provider生成输出上限

将固定4000生成上限改为可见配置（旧默认不变），始终受总预算/输入/上下文限制；修改使旧Profile失效，元数据和费用联合更新不丢失，语言切换保留未保存行编辑。37相关测试及真实API UI通过，App原生4000默认已确认，主库Provider metadata前后相同。本轮0付费调用，真实DeepSeek稳定输出门仍未通过，完整规划继续开放。见 `docs/acceptance/2026-10/2026-10-19-provider-output-cap.md`。


### 2026-10-19 实际模型受审阅候选连接补证

隔离Provider输出9000/任务总16000下，实际Nuclei→DeepSeek：信号正例3Claim、审阅新增1个scanner_match候选；修复负例0Claim/0模型候选；均0正式Finding。两例settled，保守用量43620micros，主库配置/调用未变。此前失败保留，不宣称稳定性/三域质量或独立漏洞确认；完整规划仍开放。见 `docs/acceptance/2026-10/2026-10-19-actual-model-reviewed-candidate.md`。


## 2026-10-19 浏览器真实模型与独立复验增量

公开loopback开发夹具无预置候选：真实浏览器→DeepSeek→审阅候选→独立verified→1限定报告已跑通；修复refuted/共享业务规则阻止，均0Finding。最终拒绝反证版本仅重跑修复例，非完整冻结三例矩阵。61相关测试、3实际浏览器测试、18页/5页签/144显示组合通过。完整15Tasks/8Gaps继续，历史去重材料补录、三域质量、三次原生启动等未完成。详见 docs/acceptance/2026-10/2026-10-19-browser-model-ownership-pipeline.md。


### 2026-10-19 旧采集重新登记兼容

同Run新浏览器采集不再被仅有HTTP行的旧版本记录阻断；必须已有对应观察和登记产物才去重，保留历史行且不执行历史批量补录。42相关测试及22实际浏览器/资料/草稿回归通过，0付费模型调用。UI未改；原生重启加载因Mac锁屏待验收。完整规划保持开放。见 `docs/acceptance/2026-10/2026-10-19-legacy-browser-recapture.md`。


### 2026-10-19 当前累计完整回归

当前HEAD52187a6加未提交改动：默认完整回归1491通过/14跳过/2警告（484.76秒）；18页/5Agent页签/144显示组合及既有操作通过。四目录314源码/测试集合及字节运行前后相同，日志SHA已保存。跳过与原生锁屏项不算通过，完整15Tasks/8Gaps继续。见 `docs/acceptance/2026-10/2026-10-19-current-complete-regression.md`。


### 2026-10-19 Agent攻击测试申请管理（部分）

原禁用预览入口接入实际预检/确认保存/取消API；身份组件指纹变化阻止旧确认、严格用例数和幂等事务，状态明确awaiting_adapter/未派发。62相关测试和真实API UI新增申请生命周期及18页/5页签/144组合通过，0模型调用。尚无可执行受测Agent适配器，不是主动攻击能力完成；后续继续范围/预算/隔离执行/独立回执。App加载待解锁，完整规划不关闭。见 `docs/acceptance/2026-10/2026-10-19-agent-attack-request-management.md`。


### 2026-10-19 本地Agent适配器执行原语（部分）

增加合成提示注入标记观察的真实macOS无网络Python适配器执行原语，源码SHA/64KiB/两例/每例1–15秒、协议错误/超时不算干净负例，JSON转义泄露及控制组泄露分开。16执行原语与申请测试通过，未接App派发/Worker/正式回执，未测试外部Agent或真实模型质量；工具/记忆场景及完整规划继续。UI未改、原生待解锁。见 `docs/acceptance/2026-10/2026-10-19-local-agent-adapter-executor.md`。


### 2026-10-19 本地Agent适配器登记与启停（部分）

新增源码语法/字节/摘要预检、当前配置CAS、确认保存、启停与篡改拒绝；响应事件不返回源码。登记不执行，申请预检显示派发尚未接通。72相关测试通过；真实API UI首轮发现GET误用已修，重跑上传/保存启用/禁用数据库断言及18页/5页签/144组合通过。0模型调用，原生加载待解锁。实际Worker/项目授权/预算/取消恢复/独立回执继续开放，完整规划不关闭。见 `docs/acceptance/2026-10/2026-10-19-agent-adapter-registration.md`。


### 2026-10-19 Agent适配器生命周期与取消（部分）

新增可信组长监督器/父管道约束、适配器禁fork/signal/网络broker、运行取消与异常清理，并修共享执行器关闭输出后仍须超时的等待问题。20专项及37相关含Web3工具回归通过；真实父进程kill后子进程停止。UI未改，实际派发/项目授权/持久恢复/独立回执仍待接通，完整规划保留。见 `docs/acceptance/2026-10/2026-10-19-agent-adapter-lifetime-cancel.md`。


### 2026-10-19 本地适配器受审阅派发与精确Run证据

已接通本地Python合成提示注入的实际派发：Audit项目新授权快照/新synthetic Run、两例逐次预算、单执行锁、运行中授权/身份/预算检查、取消/显式中断恢复、产物原子登记和本记录内精确Run/SHA证据。93相关测试与真实API UI及18页/5页签/144组合通过，8文件前后摘要一致；旧列名和跨域项目缓存失败已修并保留日志。该限定链不关闭工具/记忆、真实Agent质量、独立回执或持久Worker；原生待解锁，完整规划继续。见 `docs/acceptance/2026-10/2026-10-19-local-agent-reviewed-dispatch.md`。


### 2026-10-19 记忆策略文件观察（限定场景）

memory-protected-policy.v1已接入同一授权/两例预算/实际沙箱/精确Run证据，监督器记录最终文件前后字节及SHA；仅笔记变化不算污染、控制污染/不完整不算攻击特有。修导出根链接及特殊文件/限额边界，新增导出存在状态防止误读旧基线；未支持工具/不足预算明确阻塞。113相关及双场景真实API UI、18页/5页签/144组合/430px证据保留通过。13文件前后相同；最终状态不证明读取/中间行为或正式独立回执，完整规划继续。见 `docs/acceptance/2026-10/2026-10-19-agent-memory-file-observations.md`。


### 2026-10-19 合成工具请求代理观察（限定场景）

JSONL受控代理接入原授权/派发链，实际记录请求/响应/SHA，控制校准要求公开资料随机回执；未授权资源/工具均拒绝，口头自述与伪造包不作调用。两次适配器运行、每例4次/合计8次合成RPC，不授予主机工具/网络；严格结构/ID/配额/结束后输出及父退出/取消检查。130相关及最终三场景真实API UI、18页/5页签/144组合通过，11文件前后相同。真实工具效果、独立回执/逐调用持久检查点及真实Agent质量继续开放；原生仍待解锁。见 `docs/acceptance/2026-10/2026-10-19-agent-synthetic-tool-broker.md`。


### 2026-10-20 工具持久检查点与原生加载

响应释放前经父端验证和数据库提交，再由私有确认管道放行；准备/未知/扣住/写入管道分开记录，真实取消及父执行进程死亡后前缀保留，显式恢复不重发。修并发锁打开偶发ENOENT的受限处理：同一目录句柄、完整路径/inode复核、最多3次，保留原私有文件/flock检查且不回退绝对路径。3轮7项压力检查及193相关通过，最终UI三场景/检查点/18页5页签144组合通过。Mac解锁后现有App正常重启加载，原生18页/5页签入口可读；Provider摘要相同，主库模型/攻击记录0。一次原生导航不是三域质量或独立发布；全规划继续。见 `docs/acceptance/2026-10/2026-10-20-agent-tool-durable-checkpoints-and-native-reload.md`。


## 2026-10-20 智能体观察结果摘要增量

新增冻结合成输入与只读结果审阅，重新核对输入、输出和场景信号；区分观察到、限定范围未观察到、对照污染、材料不完整及旧输入不可用。当前性包含Agent/组件摘要。UI保留原始证据/检查点并增加摘要表及Run标识。65相关回归通过；隔离浏览器实际三场景、摘要/证据/检查点、430宽展开及18页/5页签/144组合通过，0模型调用。原生正常重启加载当前源，攻击测试页可读；主库Provider摘要及调用/测试计数前后相同。主库无夹具，原生摘要交互未验收；同份观察审阅不是独立行为收据、真实主机效果或整体完成。验收：docs/acceptance/2026-10/2026-10-20-agent-observation-review.md。完整15Tasks/8KnownGaps继续开放。


## 2026-10-20 冻结浏览器模型矩阵与拒绝输出诊断

同一冻结版本实际DeepSeek三场景已执行终态exit1，2通过/1失败：漏洞2Claim→独立verified/1发现；修复invalid_json暂停、0Claim/0发现，不能算负例通过；合法共享2Claim但业务规则阻止、0发现。3次均settled，配置保守费用合计161830micros，未重发。24份登记产物和Run/Scope/观察等原事实补归档并核对SHA。新增仅含字节/SHA/固定类别/位置的输出诊断，严格解析与用量结算不放宽；73相关、3归档守卫及研究组真实API/协议夹具8显示组合通过。原生重启加载，Provider摘要/主库0调用不变。完整质量门仍失败、新诊断尚无实际DeepSeek失败样本、完整15Tasks/8KnownGaps继续。见 docs/acceptance/2026-10/2026-10-20-frozen-browser-model-matrix-and-json-diagnostics.md。


## 2026-10-20 证据引用契约、一次冻结三例通过与当前全量

新增每次模型输入的支持ID枚举/反证固定清单/去重要求，无支持材料不得提出假设；后端独立核验并给出固定语义拒绝原因，研究组UI可读、不自动重试。98相关及研究组协议夹具UI通过。真实DeepSeek前一版本1通过2失败/178250保守micros，旧失败保留；新契约一次冻结开发三例3通过（漏洞verified/1发现，修复refuted/0，共享业务规则阻止/0），3调用147260保守micros，36登记产物SHA核对。本轮6调用总325510micros非账单，不宣称稳定性。首次全量1591通过但磁盘限额默认值失败，改为调用时读取当前上限且显式超限仍拒绝；126相关后，最终全量1596 passed/14 skipped/2 warnings/518.91秒通过，333源码前后相同。18页5页签144组合及原操作入口保持通过；源关联App已重启加载，主库Provider摘要/0调用计数不变。付费矩阵早于后续限额默认值修正，整体15Tasks/8KnownGaps仍开放。见 docs/acceptance/2026-10/2026-10-20-bound-research-evidence-contract.md。


## 2026-10-20 组内草稿批量审阅与原子候选保存

新增活动Traditional研究组的只读分页草稿索引、最多3条合并预览/原子保存，同组同Run跨任务可选；每条沿用当前授权/登记文件/反证核验，来源或选中内容变化阻止，中途失败全部回滚，已存候选幂等保留实际状态。UI新卡片入口保留原单条/来源/同Run复验入口；目标或页面变化撤销旧确认，不启动模型、目标请求、复验或正式发现。48相关/13.71秒及3组11任务22写入/8组合真实API界面通过，18页5页签144组合/原操作通过，7文件SHA一致；源关联App重启加载，主库配置/0调用摘要不变。主库无测试组，原生批量记录交互未验收；旧1596全量早于此增量，不冒充当前全量。完整15Tasks/8KnownGaps继续。见 docs/acceptance/2026-10/2026-10-20-group-draft-batch-handoff.md。


## 2026-10-21 精确候选名单到复验计划

单条/组内候选保存后携带最多3条明确ID，直接打开所选名单的只读复验计划，不再默认处理Run中较早条目；超100条也精确定位。后端预览/执行/Worker/回执均绑定名单，失效拒绝不换选；登录恢复不把已审阅/限定名单扩大到全Run。UI显示名单，清除后恢复原全Run继续入口，项目/Run切换清理状态，旧服务忽略过滤时拒绝。82相关/95.78秒、29恢复守卫、实际API组界面/兼容界面与18页5页签144组合通过；10源码前后一致。当前Mac锁屏阻止本轮原生重启，已请求解锁；主库Provider摘要/0调用计数不变，不称本轮App已加载。整体15Tasks/8KnownGaps继续，旧1596全量早于新增量。见 docs/acceptance/2026-10/2026-10-21-selected-candidate-replay-handoff.md。


## 2026-10-21 适配器源码历史与未启用恢复

UI-04/06局部补齐：原攻击测试配置区域新增最多20个不同源码版本、显式查看、恢复预览和单独确认，恢复强制未启用；旧配置/执行计划不复活，不执行源码或调用模型。历史位于已有私有组件行，单源码/记录/索引摘要校验，事务失败回滚，旧数据仅捕获已知当前源码，不伪造历史；通用详情不泄漏源码但保留显式查看入口。129相关测试/38.69秒、18页面5页签144显示组合及原操作通过，零模型调用；旧1596全量早于新增量。Mac仍锁屏，原生App加载/交互验收待完成。完整15Tasks/8KnownGaps继续。见 docs/acceptance/2026-10/2026-10-21-agent-adapter-source-history.md。


## 2026-10-21 当前累计完整回归

补齐最近组内草稿批量审阅、精确候选名单、适配器源码历史增量后的默认全量：1630 passed/14 skipped/2 warnings/494.83秒，exit0，无失败。339冻结源码/测试前后集合及SHA一致；配套最近18页5页签144组合验收源码一致，原操作保持。跳过的真实模型/浏览器/部分原生专门门不计完成；Mac仍锁屏，本轮没有重启App或验证加载。未修改主库模型配置，未调用付费模型。源码核对仍缺跨阶段无人值守控制器、完整持久恢复及独立Agent行为证明等，完整15Tasks/8KnownGaps继续。见docs/acceptance/2026-10/2026-10-21-current-complete-regression.md。


## 2026-10-21 研究调度器心跳与归属

修复运行标记仅启动更新导致长期组可能被另一进程误判陈旧：调度/在途等待持续心跳，领取前再核对归属；未过期活动租约阻止第二调度器，旧线程不覆盖新归属。原研究组卡片新增只读执行状态与恢复提示，计数/心跳不冒充研究完成，不自动重启/重放。94相关测试/17.33秒（含真实第二进程和在途线程）及真实API组UI/8显示组合弹窗/430宽通过，0云模型调用，原创建控制/草稿候选入口保留。旧1630全量早于本增量；Mac仍锁屏，原生加载待验收。跨阶段自动控制器与完整持久Worker仍未闭合，15Tasks/8KnownGaps继续。见docs/acceptance/2026-10/2026-10-21-research-dispatcher-ownership.md。


## 2026-10-21 模型租约中断待核对

过期租约不再把可能已调用/已结算但结果未落库的模型任务自动排队：有效预留、calling、unknown、settled均暂停待核对，保留结果/账本/attempt，已过期且未发送预留可释放后按原规则恢复。迟到已知响应仍结算消费，不提交草稿或覆盖中断标记。原任务表中英提示和组执行弹窗待核对计数接通，读取无执行。最终114相关/42.39秒及3组11任务22写入/1本机协议夹具、8显示组合与430宽通过，0云调用；6文件前后稳定。原演化旧attempt隔离测试改为先明确结算/操作者resume，不允许calling直接释放；失败日志保留。旧1630全量早于增量，Mac仍锁屏未原生验收；跨阶段控制器、完整持久Worker及15Tasks/8KnownGaps继续。见docs/acceptance/2026-10/2026-10-21-model-lease-interrupted-review.md。


## 2026-10-21 明确授权的未开始任务自动续跑

原研究组新增自动续跑设置：确切1–1000个queued/attempt0/无调用任务、明确Profile及冻结材料、摘要确认与revision CAS；App后端每5秒检查最多8策略，重启后可自动继续原清单，领取前复核授权/任务/组预算/模型配置/材料。已开始/未知用量不自动重放；任一批准任务暂停/失败或输入配置改变后blocked，需审阅；关闭仅停止本策略新领取，在途可完成，原手动控制保留。策略状态与实际调度心跳分开，shutdown等待确切tick及异常结束后再停Worker。126相关/37.30秒（含真实uvicorn两启动+本机协议2调用后不重复及shutdown等待异常）通过；组UI3组11任务27写入/1本机协议/8显示组合及整页18导航5页签144组合/原操作通过。7文件最终稳定，0云调用，无schema迁移。Mac仍锁屏，原生加载待验收；不是独立常驻Worker或跨阶段控制器，旧1630全量早于增量，完整15Tasks/8KnownGaps继续。见docs/acceptance/2026-10/2026-10-21-research-unstarted-auto-resume.md。


## 2026-10-21 续跑轮转与真实任务进度

启用策略按持久游标每轮至多8组环回检查，前8运行不长期挡住第9组；不扩大已批准清单。基于任务实际记录显示未开始/在途/已存research_draft/待核对/缺失，已批准attempt>0重新排队必须待核对；历史completed不再遮住未完成任务，GET只读保留旧JSON。续跑状态/后台状态中英可读，旧服务缺进度显示未提供，不伪造0或草稿完成。129相关/36.02秒及真实API组UI3组11任务27写入/1本机协议/8组合和旧服务缺字段模拟通过，最终4文件稳定。9组轮转为实际API策略+明确live句柄fixture，非9真实模型并发质量证明；0云调用。Mac仍锁屏，原生加载待验收；上一轮18页144组合并未本轮重跑，旧1630全量早于增量。完整15Tasks/8KnownGaps继续。见docs/acceptance/2026-10/2026-10-21-resume-fairness-and-task-progress.md。


### 2026-10-21 native reload acceptance
18 pages and six Agent tabs loaded in installed source-linked App; existing group resume/recovery dialogs visually checked, missing explicit Profile correctly blocked preview. No model calls/config changes. English error and native modal AX omission remain. See `docs/acceptance/2026-10/2026-10-21-native-resume-reload.md`. Full flow and distribution gates remain open.


### 2026-10-21 续跑错误说明与恢复语义修复
17条已知失败原因中英映射；重新预览清旧错误与旧批准，恢复说明区分批准的未开始任务续跑与不重放已开始任务。3组11任务43隔离写入/8显示组合/1本机协议夹具通过，原生已重启并读到中文错误和新恢复说明，主库模型/活动摘要不变、0调用。未重跑全量，未新增自动候选控制器；完整18页/15Tasks/8KnownGaps仍继续。见 `docs/acceptance/2026-10/2026-10-21-resume-localized-errors.md`。


### 2026-10-21 续跑增量后的累计回归
默认完整套件1660通过/14跳过/2警告/605.92秒、精确终态exit0；341源码文件集合与SHA前后相同。隔离真实API的18页、5智能体详情标签、8表面及144页面/显示组合通过，零模型调用。旧1630证据由本次当前累计版本证据补充；没有新增功能、迁移或重打包。完整UI-00→QA-07、15Tasks/8KnownGaps保持进行中。见 `docs/acceptance/2026-10/2026-10-21-cumulative-regression-after-resume.md`。


### 2026-10-21 自动准备可核验草稿批次
原批量审阅新增只读自动准备：本页按固定顺序最多3条，原来源/当前授权核验，跳过已有候选和不明确目标；保留手动选择与原子保存确认，不调用模型/工具或自动复验。29相关测试及真实API组UI通过；原生已重启加载，中文无草稿禁用态核对，主库摘要不变。不是跨阶段自主控制器；1660全量早于本轮。见 `docs/acceptance/2026-10/2026-10-21-automatic-draft-batch-preparation.md`。


### 2026-10-21 单独批准的自动候选衔接
原续跑设置增加候选上限0–3和独立严格确认；默认0/旧策略不扩权。当前Traditional授权与登记来源核验，全部批准任务草稿完成后每轮最多5任务、最多批准上限候选，游标与写入同事务；保留反证/手工入口，歧义待核对，不自动复验或晋升。55相关测试及实际API组UI双确认/8显示组合通过。原生已重启，但后续工具报告用户改变App，新增控件原生交互待验收；主库摘要不变、0调用0启用。完整目标继续，1660全量早于本轮。见 `docs/acceptance/2026-10/2026-10-21-policy-approved-auto-candidates.md`。


### 2026-10-21 自动候选的草稿完整性
Worker完成事务保存Claim完整行摘要，自动衔接检查精确清单/摘要/草稿状态，编辑或历史缺摘要转待核对，手工审阅保留；提交等待时锁定候选配置与确认框。74相关测试及真实API UI挂起响应/8显示组合通过。本轮未重启App，原生新逻辑未验收；旧1660全量早于本修复，完整目标继续。见 `docs/acceptance/2026-10/2026-10-21-auto-candidate-draft-integrity.md`。


### 2026-10-21 自动候选设置原生只读验收
CUA正常退出并重启安装源码关联App，health停/起证实；原生键盘选择已有项目成功。自动候选默认关闭、0–3可读，临时上限1预览因旧组缺Profile正确阻断；关闭重开恢复默认关闭。主库前后模型配置摘要/活动计数一致，0模型调用0启用策略。新增控件只读验收已补齐，正向原生自动研究/独立复验仍待完整验收。见 `docs/acceptance/2026-10/2026-10-21-native-auto-candidate-settings.md`。


### 2026-10-21 续跑实时状态与精确结果入口
原续跑弹窗每5秒可见时只读刷新/显式刷新，同一状态请求串行，提交期间不覆盖，关闭清定时器；revision/state变化清旧批准，错误独立展示。真实API UI自动读外部revision变化、精确2候选跳复验排除同Run噪声、关闭后5.5秒无轮询，原8显示组合通过。原生已重启并实际刷新无错，主库摘要不变0调用/启用。无后端/schema改动，未全量/真实研究，完整目标继续。见 `docs/acceptance/2026-10/2026-10-21-resume-live-status-and-result-link.md`。


### 2026-10-21 实际模型自动候选与资产噪声
实际Nuclei→真实DeepSeek→策略正例成功；真实修复对照暴露surface资产事实误入候选，已增加自动安全类别门，普通资产草稿保留/计数，手工路径保留。修复后实际正例3草稿→2安全候选+1资产草稿；对照无效JSON被settled/paused/blocked，保留exit1未伪装通过。4真实调用合计保守109610微美元，主库0调用且摘要不变。60相关测试/真实API UI通过，App原生重启加载。有效负例及完整跨阶段/三域/发布目标继续。见 `docs/acceptance/2026-10/2026-10-21-actual-model-auto-candidates-security-gate.md`。


### 2026-10-21 研究组任务完整分页
修复“查看任务”只过滤已加载100条导致组任务缺失：后端group_id SQL过滤/游标绑定，前端按组/全部组重新读取及继续分页，原控制保留。19相关测试含1001组任务+110噪声，真实API UI首屏隐藏→8组任务→全部121通过，原8显示组合继续。App已重启并原生切组读取，主库摘要不变0调用/启用。仅逻辑分页证明，完整质量/并发/跨阶段/分发目标继续。见 `docs/acceptance/2026-10/2026-10-21-team-task-group-pagination.md`。
