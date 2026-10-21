# 冻结浏览器模型矩阵与安全输出诊断

日期：2026-10-20。继续完整 UI-00→QA-07、15 Tasks/8 Known Gaps，未标记整体完成。

## 本轮真实模型验收：未通过

`FIELDWORK_LIVE_BROWSER_MODEL=1 .venv/bin/python scripts/run_browser_model_matrix.py`

唯一矩阵目录：`build/browser-model-frozen-matrix-20261020T113023Z/`。三种场景与预期发现数、源码文件摘要、HEAD（含当前未提交源码摘要）、调用和预算上限在启动前冻结。进程终态 exit1，源码运行前后相同，pytest 2 passed/1 failed，95.81秒。每个场景新的隔离数据库、实际浏览器采集，未预置 Claim 或候选；仅公开合成 loopback 元数据发送到现有 DeepSeek，未发送主库研究数据。

| 场景 | 调用/结算 | Claim | 实际后续 | 正式发现 | 保守 cost_micros |
| --- | --- | --- | --- | --- | --- |
| 漏洞 | 1 / settled | 2 | 审阅候选→独立 verified→限定报告 | 1 | 77660 |
| 修复 | 1 / settled | 0 | finish_reason=stop，但 invalid_json；任务暂停 | 0（归档主事实核对） | 45840 |
| 合法共享 | 1 / settled | 2 | business_access_permitted 阻止复验 | 0 | 38330 |

合计161830 cost_micros，按配置的每百万词元10美元保守上界计算，不是服务商账单。没有自动重发、修改解析器容忍错误或重跑付费矩阵。修复任务未走到独立 refuted，因此不能算修复负例质量通过；原安全门阻止了错误晋升，但不能替代成功完成研究。

## 持久证据

原始 manifest-before、matrix-result、pytest 日志、各场景冻结输入、Claim、回执/报告保留，所有原始文件摘要重新核对一致。

从本次明确拥有的 pytest-124 三个夹具数据库，以只读方式核对 Run、登记 URI 和 SHA 后归档24份原始登记产物（16/4/4）、Run/Scope/Policy、观察、证据、候选、正式发现及预算事实。不复制 Provider、密钥表或身份 Cookie。文件路径必须处于夹具根、无链接、大小不超过2MB、摘要一致，排除Cookie/Bearer等字段。登记证据索引另存 registered-evidence-index.json；这是运行后的补归档，不伪称已在原启动前清单内。

后续运行器已改为冻结全部 backend/static/templates/tests 及归档脚本；唯一目录、防止覆盖旧实验、按确切子进程等待、不自动重试。测试 finally 在清理前归档证据；有用量的失败仍保存并结算，独立 Worker 真正停止后才退出测试数据库环境。

## 输出诊断增量

请求本来已经携带 json_object，因此此次失败的具体语法根因仍未知，没有以假设修复替代证据。

新增拒绝输出时的内容字节数、SHA256、JSON解析行/列/偏移、固定语法类别（未结束字符串、尾随内容、无效转义等）。不保存正文、解析器自由消息或私有字段。两种 Worker 仍严格拒绝，结算已知用量，暂停待明确审阅，不自动重试；JSON或Schema错误不产生Claim/Finding。旧错误没有新诊断时仍可显示。

研究组任务表补充中英诊断文字和位置，保留原有错误原因、用量、控制、草稿及研究组入口。没有新增页面或删除既有页面。

## 当前增量验证

- build/model-output-diagnostics-final-regression-20261020.log：73 passed/1依赖警告/26.12秒，含两类Worker、Ollama/兼容服务、语法/Schema/截断、结算失败、不重发、无私有正文持久化及原输入预算守卫。最初新增断言误引用Handler局部变量，失败日志保留，测试作用域修正后通过。
- build/model-output-diagnostics-ui-20261020.log：实际API及本机模型HTTP协议夹具，3组11任务、17次UI写入、1次协议请求；诊断可见、刷新不重发、8种显示组合、0页面错误。不是实际DeepSeek质量验收。
- build/browser-matrix-archive-regression-20261020.log：3 passed/1警告/3.50秒，真实本机隔离产物的原字节、越界路径和归档替换均拒绝。
- 新脚本/测试AST、JS语法、git diff --check通过。

## 原生App

只读确认无活动Run/租约后，CUA正常退出，HTTP端口exit7，再启动 `/Applications/Fieldwork.app`。原生智能体清单和研究组创建入口可见；主库无可展示此错误的测试任务，因此未声称原生错误行验收。源关联App加载新源码，未重建独立分发包。

build/model-diagnostics-native-before-20261020.json 与 after完全一致：Provider元数据摘要、主库调用/攻击测试计数不变，runtime_calls=0。此处主库0调用与隔离矩阵3次真实调用是两个账本，不混为无付费使用。

## 剩余与下一步

三场景本次质量门失败保留。新增诊断尚未取得实际DeepSeek失败样本；需下一次受控矩阵判断输出问题与波动，不能推断已解决。本次仅一个开发场景集合，非保留集、三次稳定重放、原生完整链或三域质量。8/16/32质量成本、完整持久Worker、独立Agent行为收据、真实主机工具效果、迁移激活、正式独立签名分发及其他原条款继续开放。未提交或推送Git。
