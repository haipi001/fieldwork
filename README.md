# Security Research OS

本地优先的授权安全研究工作台。产品规范以 `FINAL/SRC_AI_Security_Research_OS_FINAL_2026-08-27` 为唯一权威。

主线：

`Target → Scoped Run → Observations → Verification → CanonicalFinding → Evidence Capsule → Report Preview → Submission Package`

前端只有五个一级工作区：新建分析、分析过程、漏洞结果、报告中心、设置与工具；顶栏切换 Traditional SRC 与 Web3 / Immunefi。

## 本地启动

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app:app --host 127.0.0.1 --port 8000
```

打开 <http://127.0.0.1:8000/new>。

## 验证

```bash
PYTHONPATH=. pytest -q
python3 FINAL/SRC_AI_Security_Research_OS_FINAL_2026-08-27/scripts/verify_package.py
python3 FINAL/SRC_AI_Security_Research_OS_FINAL_2026-08-27/scripts/static_contract_check.py
```

## 事实边界

- 默认任务运行真实本机工具链；每个测试项分别记录 `tested / not tested / blocked / degraded`，不得用合成结果冒充真实覆盖。
- 默认生产路径为 Docker-free 的本机原生执行：Traditional 使用 Nuclei、Katana、httpx、Subfinder、Semgrep、Gitleaks、Trivy 与 pentest-ai；Web3 使用 Foundry、Slither、Echidna、Medusa 等原生工具。Strix / Shannon 仅作为可选容器 Agent 保留，不进入默认任务，也不影响产品 Ready 状态。
- Native Agent 使用系统 Chrome 与 Playwright 进行只读页面研究，不下载独立浏览器。每个 HTTP(S) 请求均经过不可变 Scope、DNS/IP 和原子请求预算检查；不开放任意 Shell、表单提交、文件上传、下载或状态修改。模型未配置时明确降级，确定性工具链继续运行。
- Traditional Run 内置受控 HTTP 工作台，支持请求/响应历史、编辑后重放、响应 Diff、身份关联和加入 Candidate。所有发送继续通过 Scope、DNS/IP、方法和请求预算门禁；Authorization、Cookie 等敏感 Header 只用于当次请求，持久化时强制脱敏。
- 长期 Research Campaign 保存业务流程、安全不变量、跨身份/跨租户差异、重复重放、研究假设、失败反证和定向复测；单次无结果不会自动关闭长期风险。
- 测试账号登录必须由冻结 Scope 显式开启。Fieldwork 使用非持久化、可见的独立 Chrome 采集授权测试账号会话，并对每个请求执行精确域名白名单与请求上限；不会读取日常 Chrome 配置。Cookie 仅经匿名内存管道写入 macOS Keychain，不进入 SQLite、日志、事件或报告；删除身份和清空记录时同步清理其专属钥匙串项。
- 可逆业务状态测试只允许 `local_fixture / ephemeral_test / staging_clone`，且 Scope、Policy 和每次执行三重确认。每个动作强制执行“前置快照 → 状态动作 → 后置快照 → 补偿 → 回滚快照”，以响应哈希证明恢复；动作前写入持久化 Journal，进程中断后阻断后续变更，并提供补偿恢复入口。生产目标和无补偿的状态动作永远不执行。
- 多步业务流程支持受限 JSON Pointer 提取和 `{{variable}}` 模板传递，可在后续 URL/Body 中使用前序对象 ID；不执行任意脚本。提取值只存在于本轮内存，证据仅保存变量名和 SHA-256。机器不变量支持状态码、JSON 存在/相等/不等和跨步骤响应哈希比较；跨身份序列会冻结源身份对象变量，用另一角色/租户重放并建立待复验假设，不能直接升级为漏洞。
- 流程步骤可显式声明 `requires_steps`，系统用逆序扰动检查前置条件绕过。只读步骤还可声明 `concurrency_safe`，但只有冻结 Scope 显式允许、环境属于本地夹具/临时测试/脱敏克隆时才会执行 2–5 路有界并发。顺序或响应分歧只产生待复验 Hypothesis，不直接生成 Finding。
- 声明式多步流程支持基于前序标量提取变量的条件分支，以及最多 5 次的只读有界轮询。未命中的分支明确记为 `SKIPPED / NOT APPLICABLE`；轮询未收敛会建立长期待证明假设，不会被解释为安全，也不允许脚本或变更型循环。
- 机器不变量可证明数值边界、跨步骤数值增量、列表金额求和、集合成员存在/禁止、集合唯一性和集合数量边界。数值使用十进制计算和显式容差，不执行用户表达式；断言失败只建立可定向复测的 Hypothesis。
- Campaign 流程可声明同项目上游依赖并导入其显式提取变量。规划器按依赖图排序；只有上游多步只读流程的机器不变量全部通过，下游才会执行。原始导入值只在本轮内存存在，Evidence 仅保存变量名和 SHA-256；上游失败或变量缺失时下游失败关闭。
- 含多个可逆动作的隔离流程作为一个事务测试执行：先捕获全部基线，动作正序执行，补偿严格逆序执行，每个动作使用独立持久化 Journal 和回滚哈希证明。任一回滚失败会保留恢复入口并阻断后续状态测试。
- 任务中心和 Coverage Ledger 提供分区清理：只隐藏已结束任务或当前覆盖列表，活动任务、Observation、Evidence、Finding 和报告保留。
- `benchmarks/logic-v2.json` 将复杂业务逻辑验收固定为版本化门禁：同时评分跨租户动态对象、声明前置步骤绕过、金额守恒破坏、稳定并发负对照、健康账本负对照和 Finding 证据门禁。`python benchmarking.py captured-results.json` 可对导出的 Campaign `results` 重复评分；v1 清单继续保留用于历史可比性。
- OAST 必须由冻结 Scope 显式开启，并为远程自托管回调声明 `oast_allowed_hosts`。每个短期探针绑定 Campaign、真实 Run 和可选假设；token 只存 SHA-256，回调 Header 脱敏后进入 Observation/Evidence/Coverage。探针过期无回调保持 `NOT TESTED`，不能解释为安全。
- “设置与工具”提供自定义 OpenAI-compatible API Base、模型 ID 和 API Key。本机 Key 只写入 `~/.strix/cli-config.json`（权限 `600`），不进入 SQLite、事件、报告或 API 响应。
- Scanner / tool output 必须先成为 Observation，不能直接生成 Verified Finding。
- Candidate 必须通过至少两次独立重放、反证检查、已确认 ScopeSnapshot 和 Evidence 绑定，才能成为 CanonicalFinding。
- 报告编译器只读取 CanonicalFinding；缺字段输出 `MISSING_REQUIRED_FIELD`，不会编造内容。
- 导出只生成本地 Submission Package，不自动登录或提交任何漏洞平台。
- 生产网与公共测试网 Web3 写入失败关闭；写入只允许真实 Anvil local fork / local devnet，不需要真实私钥。
- Web3 链上合约启动前必须生成部署对齐 ProgramSnapshot：本地源码编译、编译器/优化参数、固定区块、Chain ID、EIP-1967 Proxy Implementation 与 Runtime Bytecode SHA-256 均必须一致。不对齐时阻断真实任务；RPC URL 和原始 bytecode 不持久化。
- Web3 源码检索会构建合约/继承图、public/external 入口点目录、状态变更面、危险原语索引、候选不变量和按风险排序的研究假设。启发式命中只进入 Observation，必须经本地 Fork/模糊测试/独立复现后才能成为已验证漏洞。
- Forge 属性测试使用结构化 JSON 执行，正常化每个属性的状态、fuzz 轮数、失败原因与反例。失败属性按 Observation → Evidence → Candidate 顺序入库，并保持“需独立 Fork 复现与影响证明”的验证门禁。
- Web3 Property Candidate 可从结果页发起两轮定向 Forge fuzz 复测；两个独立 seed 的属性、失败原因与反例摘要进入 VerificationAttempt / Observation / Evidence。稳定复现只将 Candidate 标记为 `reproduced`，不会绕过 ProgramSnapshot、影响、已知问题、历史审计与反证门禁生成 Verified Finding。
- Web3 语义攻击面会从 Solidity 函数体提取合约级状态变量、入口读写集、内部/外部调用边、低级调用和 guard，并对入口生成风险分数。“同一可达入口内写状态 + 外部交互”会形成优先验证路径和 Observation 假设，用于排序重入、会计与权限分析，不直接宣称漏洞。
- Heavy tools 是可选能力；缺失时 Settings 显示 `MISSING/degraded`，不伪造可用状态。

## 数据与恢复

- SQLite：`data/src_control.db`
- 报告包：`data/exports/`
- 结构化 Artifact：`data/artifacts/`
- Native Agent 任务隔离目录：`data/agent_workspaces/<run_id>/`（目录权限 `700`，文件权限 `600`）
- v1 Run 在进程异常退出后进入 `paused`，保留 checkpoint；用户点击恢复后从首个缺失阶段继续。

操作与故障恢复详见 [OPERATIONS.md](./OPERATIONS.md)。
