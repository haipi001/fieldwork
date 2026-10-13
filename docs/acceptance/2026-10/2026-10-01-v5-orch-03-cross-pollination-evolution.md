# V5-ORCH-03 跨组胶囊与研究群体演化首轮验收

日期：2026-10-01
版本：Fieldwork 0.59.0（Build 68 / Schema 24，V5 子版本 5）

## 交付

- `POST/GET /api/v1/evolution/transfers`：从同 Campaign、同 Run 的活动研究组向另一个活动组传递单个 Claim 的精简 Context Capsule，包含 key idea、第一类 Evidence/Counterevidence 的 ID 和开放问题，不复制全局聊天或原始 Evidence 正文。服务端校验 Claim 归属、图谱边、当前 Scope/Policy，脱敏并限制大小。传递与目标组的无工具授权 Specialist 任务同事务落库；目标任务在领取、心跳、完成时再次检查 Scope/Policy 与输入图谱快照。相同完整快照去重；输入图谱变化但胶囊文字未变时仍形成新快照，冲突的幂等键失败关闭。
- `POST/GET /api/v1/evolution/populations`：将同组的 2–32 个 Claim 作为一代候选。确定性 Evaluator 保存 novelty、证据支持、反证比率、可测试性、声明的影响线索及已观察成本；逐项保存分数、排名和前 K 选择。下一代必须引用同组上一代已选父代，并以 `derived_from` 图谱边支撑新增 Claim 的谱系。每代及评估快照不可更新/删除；读取时重检当前 Scope/Policy 和输入节点/相关边，变化则标记 `current=false`，父代失效向子代传递。显式新幂等键且不提供父代可在最新代之后重新播种，不复用过期评估。
- 以上仅为研究调度启发式，不是独立验证、实际影响证明或 Finding 置信度；不创建 Canonical Result 或 Finding。声明影响信号来自 Claim 属性，不能解读为已证实的影响。

## 迁移与回滚

新增 `research_transfers_v5`、`research_populations_v5`、`research_variants_v5` 三个表及索引/不可变触发器。桌面启动从 Schema 23 升至 24 时先按既有生命周期创建可校验数据库备份，再进行加法迁移；未回填或晋升历史记录。回滚须先停止新版本，用该启动前备份恢复数据库，再运行旧版本；旧版本不能直接读取已升至 Schema 24 的数据库。不要删除新表来模拟回滚，因为迁移后的新记录会丢失。

## 验证

最终全量 `python3 -m pytest -q`：630 passed、3 skipped（1 条第三方弃用警告）；Evolution/Schema/Orchestration 专项 19 passed。新增用例覆盖同快照去重、图谱变化后新快照、来源组归属、Scope/输入失效、实际本地 Runner 消费、排序选择、未选父代和缺失谱系拒绝、父代失效传递、重新播种、不可变记录，以及模拟 Schema 23→24 的预升级备份。Python 编译和 `git diff --check` 通过；0.59.0 Build 68 未安装 bundle 构建、版本及 `codesign --verify --deep --strict` 通过。未执行实际外部目标操作，未覆盖 `/Applications/Fieldwork.app`。

## 仍待完成

目前没有自动生成下一代的 mutation/combine Worker，也未将排名/谱系接入 V5 UI。跨组传递只建立待执行 Specialist 任务，不替代独立 Verifier。正式宣称 `V5-ORCH-03` 整体完成前，仍需验证真实 Worker 的消费链、UI、跨轮资源/预算以及更多长期运行样本。
