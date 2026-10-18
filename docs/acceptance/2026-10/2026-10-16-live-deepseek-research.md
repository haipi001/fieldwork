# 真实公开材料研究调用


## 2026-10-16 真实 DeepSeek 研究调用验收

新增显式环境开关 FIELDWORK_LIVE_DEEPSEEK=1 的独立验收 tests/test_live_deepseek_research.py，默认跳过，避免普通回归收费。隔离数据库、隔离凭据存储、仅发送合成公开图材料，经过真实组队预览/提交/启动、路由、预算预留、独立传输子进程、模型响应解析、用量结算及草稿落图。未替换网络或模型输出。

首次启动因费率为零被已有门拒绝，无推理；保留失败日志 build/live-deepseek-research.log。配置仅用于隔离测试的保守上界 10 USD/百万词元后真实运行：1 passed，6.98s；输入251/输出888，1个settled调用、0正式Finding，模型认为空正文材料不足，只生成5个问题，无假设。费用11390µ为保守费率估算，不是账单。证据 build/live-deepseek-research.json 与 build/live-deepseek-research-retry.log。普通边界回归9 passed/1 skipped（live默认跳过），build/live-research-boundary-regression.log；diff检查通过。主库runtime_calls/runtime_usage均0，没有向云端发送主库研究上下文。

官方价格页 https://api-docs.deepseek.com/quick_start/pricing/ 当前说明旧ID仍接受但由新版服务；本轮保持用户保存的模型ID。不得将模型别名视为冻结权重版本。主库零费率Provider虽然健康，但不能启动云研究，费率编辑及UI提示需继续完善；不声明主库可运行或整体完成。本次不是漏洞自动发现、独立复验、三域闭环、组队收益或三次冷启动验收。
