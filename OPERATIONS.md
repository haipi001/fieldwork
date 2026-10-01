# Security Research OS 操作与恢复

## 日常流程

1. 在 `/new` 选择 Traditional SRC 或 Web3。
2. 输入已经获得授权的目标。
3. 展开高级设置时配置预算和授权说明；Native Agent 默认启用，但只有模型 Provider 就绪时才会运行。
4. 在人工 Scope gate 复核目标、允许动作和禁止项。
5. 在 Run Timeline 查看阶段、预算与解释性事件；需要时 Pause、Resume 或 Stop。
6. Findings 默认先显示 Verified；Candidate 始终在独立分区。
7. Reports 选择平台，先看完整度，再由用户主动导出材料包。

## 本地环境

首次安装或依赖更新：

```bash
./scripts/bootstrap_local.sh
```

需要验证干净重建时：

```bash
./scripts/bootstrap_local.sh --recreate --dev
.venv/bin/python -m pytest -q
```

## 服务恢复

桌面 App 会生成一次性会话并启动服务。仅做 CLI 维护时，可以显式提供临时会话：

```bash
FIELDWORK_SESSION_TOKEN="$(openssl rand -base64 48)" \
FIELDWORK_DESKTOP_INSTANCE="manual-cli" \
uvicorn app:app --host 127.0.0.1 --port 8000
```

业务 API 必须通过 `X-Fieldwork-Session` 或桌面 App 的 HttpOnly Cookie访问。不要把 token 放入 URL、shell 历史、普通日志或浏览器存储；上例更适合临时受控终端，日常使用仍应从桌面 App 启动。

启动时，遗留的 `queued/running` v1 Run 自动改为 `paused`，不会假装仍在执行。ScopeSnapshot、事件与 Checkpoint 保留；Resume 从未完成阶段继续。不要删除 SQLite 来“修复”状态。

V5 AgentTask 使用持久租约。服务重启只回收已经到期的租约：有剩余尝试次数的任务重新排队，最后一次到期则明确失败。不要直接修改 `agent_tasks` 或 `runner_registry_v5`；需要人工恢复时先备份数据库，再调用受会话保护的 `POST /api/v1/orchestration/recover` 并检查审计事件。

V5 Verification Receipt 与其 binding 是不可变事实。不要关闭触发器、直接更新 Receipt，也不要把 Runner-attested 环境误称为系统级证明。需要重新验证时调用 Receipt replay 创建新 verifier task；旧收据继续保留，并由新收据表达新的事实。

V5 Runtime Provider 创建后必须先运行健康检查，只有 `healthy` Provider 会参与路由。Cloud endpoint 仅允许公共 HTTPS；Local endpoint 仅允许显式端口的 loopback。Provider 密钥位于 `data/runtime-secrets/` 的 owner-only 文件中，数据库和 API 只有引用/已配置状态。备份、诊断和证据导出不得收集该目录；轮换或清除应使用 Provider API，不要手工复制密钥。

## 数据备份

停止服务后复制：

```bash
cp data/src_control.db data/src_control.backup.db
```

恢复前先保留当前数据库副本，再替换目标文件。报告 ZIP 和 Artifact 目录应与数据库一并备份。

每次数据库 Schema 升级前，应用会先在 `data/backups/` 使用 SQLite Backup API 创建权限 `600` 的一致性备份，并保存 SHA-256、应用版本、Schema 版本和 Git commit 清单。快速回滚到最近一个已校验版本：

```bash
./scripts/rollback_last_version.sh
```

回滚在存在运行/暂停任务、备份校验失败或 Git 工作区有未提交修改时拒绝执行；执行前还会创建一份 `pre-rollback-safety` 安全备份。

## Web3 local fork

Forge/Anvil 安装于 `~/.foundry/bin` 时会被自动探测。系统创建本地上游链和真实 Anvil fork；写入只发生在 fork RPC。生产网、公共测试网写入以及真实私钥都会被拒绝。

## 能力降级

Settings & Tools 显示实时能力状态。Native Agent 使用系统 Chrome，不需要 Docker；Provider 未配置或浏览器不可用时会记录为 not-tested，原生确定性工具链仍正常完成。Strix / Shannon 是可选 Docker Agent，不进入默认运行。Coverage Ledger 会把所有缺失或策略拒绝分支记录为 degraded / not-tested，而不是生成空的成功结论。

## 安全事件排查

- `out_of_scope`：目标未包含在当前不可变 ScopeSnapshot。
- `destructive_action_not_approved`：ExecutionPolicy 未授权状态变更。
- `third_party_active_test_denied`：第三方主动测试未显式允许。
- `production_write_forbidden` / `public_testnet_write_forbidden`：Web3 网络写策略阻断。
- `human_review`：重放、反证、Evidence 或真实 Oracle 门槛未通过。
- `draft_incomplete`：报告缺少平台 required fields；查看完整度清单，不要补写虚构事实。

## V5 结构化 Worker 恢复

从 0.62.0 开始，显式本地 Worker tick 会先回收过期租约；重启时仍有效的租约不会被抢占，之后可在 Evolution 页面选择“运行或恢复本代 Worker”。组暂停、并发已满或费用预算耗尽时保持待领取；耗尽最大尝试次数的任务保持 failed。

外部 Critic、Synthesizer 与 Evolver Runner 提交重试结果时，须设置 `lease_attempt` 为领取响应中的 `task.attempt`。首轮结果仍兼容省略字段；后续省略或提交旧 attempt 会返回 409。本地 Worker 自动处理此字段。过期执行迟到的结果与错误清理不能覆盖新的租约。

Evolution Advance 会为保留父代预留下一代的 32 个候选容量，返回 `omitted_combinations` 说明因容量未创建的组合数。历史超量批次 Collect 返回 409，需要显式重新播种符合容量限制的群体；历史任务和提案继续保留。
