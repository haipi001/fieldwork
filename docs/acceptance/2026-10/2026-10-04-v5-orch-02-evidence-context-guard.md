# V5-ORCH-02：证据集合及所选claim反证变化使回执失效

日期2026-10-04；源码0.68.13/build91/schema25。

## 变更文件与实现

traditional_runtime.py、v5_http_receipts.py、tests/test_v5_http_receipts.py、version.py、当前进度/完整规划审计及本文。

来源回放不再仅要求原证据引用是当前集合的子集：传统候选证据ID集合必须与捕获时一致；guided只允许加入本次实际复验生成的那一条证据。新增、删除、重复引用及不合法ID均使回执失效。新完整Artifact在写入前分配并保存result_evidence_id，后续持久证据必须匹配该ID、Run、Artifact、实际结果Observation、摘要与极性。历史guided材料没有marker时，只接受唯一的同Artifact实际生成记录；没有开放任意附加证据。

所选HTTP claim同时绑定其contradicts关系上下文摘要。新增反证关系、改变相关节点或关系内容会使当前回放输入失效。refuted回执由canonical_for_receipt生成的自身反证，在同request/receipt、原样类型/状态/标题/正文/属性/空关系属性下可保留；修改派生正文或关系属性不再被豁免。该检查针对所选claim的显式contradicts关系，不宣称已推断其它claim、候选或整个项目中未关联的反证含义。

所有V5当前输入、晋升计划、机器收据绑定和报告证明导出复用这个入口。历史回执记录及CanonicalResult不删除，不回填证明；读取时current_inputs_match/promotion_eligible变false。元数据集合顺序不改变逻辑集合，重复ID仍拒绝。

摘要型反证允许没有Observation引用，其自身evidence行摘要仍纳入新捕获的source refs；旧回执保持失效，没有通过重新捕获修改旧收据。

## 验证

- 业务边界/回执专项39 passed（75.28秒）：原正/负例和修复谱系继续通过；新增证据、删复验证据、重复引用、公开API新增contradicts边均使旧回执失效，工作流刷新显示不可晋升。
- 正式保存后追加候选反证或新增claim反证边，正式计划和证明附件导出拒绝旧收据。人工声明仍不能替代新的独立回执。
- 派生负例反证正文/关系属性变化专项2 passed（4.04秒）；改动前原回执eligible，变更后refuted记录保留但当前输入/晋升无效。
- 回归基线751 passed / 3 skipped（209.43秒），仅既有Starlette/AnyIO弃用警告。全量运行后期追加了nullable Observation捕获处理及对应断言；最终该分支专项2 passed（4.69秒），没有将它称为已重跑最终全量。其余代码在该全量开始前已确定。

本地HTTP fixture账号/候选为给定输入，没有自主模型发现、生产图验收或外部目标扫描。证据/边查询增加本地DB和摘要核对，不增加目标HTTP或模型请求；没有新的模型成本/精度基准结论。

## 迁移、回滚与下一Task

无DDL/schema迁移，增加的JSON marker/context字段兼容既有读取；未全面验收全部历史库迁移。无生产数据修改或App覆盖安装，安装检查点仍0.68.2。逆向本提交恢复0.68.12，但回滚会恢复旧子集验证能力，不能声称仍具有新证据集合守卫。

下一Task仍V5-ORCH-02：实际主进程退出与安装冷启动、真实发现链、跨claim/三域反证上下文和独立验证。完整15 Task/8 Known Gaps、情报治理/依赖固定、模型与规模基准和发布安装门仍未完成；整体目标保持进行中。
