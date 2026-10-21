# 真实浏览器对照之后的独立复验与报告衔接

日期2026-10-19（执行跨越18日）。完整UI-00→QA-07、15Task/8Gaps仍开放。

## 实际缺口与修复

真实浏览器原验收停于普通HTTP两轮对照。继续扩展时发现：candidate状态变成reproduced后，material_for转影响审阅、guided_http.plan拒绝绑定，使受审阅的独立复验入口不可达；历史复用也未区分普通与reviewed隔离执行。

保留默认影响审阅状态，新增显式reviewed_replay路径；只有核对计划并提交authorized和source_fingerprint才允许对已复现候选重建绑定。当前Scope/Policy、账号归属、auth_allowed_hosts、预算、隔离传输与每步材料摘要核对均保留。普通自动流程不自动获得此权限。历史复用在reviewed路径只接受reviewed历史，普通对照不能充当隔离执行。

分诊UI对有可重新核对绑定的影响审阅候选保留“检查复验计划”按钮；计划若显示合法共享、身份/授权缺失等仍阻断。读取、检查计划均不发目标请求；原状态、来源、反证、分页与语言切换保留。

## 真实浏览器到业务结果证据

`FIELDWORK_TEST_BROWSER=1 .venv/bin/python -m pytest tests/test_browser_guided_flow.py -q`，最后产物保存运行：**3 passed，27.09秒**；日志`build/browser-auth-report-artifacts-20261019.log`。另一次相同业务验收3通过/28.08秒不是额外样本量或clean-start质量统计。

| 本地构造场景 | 实际请求预算用量 | 独立判定 | 正式Finding | 报告 |
| --- | ---: | --- | ---: | --- |
| owner_only漏洞正例 | 20（原普通10+审阅隔离10） | verified | 1，显式审阅保存 | HackerOne格式预览正文已保存 |
| 修复负例 | 20 | refuted | 0 | 禁止正例晋升 |
| 合法共享allowlist反例 | 10 | 业务规则在计划阶段阻断 | 0 | 无正式报告 |

实际隔离Chrome登录与Keychain会话输入、真实loopback HTTP对照、network-denied独立进程判定；独立child_pid不同于parent_pid。正例报告继续明确severity unknown、源码根因未检查、更广影响未证实。**候选为显式本地夹具预置，模型调用0；不是实际模型自动发现，不是保留样本发现质量，也不是原生三次清洁启动。**

证据目录`build/browser-auth-report-real-20261018/`（沿用启动日命名）包含各场景JSON、vulnerable/fixed回执JSON、vulnerable-report.md；报告SHA与场景记录一致。未把模型答案、回执、Finding直接写入夹具数据库；正式保存经生产审核API，数据仅在隔离临时库，未创建主库Finding。

## 回归及失败记录

- `build/reviewed-replay-20261019-regression.log`：**78 passed/1 dependency warning，104.64秒**，包括HTTP回执、当前输入变化、计划/授权、定向修复、普通对照后正反例独立回执、guided与triage。
- `build/reviewed-replay-triage-ui.log`：只读分诊、影响审阅复验入口、筛选/分页、语言/失败保留和390/1280主题布局通过，0写入。旧脚本静态模板asset_url未展开，已修测试资源加载。
- 中间实际浏览器运行失败日志保留：最初影响审阅/http_binding阻塞；随后明确auth_allowed_hosts缺少127.0.0.1，补的是本地验收Scope授权，并未放松产品检查。一次Chrome ready超时保留在browser-reviewed-diagnostic.log，确认该session终止后才重试。中间mock函数不支持新可选参数导致的回归已通过默认路径保持原调用签名修复；一次中间回执未完成亦保留原日志，不归为最终通过证据。
- 此前完整1452通过/11跳过是本补丁之前的完整回归，本轮仅以上匹配范围回归，不冒称当前全量再次运行。
- 安装App0.68.76/build154源链接，主库活动Run/租约任务0；正常退出后8000端口关闭，重启工具就绪且独立复验/报告导航可见。原生主库未注入夹具，业务三例在真实隔离API/浏览器链验收，非原生主库完整演示。

## 剩余工作

默认连续研究尚不能据此宣称全自动独立复验；显式受审阅路径现已可达。实际模型发现衔接、三域与保留样本质量、原生三次清洁启动、正式报告完整平台质量、监控独立行为真实性、真实通知及退出App投递、源治理、规模/迁移/正式分发等原条款继续开放。
