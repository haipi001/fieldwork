# Fieldwork 产品缺口审查

Date: 2026-08-30  
Perspective: 本地独立、授权范围内的真实漏洞研究产品

## 产品结论

Fieldwork 已经不是界面演示：已有五工作区、真实工具链、Scope 门禁、运行状态机、Observation → Candidate → Verified Finding、证据与报告边界、Traditional/Web3 双路径、本地恢复和首次启动诊断。

当前主要差距不是“再加几个扫描器”，而是把技术能力收敛成稳定、可理解、可恢复、可复查的日常工作流。

## 必须补齐的 P0 闭环

### 1. 任务中心，而不只是任务选择器

- 显示排队、运行、暂停、等待用户、失败、已完成。
- 支持并发上限、优先级、取消、重试和单项失败后续跑。
- 每个任务显示当前步骤、用时、剩余预算、最后心跳和阻塞原因。
- 应用重启后自动恢复队列，不把“中断”误报为“完成”。

### 2. 启动前的“实际执行计划”

在用户点击开始前，明确告知：

- 本次将测试哪些面，使用哪些能力。
- 哪些工具可用、哪些会降级、降级将损失什么覆盖。
- 是否包含主动请求、登录态、本地 fork 状态变更。
- 估计时间、请求上限、模型预算、磁盘需求。

用户确认的应是这份计划和冻结的 ScopeSnapshot，而不是一个模糊的“开始”按钮。

### 3. 可展开的实时执行证据

- 八个阶段均可展开，显示测试项、目标、工具、输入、实际状态和结果摘要。
- 区分 `RUNNING / WAITING / BLOCKED / DEGRADED / FAILED / DONE`，禁止“总体完成、子阶段仍 WAIT”的矛盾状态。
- 心跳超时时明确显示“可重试 / 需配置 / 需人工”，而不是无限转圈。
- 默认显示人话进度，原始 JSON/CLI 仅作为专家层。

### 4. 测试身份与会话闭环

已有角色/租户资料和差异矩阵后，还需要：

- 使用 macOS Keychain 保存凭据，数据库只保存不透明引用。
- 受控登录浏览器，可见地采集 Cookie/Token，禁止密钥进入日志。
- 开始前检查会话有效性、过期时间、角色与租户覆盖。
- 产出“哪个角色对哪个对象做了什么比较”的证据。

### 5. HTTP 研究工作台

仅有自动扫描不足以支持 SRC 研究，需要受 Scope 约束的：

- 代理历史与请求/响应证据查看。
- Repeater：编辑后重放、参数对比、响应 diff。
- 两个身份间的对称请求比较，用于 IDOR/越权复验。
- 所有重放继续经过 Scope、DNS/IP、方法、速率与预算门禁。

### 6. OAST 外带验证

- 本地或自托管回调服务，为每个 Candidate 生成短期关联标识。
- DNS/HTTP 回调作为 Evidence，保留时间、原始事件哈希与过期策略。
- 网络不可达时必须标记 `NOT TESTED`，不能当作无漏洞。

### 7. 结果审阅与漏洞生命周期

每个 Candidate/Finding 需要统一工作流：

`new → triaged → verifying → verified / rejected / duplicate / accepted-risk → reported → fixed → retested`

同时补齐：

- 用户备注、标签、指派、严重程度调整理由和审计记录。
- 指纹去重：同一根因、同一端点、跨次运行相同漏洞。
- 修复后一键定向重测，对比旧 Evidence 与新 Evidence。

### 8. 零漏洞也是完整交付

每次 Run 无论是否有 Verified Finding，都必须生成：

- 测试范围、工具版本、时间、预算和会话角色。
- Tested / Not Tested / Blocked / Degraded 覆盖矩阵。
- 每个未测项的原因和补测方法。
- “未发现已验证漏洞”而非“安全”的责任边界。

### 9. Web3 目标对齐向导

链上目标不应让用户自己猜 source/fork 如何组合。需要一个向导完成：

- chain + contract address + block 锁定。
- proxy / implementation 解析。
- 已验证源码或本地 repo/commit 关联。
- compiler/settings/library 对齐与 bytecode 一致性。
- 只读 RPC 检查与本地 fork 准备。
- 对齐失败时禁止把本地结果冒充链上影响。

### 10. 数据与发布安全

- 数据保留期、项目级删除、证据级删除和可验证备份。
- 整个项目可导出/导入，便于迁移到另一台 Mac 后复核。
- 更新前预检、数据库备份、失败回滚、版本说明和迁移报告。
- 一键生成脱敏诊断包，不包含 API Key、Cookie、Token、私钥和未脱敏请求。

## P1 产品增强

- 项目模板：Traditional API、登录 Web、白盒 Repo、Immunefi Contract。
- 基线与变化扫描：只扫 commit/endpoint/bytecode 的变化部分。
- 定时复测与本地通知：任务完成、需人工、会话过期、磁盘不足。
- 采集包导入：OpenAPI、HAR、Postman Collection、Burp XML、Foundry/Hardhat 工程。
- 证据审阅器：请求/response diff、trace、storage diff、screenshot 和 source location。
- 报告字段反向导航：点击缺失材料，直接回到需补证据的 Candidate/测试项。
- 重现包签名和外部验证：在干净本地实例验证 Proof Capsule。

## P2 暂缓项

- 多人协作、云同步、组织 RBAC。当前北极星是单机独立应用。
- 第三方平台自动提交。保持人工预览与导出更安全。
- 无限制自主攻击 Agent。在 Oracle、Scope 和成本边界稳定前不应追求。
- 安全工具市场。先把默认工具链的真实覆盖做透明。

## 成功指标

### 可用性

- 首次安装到成功启动第一个本地测试：不超过 10 分钟。
- 用户无需 CLI 完成创建、运行、暂停、恢复、查看和导出。
- 任务失败后 100% 有可操作原因和下一步。

### 可信性

- 100% Verified Finding 引用 ScopeSnapshot 和 Evidence。
- 0 个扫描器命中直接进入 Verified。
- 100% Run 产出覆盖报告，包括零漏洞 Run。
- 运行状态、阶段状态、报告状态不相互矛盾。

### 安全性

- 0 个原始密钥进入 SQLite、事件、日志或导出包。
- 100% 主动请求通过 Scope/DNS/IP/速率/预算门禁。
- 生产链和公开测试网的写入/签名/广播保持 100% 拒绝。

## 建议的开发顺序

1. 身份与会话闭环 + 任务中心。
2. 启动前执行计划 + 可展开实时证据。
3. HTTP 代理/Repeater + 身份对比 + OAST。
4. Web3 source/bytecode/fork 对齐向导。
5. Finding 生命周期 + 跨次运行去重 + 定向复测。
6. 项目导入导出、数据保留、诊断包和更新交付。
7. 在授权 Traditional 目标和真实 Web3 项目上做发布验收。
