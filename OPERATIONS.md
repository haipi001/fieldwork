# Security Research OS 操作与恢复

## 日常流程

1. 在 `/new` 选择 Traditional SRC 或 Web3。
2. 输入已经获得授权的目标。
3. 展开高级设置时配置预算和授权说明；Native Agent 默认启用，但只有模型 Provider 就绪时才会运行。
4. 在人工 Scope gate 复核目标、允许动作和禁止项。
5. 在 Run Timeline 查看阶段、预算与解释性事件；需要时 Pause、Resume 或 Stop。
6. Findings 默认先显示 Verified；Candidate 始终在独立分区。
7. Reports 选择平台，先看完整度，再由用户主动导出材料包。

## 服务恢复

重新启动服务：

```bash
uvicorn app:app --host 127.0.0.1 --port 8000
```

启动时，遗留的 `queued/running` v1 Run 自动改为 `paused`，不会假装仍在执行。ScopeSnapshot、事件与 Checkpoint 保留；Resume 从未完成阶段继续。不要删除 SQLite 来“修复”状态。

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
