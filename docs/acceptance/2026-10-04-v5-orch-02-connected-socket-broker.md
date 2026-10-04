# V5-ORCH-02：授权连接代理与禁止新建网络的HTTP进程

日期2026-10-04；源码0.68.8/build86/schema25。

## 改变下一步的实测证据

Seatbelt不接受数字IP的remote-ip规则，但实际macOS实验确认：deny network*的子进程仍可使用父进程明确传入的已连接TCP descriptor，新建连接会得到PermissionError。初次试验发现父进程timeout socket的非阻塞状态会被继承，子进程须重新设置timeout；修正后继承连接与拒绝新连接同时通过。此机制允许去掉loopback出站例外，不需要开放通配网络。

## 变更文件与边界

- `v5_http_transport.py`：可信监督器只按已有network_guard核验的数字IP连接。使用非阻塞连接等待和200ms取消/当前输入检查，超时或取消关闭descriptor。Popen仅pass_fds传入这一条连接，其余应用/数据库descriptor关闭；随后父进程关闭自己的副本。子进程profile为deny network*，没有outbound例外。
- `v5_http_transport_child.py`：核对继承连接的peer IP/port；实际尝试新建连接并要求OS拒绝，再在继承连接上执行固定GET。HTTPS仍校验原主机名及SNI，未禁用证书检查。无DNS重解析、跳转、代理、Host覆盖或请求体；保留文件读取拒绝、资源限制、64KiB响应限制和凭据匿名管道。
- `guided_research.py`：计划不再按“仅loopback”阻断。原Scope/Policy、认证域名、业务规则、两份会话、预算、当前材料、macOS沙箱可用性等门继续执行。
- `v5_http_receipts.py`：新传输证明要求single_connected_socket及实际network_connect_denied；保留历史loopback精确端口观察兼容，历史材料本身仍须符合V5原始输入/回执守卫。
- `tests/test_v5_http_transport.py`：真实服务使用连接、URL主机不可解析时仍使用已核验数字连接、TLS无效证书拒绝、禁止新建连接、取消释放descriptor等。
- `docs/MASTER_BUNDLE_PROGRESS_AUDIT_2026-10-04.md`：保留全部15个Task的每条done要求、8类Known Gaps、现有入口和缺失证据，防止以本地切片代替完整目标。
- `version.py` / 当前进度文档 / 本文：版本与验收。

## 测试、基准与迁移

相关真实HTTP/工作流/回执专项47 passed；传输与session/Host/Origin专项62 passed。新TCP连接拒绝探针在每个成功请求前执行；TLS自签名负例未发送HTTP凭据；取消关闭进程和连接。最终全量729 passed / 3 skipped（152.31秒），仅既有Starlette/AnyIO弃用警告。

旧测试把192.0.2.1 TEST-NET地址作为“必须禁止非loopback”的直接helper输入，迁移后触发一次8秒TCP连接超时，没有启动HTTP子进程或发送凭据。此用例不符合新授权代理合同，已移除；新的不可解析主机及连接限制测试均使用本地服务，没有用真实外部目标替代验收。

真实外部目标尚无授权现场验收；本增量证明连接能力与本地安全边界，不声明已完成外部生产链或真实发现。HTTP请求数仍为每候选两轮10次，额外probe被OS拒绝，不增HTTP请求。基准侧改变网络授权机制，没有新增模型成本或发现质量结论。无schema迁移、生产数据修改或App覆盖安装。

## 回滚与下一Task

逆向本提交可恢复0.68.7源码。此前版本只能校验loopback_exact_port证明，会拒绝single_connected_socket传输证据进入其HTTP Oracle，不得人为修改证明标签以恢复晋升。

继续V5-ORCH-02：旧HTTP finalize=True进程内确认迁移、完整中断材料、真实发现及跨域独立验证。随后按完整15个Task/Known Gaps逐项取得与要求匹配的直接验收，整体目标仍进行中。
