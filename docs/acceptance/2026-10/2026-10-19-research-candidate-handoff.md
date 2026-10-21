# 研究草稿到未验证候选的受审阅衔接

2026-10-19。完整UI-00→QA-07、15Task/8Gaps继续开放。

## 实现与保留边界

智能体“审阅草稿与原始材料”中的Claim增加“审阅为未验证候选”。只接受成功完成任务、当前确认授权下的Traditional Run、活动且拥有该Run的Campaign，以及图谱冻结摘要和所有登记来源/有界文件SHA均当前一致的材料。queued Run可保存未验证材料，与既有研究组准入一致；后续复验仍受原Run可执行状态门限制。

支持引用映射回同Run的原观察/证据，完整保留所有第一类反证ID与原polarity。原候选API只从观察新建supporting证据，本入口独立处理，避免把原反证改成支持。只有缺少对应既有支持证据的原始支持观察才登记新支持行，携带原artifact引用；原反证原行不修改。

目标只能从支持观察中、且通过当前read范围校验的地址选择。多个目标需显式选择；分类由登记观察类别确定，无法唯一确定时research_hypothesis，不能凭模型文本打开专用执行。保留模型假设、scope、limitations与来源任务/Claim及反证引用。

预览指纹绑定上述材料、提案与Scope/Policy正文；提交须显式确认。共享存储锁、BEGIN IMMEDIATE、插入后再次源核对，变化回滚候选/新证据/来源事件；同Claim和目标幂等，不覆盖已有改动。只创建candidate，不执行模型/工具/请求/复验、不生成Receipt或Finding。原草稿、来源、同Run候选衔接和全部原页面保留。

## 验证

- `build/research-candidate-handoff-final-current.log`：**45 passed/1 dependency warning，14.03秒**。包括转换/来源核验/Worker/云路由及真实HTTP协议模型夹具。覆盖原反证保留、幂等、未授权、文件变化、scope变化、反证遗漏、目标越界、任务未成功、Campaign归档、多目标显式选择和插入后材料变化的完整回滚。
- `build/research-candidate-handoff-ui-final.log`：真实隔离API，3组11任务、17次fixture写请求、page_errors为空。历史草稿明确为手工保存夹具，登记原始文件后实际预览；文件变化后提交409/无候选，恢复并重新审阅后保存1个未验证候选，0正式Finding、未增加模型协议请求；原研究组控制、重载、8显示组合、同Run候选/跨项目拒绝继续通过。不是实际模型成功输出证明。
- 最后活动Campaign/项目归属检查新增于该UI运行后，最终45项覆盖归档拒绝；不把相邻版本结果混称完整当前原生业务验收。
- 开发中一次文本替换误改create分支缩进，首次UI运行报IndentationError，日志保留；修复并py_compile、上述45及UI通过，未放松任何材料校验。

## 实际DeepSeek验收：尚未通过

仅公开构造loopback Nuclei夹具，使用现有DeepSeek v4 flash。两个有界研究调用均失败后暂停，**0 Claim、0新候选**：

| 调用 | 输入/输出tokens | 保守cost_micros | 结果 |
| --- | --- | ---: | --- |
| 原输出约定 | 510 / 1854 | 23640 | JSON字段/类型不符，schema_mismatch，已结算 |
| 增补确切JSON Schema及长度限制 | 827 / 3438 | 42650 | finish_reason=length，output_truncated，已结算 |

合计66290本地配置的保守cost_micros，非供应商账单。第一次保留在`build/actual-model-candidate-handoff-20261019/`及同名log；第二次在`build/actual-model-candidate-handoff-schema-20261019/`及同名log。第二次减少任务token/费用上限（7000/76000），未放宽验证器、未截取不完整JSON冒充成功、未自动重新调用。当前系统提示加入ResearchOutput实际JSON Schema与limitations/open_questions长度约定，但**第二次未成功，不能宣称解决真实模型稳定性**。

两次都实际取得Nuclei产物并进入模型协议；未得到合法研究草稿，因此新的候选衔接未在实际模型成功输出上验收。后续必须解决当前模型配置/输出预算下的稳定性并继续真实链验收，不把受控输出测试当作实际发现质量。

## 安装及剩余目标

安装0.68.76/build154源链接App正常退出、8000端口关闭后重启，工具就绪与原智能体/独立复验/报告导航可见。主库分析/Agent/guided/verification活动数均0；未注入夹具或执行主库研究。主库没有已保存草稿可验证新按钮，功能行为证据来自隔离真实API界面。未提交推送Git。

新转换仅支持Traditional当前登记来源；原Web3/Agent入口不删除。实际模型稳定草稿→候选→独立验证→报告、三域质量/保留样本、原生三次清洁启动、长期自动化/通知、源治理/规模/迁移/正式分发等继续开放。此前完整1452回归在本补丁之前，不能当作当前全量。
