# 实际扫描工具到真实DeepSeek研究草稿


## 2026-10-18 实际扫描工具到真实DeepSeek研究草稿

继续QA-07工具/模型衔接，不以分别可用代替连接验收。新增显式付费开关tests/test_live_tool_model_research.py，复用实际Nuclei deep/受控GET broker/真实loopback正例与修复服务，原始产物SHA核验后按同项目同Run生产bridge函数投射观察到图谱。只有构造loopback夹具节点被标记public并明确allow_cloud_context；不发送主库目标/用户资料，凭据复用现有DeepSeek后端设置，保存在隔离secret_root，不打印或归档。

工具用例新增可选real_run单工具模式：直接构造当前已确认Scope/Policy下queued且非synthetic的隔离Run及预算，然后真实调用工具API。它不是完整App启动/完整管线完成；Run未被伪标completed。原默认工具用例行为保留。新增用例开始时无Claim，实际Nuclei输出不替换、不注入模型答案/Claim，研究组通过真实预览与确认创建，实际Worker/模型路由/账本执行，唯一研究任务无工具权限。模型输出草稿仍经既有引用校验，0正式Finding。

实际结果：首个有效模型调用输出JSON不合法，任务暂停、usage settled，425输入/1450输出/18750micros保守费用上界；failure-pytest-13.json与原失败日志保留，未据此签成功草稿。显式一次诊断调用正例成功，417输入/2292输出/27090micros，4条draft Claim、9个问题，内容明确检测级观察不足以证明实际行为影响；build/actual-tool-model-20261018-diagnostic.log为1通过/1未选（16.97s）。修复负例单独执行成功，312输入/601输出/9130micros、0Claim，build/actual-tool-model-20261018-fixed.log为1通过/1未选（10.17s）。不是同一命令两用例全绿，更不是无失败稳定率。三次记录用量费用上界合计54970micros，以10USD/百万token保守换算，不是账单/官方定价。

保留build/actual-tool-model-20261018/两份原工具产物、两份任务冻结输入、正例/修复报告与失败摘要及manifest；原工具字节SHA匹配既有Artifact，报告包含输入hash/来源观察ID、模型草稿/引用/问题/用量、非晋升边界。诊断仅输出finish_reason、字符串长度与SHA等，不保存原始模型正文或推理段；成功草稿是实际结构化产出，和原始模型传输内容分开。没有真实行为独立复验、负向复现、正式Finding/报告、保留样本或三次清洁启动；两构造夹具不能代表三域生产发现率、多Agent质量或原完整验收门。

开发中的初始失败如实保留：产物kind应为traditional.tool.nuclei、观察引用为raw_ref；旧默认演示Run被编排门拒绝；暂停Run被broker拒绝；均修正验收夹具状态/真实记录定位，没有放宽生产守卫。实际调用JSON失败后不自动重试，诊断仅是显式新验收调用，未删失败来宣称可靠。

build/actual-tool-model-regression-20261018.log为16相关通过/2显式付费测试默认跳过/1既有warning（24.49s）；末尾只增补可选验收产物留存语句，不影响默认路径，不用本轮局部回归宣称当前全仓1406。产品代码/UI/schema本轮未改变，不需再次安装/重启；已加载Apphealth ready。主库0活动Run/执行任务/模型调用/活动监控、登录/告警关闭，无主库样例或研究；付费账本均在隔离验收库。完整15Task/8Gaps继续，无Git提交推送。
