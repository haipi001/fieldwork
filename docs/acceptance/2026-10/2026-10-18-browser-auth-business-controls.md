# 真实浏览器授权读取与业务共享对照


## 2026-10-18 真实浏览器授权读取与合法共享对照

继续UI-03/QA-07支持类型验收，而非用完整回归代替真实流程。显式开启FIELDWORK_TEST_BROWSER，原真实Chrome两用例先2通过（17.43s），随后扩展为vulnerable/fixed/legitimate_shared三类，确认Scope绑定exact对象业务规则，前两owner_only、合法共享allowlist=['b']。每类实际启动隔离Chrome登录两个本地测试账号、捕获/api/me及对象响应、实际存取测试Keychain会话，guided自动续行发送两轮10次HTTP请求并精确计费10次；finally仅撤销本用例捕获并删除自己创建的测试Keychain账号。build/browser-auth-controls-20261018.log为3通过/1既有warning（25.86s）。冻结摘要build/browser-auth-real-20261018/{case}.json与manifest.json包含实际浏览器/执行版本、测试源码SHA、重放产物SHA及预算，不存凭据。

正例读取reproduced且business_boundary=denied；修复负例not_established且规则仍denied；合法共享读取仍可重现但business_boundary=permitted/access_permitted_by_rule。三类均0正式Finding/0模型调用。本工程用例显式创建候选与观察，不是模型/扫描自动发现，也未走正式晋升、报告、三次清洁启动或安装App真实目标链；不能据此关闭可靠自动发现门。合法共享的无晋升还受既有独立确认门约束，本轮不新建正式Finding。

发现UI只显示“读取行为已复现”可能掩盖共享规则，static/v5-candidate-workflow.js新增统一结果表述：已允许共享优先明确未建立越权；规则未知显示授权边界未确认；规则禁止显示仍需独立确认/影响证据。用于单候选和持续深挖批次，原始重放状态、证据、回执及操作不删改。build/business-boundary-result-ui-20261018-complete.log通过原草稿规则/冻结门/语言保留/执行确认/取消/陈旧计划/回执/正式保存与修复确认/窄屏主题流程，并增加3种业务提示、中英文、提示切换零额外写入及无晋升按钮断言；此脚本使用明确拦截API夹具，不是真实HTTP后端。初始旧脚本未渲染asset_url、未接新增只读预检POST且未关闭新执行计划弹窗、关闭定位器重复，均为验收夹具与当前UI衔接失配，修复夹具后保留原断言通过，未放宽产品门或据失败运行报通过。另build/browser-business-outcome-ui-20261018.log原研究8状态/6证据衔接/4组队前置路由检查通过。

本轮仅产品前端文案及验收脚本/测试改动，后端行为/schema不变。上轮1406完整基线早于本增量，没有宣称重新跑当前全仓。JS语法和git diff --check通过。主库0活动Run/执行任务/模型调用/活动监控、登录/告警关闭后CUA正常退出，端口关闭确认，再启动来源链接App；原生独立复验工作台、运行选择、分诊/深挖入口、候选及回执区保留，主库无候选所以三类新提示以隔离UI证据为准。未注入主库样例或启用采集/研究。完整15Task/8Gaps及所有未完成门继续，无Git提交推送。
