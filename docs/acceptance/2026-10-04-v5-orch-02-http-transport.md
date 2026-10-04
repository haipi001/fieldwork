# V5-ORCH-02：独立 HTTP 传输增量

日期2026-10-04；源码0.68.4/build82/schema25。Task仍在进行，未达到独立判定、可信回执和正式结果晋升验收。

## 已实现与变更文件

- `v5_http_transport.py` / `v5_http_transport_child.py`：每个已授权GET通过独立stdlib子进程执行；macOS Seatbelt、禁止派生进程/写文件、CPU/描述符/core限制、实际文件读取拒绝探针。
- 父进程先执行Scope/Policy、DNS/私网门禁及原子预算记账；子进程使用父进程提供的数字IP连接，不再按主机名重解析。HTTPS仍用原主机名进行SNI和证书验证。禁止跳转、代理、Host/连接控制头及请求体；响应超过64KiB失败，未截断后作结论。
- 凭据仅经匿名stdin管道进入子进程；原始响应仅在进程内存/匿名管道短暂保留，已有语义检查结束后删除。持久产物仅保留脱敏预览、响应hash和父进程观察的PID/脚本/沙箱校验；不保存凭据或凭据摘要。
- `traditional_runtime.py` / `guided_http.py` / `guided_research.py`：V5明确审阅执行启用此通道，保留逐请求预算、材料/授权变化和取消检查；等待网络时每200ms检查当前状态，取消/超时回收子进程。非审阅旧调用保持现有接口行为。
- `static/v5-candidate-workflow.js`：本地目标和沙箱缺失阻断说明。
- `tests/test_v5_http_transport.py` / `tests/test_v5_http_workflow.py`：真实HTTP、进程隔离、停止、跳转、请求头、响应上限、脱敏和TLS负例检查。
- `version.py` / 本文 / 当前进度文档：版本和边界说明。

## 网络限制与剩余边界

Seatbelt实际拒绝数字IP的`remote ip`规则，允许的host为localhost或通配符。本增量采用localhost精确端口规则，并通过固定子进程脚本连接已检查的loopback数字IP；沙箱允许该端口的loopback地址集合，不宣称单IP沙箱。明确审阅计划只接受loopback字面地址或localhost；外部目标显示阻断，不开放通配出站或静默退回主进程。仅支持macOS，没有无沙箱替代。

`process_execution.scope=transport_only`仅说明请求在观察到的沙箱子进程执行。两轮语义/业务规则判定仍由父进程完成，尚未接入V5任务租约、图输入绑定或V5 Verification Receipt；当前没有正式Finding自动晋升。候选与凭据供应仍由测试夹具构造，不能证明自动发现。取消/失败后现有任务保留已完成响应数量，完整中断材料归档仍待实现。

## 测试与迁移

无数据库迁移，无生产数据更改，无外部目标扫描，无App覆盖安装。专项51 passed；最终全量710 passed/3 skipped，113.01秒；1项已有Starlette/AnyIO依赖弃用提示。新增传输测试11项。隔离V5工作流UI检查通过。首次全量发现旧队列mock接口不兼容，修复调用方式后专项与全量均通过。

## 微基准变化

同机真实loopback GET，各10次，未计发现/模型成本：旧进程内median2.20ms/max12.92ms；沙箱子进程median73.81ms/max94.15ms，median增加71.61ms。子进程按请求启动有明确开销，后续可在保持逐请求授权和取消的条件下评估持久worker。本数据不能替代规划中的精度、证据质量、8/16/32角色和规模基准。

## 回滚

本增量无schema变化，可使用Git逆向提交恢复源码检查点；先停止活跃审阅任务。禁止为绕过沙箱失败而开启外部出站通配或自动降级。App仍为先前0.68.2安装检查点。

## 下一Task ID

继续V5-ORCH-02：冻结HTTP重放输入，独立语义oracle、持久任务/租约、当前图输入与Scope/Policy校验、可信回执和有证据的晋升。随后补全中断材料、正例/修复/共享反例三次清洁运行，以及真实发现链和外部目标安全执行器。
