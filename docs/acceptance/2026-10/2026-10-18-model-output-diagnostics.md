# 模型输出安全诊断与结构错误暂停


## 2026-10-18 模型输出安全诊断与结构错误暂停

根据上一轮真实DeepSeek格式失败继续UI-01/02任务可诊断性。v5_model_response.ModelOutputRejected新增白名单model_output_reason和model_finish_reason，不存正文、推理、原响应、JSON错误片段或未知服务端finish_reason。解析区分invalid_json、output_truncated、content_filtered、non_text_content、non_object_json、invalid_json_constant、json_too_deep、missing_output；显式length/content_filter即使文本可解析也拒绝，不把截断/过滤当成功完成。用量无效仍保持原unknown/预算保留路径，不借诊断虚构消耗。

研究Worker与结构化Worker保存固定原因码。有效JSON但Pydantic字段/类型失败，仅当该调用已settled时标schema_mismatch且暂停：既有消耗保留，不重新结算，不走普通local_worker_failed自动重试。结算失败仍unknown并保留预算。语义/Scope/证据独立守卫保留，未自动修JSON、移除字段或绕过引用校验。原历史通用错误不倒推具体原因。

智能体任务表保留所有列和操作，主提示改为“模型输出未通过校验”，子行说明截断/非JSON/结构不符等，并明确不自动重试、先核对模型与预算。原因码中英映射，原错误详情与传输错误仍适用；没有凭空推断上一轮真实失败是哪一种。

验收：build/model-output-diagnostics-final-tests-20261018.log为73相关通过/1既有warning（29.62s），覆盖研究/结构化Worker、llama_cpp/Ollama协议、语法/结构/截断、结算正常或失败、1次调用后暂停/刷新与重复tick不重发、累计用量/账本与无正式Finding/无私有输出标记进入API和事件；随后构造器白名单补强与额外单测后build/model-output-diagnostics-final-bounds-20261018.log为19通过/24未选（0.79s），不可相加当独立总数。build/model-output-diagnostics-ui-20261018.log真实API隔离模型HTTP协议夹具：3组11任务原创建/更新/权限/预览陈旧/语言保留/错误读取/刷新/节点/草稿来源等操作保留；真实非法响应得到invalid_json、账本结算，新增截断/结构错误展示fixture切换及不重发；8语言主题宽度组合、0页面错误，1协议请求。不是新真实DeepSeek调用。本轮不额外付费或重放实际扫描，初始旧真实失败只保留历史证据。

Python编译、JS语法及git diff --check通过。完整1406基线早于本改动，当前全仓和全站144组合未重跑。主库0活动Run/执行任务/模型调用/活动监控且登录/告警关闭后，正常退出并确认端口关闭，再启动来源链接/Applications/Fieldwork.app，health ready；原生智能体页5子页/创建前置/模型配置/刷新/Run选择/建立Campaign保留。主库无相关研究组，具体错误操作证据来自隔离真实API浏览器，未注入主库或启动研究。没有schema改变或Git提交推送。完整15Task/8Gaps继续，实际独立行为确认/正式报告/三域与质量发布门均未整体完成。
