# 指定 Run 登记资料关联与研究输入隔离验收

日期：2026-10-18。范围为 UI-01/03 的一个增量，完整 UI-00→QA-07、15 Task/8 Gaps 继续开放。

## 实现

- 新建 Campaign 保留名称、目标、策略，增加可选 Run 和只读资料预览、重新核对按钮；不启动工具/模型/任务。
- 仅关联同项目、同研究域、当前已确认 Scope/Policy 的指定 Run；校验注册来源、文件 SHA、维护锁及预览摘要。提交后再核对，变化则事务回滚。
- 资料保持私有；保留有登记来源的反证；无来源记录显示未关联原因。每类最多500条、产物100个、总字节8MB，超限拒绝，不静默截断。
- 研究组仅使用绑定 Run 加显式 Campaign 共享（run_id为空）资料；旧任务冻结了其他 Run 节点时暂停。反证超过20条输入上限时拒绝启动/建组，不丢弃反证。
- 组预览显示选入/可用/未选资料数。不存在自动独立确认或正式 Finding 晋升。

## 证据

- build/campaign-materials-final-regression.log：32 passed；含跨 Run、维护锁、内容/授权变化、上限、事务回滚、旧冻结输入暂停。
- build/campaign-materials-current-ui.log：真实隔离 API 页面错误0，创建请求2（过期摘要拒绝后重核成功），预览4，关联节点3/反证1，模型调用0/启动任务0。
- build/campaign-materials-team-ui.log：3组11任务，页面错误0，含数量、过期预览、语言保留、控制、重载、用量结算和8种显示组合。
- build/campaign-materials-flow-preservation.log：既有项目/证据/研究组衔接保持。
- build/campaign-materials-actual-tool-model.log：实际 Nuclei 登记资料经预览/创建 API 进入真实 DeepSeek，1 passed/1 deselected。source_import 为1产物/4观察/2证据、7节点6边；4个草稿 Claim，0正式 Finding。499输入/3191输出，36900 conservative cost_micros（非供应商账单）。仅公开 loopback 夹具在测试中显式改为public；主库私有资料未外发。该调用在最终跨 Run 输入过滤补丁前完成，同 Run 输入未改变。
- 安装版0.68.76/build154源链接 App 正常退出，端口拒绝连接后重启。原生“智能体→建立研究 Campaign”显示所选 Run 的3产物/3观察/0证据/0反证预览、重新核对及原表单字段。取消退出，未创建主库研究或运行模型。无需替换原生二进制。

## 未完成

登记关系和文件摘要不是独立行为复验；本轮不是完整原生发现到正式报告验收。三域质量、三次清洁启动、独立行为复验、正式报告、真实系统通知、迁移激活与独立签名分发仍待完成。完整回归1406通过是此前版本记录，不作为本次完整源码回归。
