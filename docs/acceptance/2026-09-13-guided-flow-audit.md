# 第18节当前验收对照

日期：2026-09-13。此表保留原范围，部分链路通过不等于整节完成。

| 要求 | 当前证据 | 结论 / 下一步 |
| --- | --- | --- |
| 一键开始、自动衔接 | guided_followup随启动事务保存；tests/test_guided_followup.py覆盖无页面后台衔接、重启、并发与容量等待 | 后台衔接通过；完整新建目标UI流程待复验 |
| 五步清晰、持久保存 | guided_research_jobs与步骤状态；tests/test_guided_research.py覆盖取消、重启、结果恢复 | 后端通过；最新全部状态需800/1024/1440与原生窗口复验 |
| 候选材料自动补齐且实际使用 | guided_capture/HTTP计划；真实Chrome本地双账号→Keychain→两轮HTTP Oracle | 已支持对象读取权限专用链路；更多响应结构、歧义消除与业务影响仍有缺口 |
| 正例、正常反例不误晋升 | tests/test_browser_guided_flow.py真实Chrome正反案例；不创建canonical finding；测试条目与浏览器清理 | 浏览器/后台链路通过；仍需默认UI逐步点击验收 |
| 继续深挖有增量 | tests/test_guided_pages.py真实本地HTTP首页→下一页→不重复，预算扣2 | 同源无查询链接读取通过；候选ID分页已支持（205条三批实测）；验证队列可按3/3/1续接七条；更多材料分页与自动多轮编排待补 |
| 范围、预算、停止和恢复 | 网络范围检查、限速、请求意图检查点、任务去重测试 | 已有基础；所有验证器与恢复情形尚未整体审计 |
| 简洁UI、专家折叠 | 既有候选与账号默认折叠；0.33.1真实页面检查 | 最新原生完整主流程、键盘及三个宽度待验收 |
| Web3专用复测 | 原有Web3能力，自动整理保持隔离条件缺口 | 第18.6步骤4与相关隔离验收未完成 |

## 本轮发现的真实运行缺陷

- 旧钥匙串交互写入命令读回不一致：已改为Security.framework辅助程序，并完成实际增删改读及HTTP验证器读取测试。
- 浏览器完成采集时最后响应偶发遗漏：响应回调改为有限队列，收到完成指令后短暂等待网络空闲，再读取已观察响应体；真实Chrome正反案例均通过。页面始终轮询时最多等待3秒，不无限等待。

## 可复现命令

```sh
FIELDWORK_TEST_BROWSER=1 .venv/bin/python -m pytest tests/test_browser_guided_flow.py -q
FIELDWORK_TEST_KEYCHAIN=1 .venv/bin/python -m pytest tests/test_session_keychain.py -q
FIELDWORK_TEST_BROWSER=1 FIELDWORK_TEST_KEYCHAIN=1 .venv/bin/python -m pytest -q
```

浏览器测试仅连接动态端口的127.0.0.1夹具，使用独立可见Chrome和临时SQLite/产物目录；测试完成清理自建Keychain项目。不涉及真实外部目标或日常Chrome资料。以上证明浏览器与后台接口链路，不替代Fieldwork页面真实点击验收。

全量执行结果：252 passed in 69.49s；本地App0.33.6/build40已安装，签名、运行版本和数据库完整性通过。
