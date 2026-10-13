# V5-ORCH-02：独立对象读取判定与V5回执

日期2026-10-04；源码0.68.5/build83/schema25。整体Task仍进行中；本增量接通本地已审阅HTTP读取的独立语义判定、回执及限定主张的图结果。

## 变更与信任路径

- `v5_http_transport_child.py` / `v5_http_transport.py`：实际请求子进程从原始JSON提取最多两个指定标量字段的SHA256，不用父进程填写“已通过”字段。凭据仍仅经匿名管道，持久材料脱敏。
- `guided_http.py` / `traditional_runtime.py`：请求前冻结候选指纹、原始证据/观察、三个身份绑定交换、两份身份及配置、Scope/Policy行哈希；原始Artifact中保存引用与摘要，保留十份观察到的传输进程记录。
- `v5_verifier_child.py`：新增`http_object_read_v1`。第二类独立进程禁止联网和应用文件读取，从十份实际观察字段摘要重新计算两轮身份认证、不同主体、归属匹配、同对象响应、匿名拒绝、稳定性及已冻结规则是否允许读取。正例为verified，已拒绝另一主体的反例为refuted，前置不足为inconclusive。严重度、源码根因和更广影响不推断。
- `v5_http_receipts.py`：复用现有Campaign/Graph/Verifier Task/Lease/Receipt。原Artifact按source_ref桥接，不复制响应文件；图输入显式绑定文件SHA。只接受服务器持久的明确审阅任务、十次已记录响应、未取消任务及当前证据/授权。缺少监督执行的旧Artifact不能进入此Oracle。验证前、签发时、读取回执和晋升时重查当前输入。
- `v5_verification.py`：内建隔离Runner领取指定请求，45秒租约、现有重试规则、不可变回执及当前输入判定；计算型HTTP Oracle禁止网络。重试只重新计算已记录证据，**不会再次发送目标HTTP**；要取得新现场观察必须重新进行授权执行。失败、旧输入、取消或租约不满足时不能签发有效回执。
- `guided_research.py` / `static/v5-candidate-workflow.js`：持久任务显示独立结果及回执，读取时刷新当前输入匹配状态。历史回执在材料或授权变化后显示失效提示。
- `tests/test_v5_http_receipts.py` / `tests/test_v5_http_workflow.py` / `scripts/check_v5_http_workflow_ui.py`：实际本地HTTP、沙箱Oracle、回执与图晋升及页面显示验收。
- `version.py` / 当前进度文档 / 本文：版本、迁移与剩余边界。

## 结果边界

只对精确URL、所选两份授权身份、已记录对象字段及有项目依据的业务权限规则作结论。有效回执允许产生V5图`canonical_result`；refuted同时产生一等counterevidence节点和contradicts边。传统`canonical_findings`未自动写入，严重度、源码根因、报告/修复生命周期桥接仍待完成。历史验证说明的是此次观察，不声明后续部署始终相同。

当前仍只支持macOS本地loopback精确端口传输沙箱。候选、凭据与规则由测试夹具供应；未证明真实模型自动发现、完整登录产品操作、外部目标隔离器、全安装App清洁启动或三域闭环。取消前半程材料归档、后端常驻生命周期及全计划安全/规模门仍为待办。

## 测试与迁移

相关专项41项通过；随后加入签发前变化负例。最终全量724 passed/3 skipped，150.83秒，1项已有Starlette/AnyIO依赖弃用提示；新增集成14项。隔离UI检查覆盖回执状态、历史输入变化、已有规则/确认/执行/取消/失败/主题。实际HTTP正例和修复负例各三次独立临时数据库运行（不是三次安装版冷启动）；每次采集3次+执行10次，请求数量未因语义验证增加。七类变化：原始观察、身份状态、Scope行、Policy行、候选主张、Artifact文件和取消任务，均使回执失效并阻断晋升。

无数据库迁移，无生产数据更改，无外部扫描，本轮不覆盖安装App。

## 微基准变化

代表性夹具摘要输入，独立纯语义进程10次，median73.15ms/max92.17ms。相对仅传输切片每个候选增加一次计算子进程，以及输入/回执SQLite检查；HTTP请求数仍为10。此数据不是发现精度、模型成本或8/16/32角色基准。

## 回滚与下一Task

停止活跃任务后可逆向本次Git提交恢复0.68.4源码。无需schema降级；新增Oracle回执保留历史，但旧代码无法验证该Oracle时不得将它重新当成有效授权证明。

继续V5-ORCH-02：监督链与中断材料、限定结果到正式Finding/报告/复测的桥接、真实发现链，以及外部目标隔离执行器。再按完整Master Bundle推进Critic/Synth、领域桥接、持续研究、情报治理、搜索和规模验收。整体开发目标保持进行中。
