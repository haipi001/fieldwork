# 研究输出证据契约与冻结三场景验收

日期：2026-10-20。完整 UI-00→QA-07、15 Tasks/8 Known Gaps 保留，不以本轮局部验收关闭整体目标。

## 新版本前的真实失败

新诊断版本首先运行 `build/browser-model-frozen-matrix-20261020T114024Z`：1 passed/2 failed，97.96秒，exit1；源码前后相同。修复例完成独立refuted；漏洞和合法共享在JSON解析后的HTTP校验阶段暂停，无法从旧通用HTTPException确定具体语义原因。三次均settled，配置保守估算178250micros。24份登记产物及矩阵文件摘要均核对一致。失败记录保留，没有当作质量通过。

## 实现

- researcher/explorer/specialist每次请求的JSON Schema直接绑定本次图谱输入：仅observation/evidence节点ID可作为支持证据；反证必须原样保留为固定ID清单；两类引用不重复。无支持材料时仅允许空hypotheses。
- 不将节点正文或其自称ID加入可用清单；不能通过不可信正文扩充引用权限。Schema每次独立生成，不污染其他任务。
- 预检/实际请求继续由同一消息摘要绑定；新增约束计入原词元/上下文预算，不绕过计数。
- 后端仍独立逐项校验，不信任模型遵守Schema。把未批准支持引用、反证集合不匹配、两种重复引用、空白限制/问题、调用预算等失败映射为固定语义原因，不保存模型私有正文、自由异常消息或伪造ID。
- 研究组UI显示中英语义原因及明确不自动重试的说明，保留原用量、错误、草稿、组控制及页面。

## 新契约后的冻结矩阵：本轮通过

命令：`FIELDWORK_LIVE_BROWSER_MODEL=1 .venv/bin/python scripts/run_browser_model_matrix.py`。

唯一目录：`build/browser-model-frozen-matrix-20261020T114443Z`；进程exit0、3 passed/1依赖警告、86.68秒；backend/static/templates/tests及运行/归档脚本摘要在运行前后相同。

| 场景 | 实际调用 | 草稿Claim | 独立处理 | 正式发现 | 配置保守cost_micros |
| --- | --- | --- | --- | --- | --- |
| 漏洞正例 | 1 settled | 4 | reviewed候选→verified→限定报告 | 1 | 61210 |
| 修复负例 | 1 settled | 2 | reviewed候选→refuted | 0 | 44690 |
| 业务允许共享 | 1 settled | 2 | business_access_permitted阻止复验 | 0 | 41360 |

合计147260micros，费率为配置的每百万词元10美元保守上界，不是服务商账单。本轮两个不同源码版本共6次真实调用，合计325510micros；每场景每版本至多一次，无自动重发。之前回合的161830micros保留为独立失败矩阵，不从统计中删去。

仅公开构造loopback开发样例进入现有DeepSeek，主库私有资料、身份Cookie和Provider配置没有发给服务。实际浏览器采集后才生成图谱和模型草稿，未预置答案或候选。36份登记原始产物及Run/Scope/Policy/观察/证据/候选/结果事实在finally归档，和manifest、结果、输入、回执、报告的所有摘要再次核对一致。

这是一次开发集合通过；模型仍对反例提出草稿，依靠业务规则及独立复验阻止误晋升。不是低误报模型、三次稳定重放、原生完整链或三域质量验收。前一版本的具体语义失败未溯源，不能声称已证明修复其精确根因。

## 回归证据

- build/research-contract-regression-20261020.log：98 passed/1依赖警告/36.28秒，含7类语义拒绝、settled/零Claim/零Finding、引用不来自节点正文、空输入、Schema跨任务隔离，以及原Worker/模型解析/输入预算。
- build/research-contract-ui-20261020.log：真实API与本机模型HTTP协议夹具，3组11任务17次UI写入、1协议调用、8种显示组合、无页面错误。两种引用错误可读，刷新不重发；这不是DeepSeek实际质量证据。
- 累计完整回归及原生加载结果在本文件后续追加。

## 迁移与回退

无数据库迁移。新增错误字段为可选，旧任务仍可读取。回退需保留其余未提交成果，仅还原本轮动态Schema/语义原因及UI显示增量；不得覆盖数据库、删除证据或自动重试已结算调用。


## 本机App加载

主库只读确认无活动Run/租约后，CUA正常退出；HTTP连接拒绝exit7证明旧服务停止，随后重新启动/Applications/Fieldwork.app。智能体清单、配置研究模型、创建研究组及建立Research Campaign入口可见。主库暂无Campaign导致创建组禁用，页面明确给出建立Campaign的下一步；未在主库运行测试或改写项目。

build/research-contract-native-before-20261020.json与after完全一致，Provider元数据摘要及主库模型调用/攻击记录计数不变，仍为0。与隔离夹具6次真实模型调用分别统计。这是源关联安装App加载和入口验证，原生语义错误行/完整三例链未验收，也未重建独立发行包。


## 累计完整回归发现的跨模块故障与修正

首次完整默认回归：build/contract-full-regression-20261020.log，1591 passed/1 failed/14 skipped/2 warnings，563.48秒，exit1。333源码集合前后相同，before/after快照保留。失败是test_web3_auto_import.py::test_actual_sandbox_disk_limit：共享隔离器新参数export_limit_bytes默认值在模块定义时固定为旧MAX_SNAPSHOT_BYTES；测试将总限额缩小后，默认调用先被误判为超限参数，未启动受限执行。因此本次全量门未通过，不能只报1591绿项。

修正为默认None、调用时读取当前总限额，显式输入仍须为正整数且不超过总限额，不回退目录或扩大权限。已有实际写盘超限测试恢复预期resource_limit且无产物导出；新增显式0/负数/bool/超过当前上限仍拒绝。

build/current-export-limit-regression-20261020.log：126 passed/1依赖警告/44.03秒，包含共享隔离、三种Agent执行、受审阅派发/检查点及Web3导入。付费三例矩阵早于这次限额默认值修正；生产常量下默认解析值相同，但不将旧矩阵SHA冒充修正后源码。后续完整回归和页面保留结果另行追加。


## 限额修正后的页面保留

build/bound-contract-workspace-preservation-20261020.log：真实API三种本机合成测试、摘要精确Run/范围文字、原证据和调用检查点、430宽展开，以及18页/5Agent页签/144显示组合通过，进程exit0，0模型调用。原有组调整/能力申请授予撤销/记忆导入比较/事件筛选/证据关系/候选调查分诊/登记/优先级暂停/备份演练等操作继续通过。


## 最终累计完整回归与原生加载

修正后的默认全量：build/current-limit-full-regression-20261020.log，1596 passed/14 skipped/2 warnings，518.91秒，进程exit0。14项跳过不算通过，两个警告为依赖弃用及刻意重复ZIP条目。先前1591通过但磁盘限额失败的日志保留，不混为同一次运行。

333个backend/static/templates/tests及运行/归档脚本源码文件在最终回归前后完全一致：build/current-limit-full-source-before-20261020.json、after。build/current-limit-acceptance-hashes-20261020.json保存相关最终日志SHA；JS语法和git diff --check通过。

限额修正后再次通过CUA正常退出并确认端口exit7，再启动安装App。原生智能体页及配置模型/创建研究组/建立Campaign入口可见。build/current-limit-native-before/after/final-20261020.json一致，主库Provider元数据摘要、模型调用和攻击测试计数不变，仍0调用；完整回归亦未改变这些摘要。未在主库运行夹具或修改模型配置；原生完整三例/语义错误记录交互未验收，源关联App仍非独立正式分发。

完整15Tasks/8KnownGaps继续开放。三域/保留集质量、同一正例三次干净原生完整链、8/16/32实际质量成本、完整持久Worker及独立Agent行为/真实工具效果、迁移激活、正式签名分发等门槛未由本轮关闭。下一阶段需推进这些尚缺的产品链与验收，不能将1596绿项替代原要求。
