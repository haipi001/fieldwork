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
- OAST 必须由冻结 Scope 显式开启，并为远程自托管回调声明 `oast_allowed_hosts`。每个短期探针绑定 Campaign、真实 Run 和可选假设；token 只存 SHA-256，回调 Header 脱敏后进入 Observation/Evidence/Coverage。探针过期无回调保持 `NOT TESTED`，不能解释为安全。
- “设置与工具”提供自定义 OpenAI-compatible API Base、模型 ID 和 API Key。本机 Key 只写入 `~/.strix/cli-config.json`（权限 `600`），不进入 SQLite、事件、报告或 API 响应。
- Scanner / tool output 必须先成为 Observation，不能直接生成 Verified Finding。
- Candidate 必须通过至少两次独立重放、反证检查、已确认 ScopeSnapshot 和 Evidence 绑定，才能成为 CanonicalFinding。
- 报告编译器只读取 CanonicalFinding；缺字段输出 `MISSING_REQUIRED_FIELD`，不会编造内容。
- 导出只生成本地 Submission Package，不自动登录或提交任何漏洞平台。
- 生产网与公共测试网 Web3 写入失败关闭；写入只允许真实 Anvil local fork / local devnet，不需要真实私钥。
- Web3 链上合约启动前必须生成部署对齐 ProgramSnapshot：本地源码编译、编译器/优化参数、固定区块、Chain ID、EIP-1967 Proxy Implementation 与 Runtime Bytecode SHA-256 均必须一致。不对齐时阻断真实任务；RPC URL 和原始 bytecode 不持久化。
- Heavy tools 是可选能力；缺失时 Settings 显示 `MISSING/degraded`，不伪造可用状态。

## 数据与恢复

- SQLite：`data/src_control.db`
- 报告包：`data/exports/`
- 结构化 Artifact：`data/artifacts/`
- Native Agent 任务隔离目录：`data/agent_workspaces/<run_id>/`（目录权限 `700`，文件权限 `600`）
- v1 Run 在进程异常退出后进入 `paused`，保留 checkpoint；用户点击恢复后从首个缺失阶段继续。

操作与故障恢复详见 [OPERATIONS.md](./OPERATIONS.md)。
