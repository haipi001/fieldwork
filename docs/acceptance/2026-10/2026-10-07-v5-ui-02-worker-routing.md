# UI-02 本地结构化 Worker 路由增量（2026-10-07）

## 当前范围

本轮接通既有 critic/synthesizer/evolver 的本地模型调用与 Runtime 路由/用量账本。尚不是 UI-01 新建 research-worker 队伍的完整执行器，也不代表云研究、自动发现或 UI-02 整体完成。

## 实现

- critic/synthesizer 创建 API 可绑定 immutable RuntimeProfile，路由不能覆盖任务绑定 Profile；引用不存在时拒绝。
- 本地 tick 通过实际 Runtime route 选择服务，调用前复核租约、图输入、授权和 Provider 配置 SHA；保持敏感内容本地。
- 路由持久化所选 Provider 配置 SHA。成功返回结构化模型输出后，先持久记录调用用量，再做语义结果校验；被语义拒绝的调用也计入 Runtime 账本。
- 模型生成上限写入 OpenAI-compatible max_tokens 和 Ollama options.num_predict；提交总词元超过路由上限时拒绝。
- 新 critic/synthesizer 任务时长预算 1–45000ms，绑定幂等身份；返回超限结果拒绝图写入。HTTP socket timeout 使用该预算，返回后再复核总耗时。
- 本地模型禁用代理及全部重定向，严格使用无凭据、显式端口、无 query/fragment 的数字 loopback HTTP 地址。

## 直接验收

命令：python3 -m pytest -q tests/test_v5_workers.py tests/test_v5_runtime.py tests/test_v5_evolution.py tests/test_v5_continuous.py tests/test_v5_team_plan.py

结果：39 passed。覆盖真实本地 HTTP 模型协议 fixture：错误首 Provider 不被使用，指定 Offline Profile 调用正确 Provider，路由词元上限 100 生效，11/7 用量持久化，Profile 覆盖拒绝。该 fixture 不是实际模型推理质量证明。另覆盖语义拒绝仍保留调用用量、运行时长超限不写图、幂等配置冲突、Ollama 参数以及重定向目的地未收到请求。git diff --check 通过。

## 迁移与回滚

无 DDL/schema 变更，配置写入既有 JSON；历史未绑定 Profile 保留默认本地路由，缺时长字段按 45000ms。未改写历史回执，未更换安装版。回滚需按本轮差异恢复 v5_workers.py、v5_runtime.py 相应段；保留此前 UI-01/UI-02 未提交成果，不可整文件盲目 reset。

## 未完成与下一步

- 原生 App 本轮调用账本操作验收尚未进行；前包原生配置证据不能代替本轮执行验收。
- 实际模型推理、云执行、research-worker 队伍角色与自动发现尚未完成。
- 原子预算预留、小时词元、跨重试累计、并发调用和崩溃期间未知用量仍需持久执行门。
- 当前 socket timeout 和返回后耗时拒绝不是严格墙钟终止；持续缓慢传输/取消需要监督器边界。
- 非 JSON、网络超时或进程崩溃可能已经消耗词元但无法取得用量，本轮不会伪造已精确结算证明，后续需 unknown/reserved 账本处理。
- 本地有凭据的服务与 localhost/HTTPS 尚不属于此内置传输支持范围；阻断不会默默换用其它 Provider。

下一增量继续 UI-02 durable 调用预留与监督器，再接 UI-01 research-worker，保持完整计划范围。


## 后续监督器增量

上述 socket timeout 的严格终止缺口已取得新的慢响应、实际取消与监督器 SIGKILL 证据，见 `2026-10-07-v5-ui-02-model-supervisor.md`。未知用量、服务端推理是否停止、durable 预算预留仍未闭合。
