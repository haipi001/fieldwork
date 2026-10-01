# V5-SEC-01 安全基线核对

日期：2026-09-30
任务：`V5-SEC-01` — Security baseline before orchestration

## 结论

本任务完成的是 V5 编排接入前的安全基线核对与边界修正，不代表 M1 安全 Alpha 已完成。

- 已把现有 `LocalBoundaryMiddleware` 真正挂载到主应用，并恢复最小 `/health` 就绪探针。
- Host、Origin、Fetch Metadata、重复头、下载、静态资源和 WebSocket 走同一入口边界。
- 已有 `run_isolated` 对不可信项目使用独立快照、清洁环境、无网络 macOS sandbox、文件/输出/超时限制和进程组终止；缺少 sandbox 时失败关闭。
- 会话鉴权与桌面启动握手尚未完成。无 Origin 的本机进程仍可访问业务 API，因此 Multi-Agent Overlay 不得在这一缺口补齐前开放真实编排执行。

## API / Session / Host / Origin 对照

| 项目 | 当前状态 | 证据 | 决策 |
| --- | --- | --- | --- |
| Host allowlist | 已接线 | `local_boundary.py`、`tests/test_local_boundary.py` | 仅接受配置端口上的 `127.0.0.1`、`localhost`、`[::1]` |
| Origin | 已接线 | 同上 | 有 Origin 时必须与 Host 精确同源；唯一例外是精确的浏览器采集事件路径接受受格式限制的 Chrome Extension Origin，并继续由端点 Bearer/配对合同鉴权；`null`、其他跨站与重复 Origin 拒绝 |
| Fetch Metadata | 已接线 | 同上 | 只接受 `same-origin` 或 `none` |
| 下载 / 静态 / SSE / WebSocket | 入口覆盖 | ASGI middleware 在路由与静态挂载外层执行 | 后续新增 Overlay router 继续复用同一应用入口 |
| 最小健康检查 | 已恢复 | `GET /health` 只返回 `{"status":"ready"}` | 不暴露项目、工具、版本、路径或秘密 |
| API 会话凭据 | 未完成，发布阻塞 | `SEC-01` | 每次启动生成；不进 URL、日志或浏览器持久存储；所有业务 API 必须校验 |
| 桌面身份绑定 | 未完成，发布阻塞 | `SEC-03` | 动态端口、启动握手、进程与版本校验；端口预占失败关闭 |

Host/Origin 是浏览器来源边界，不是身份鉴权。后续不得把这层标为 `SEC-01` 完成。

## 不可信执行边界

### 已证明的路径

`isolated_execution.run_isolated` 当前提供：

- 项目复制到权限受限临时快照，拒绝符号链接、超量文件和超量字节；
- 不继承任意环境变量或用户 HOME，敏感变量不进入子进程；
- macOS sandbox 禁止网络，拒绝读取用户目录、其他临时目录和外部卷；
- 禁止 Foundry FFI 与项目声明的额外文件权限；
- `RLIMIT_CORE`、`RLIMIT_FSIZE`、`RLIMIT_NOFILE`，以及运行时长和 stdout/stderr 上限；
- 独立进程组，超时后终止整个进程组；
- 缺少受支持 sandbox 时拒绝执行，不降级为裸跑。

### 尚未统一的路径

- 旧 `adapters.stream_process` 仍继承大部分主进程环境，且取消只针对直接子进程；它不是不可信项目隔离后端。
- `web3_lab.py` 的本地 Anvil 生命周期有终止逻辑，但仍需统一进程组与资源预算。
- 部分只读辅助命令使用 `subprocess.run`，需要逐项归入“可信固定工具”或迁移至统一执行服务。
- macOS sandbox 只是当前可验收后端；分发前仍需决定容器/VM 路线并验证签名、体积和兼容性。

因此 V5 Worker 只能生成研究事实或排队任务，不能直接把模型、网页、源码、仓库说明或工具输出变成 shell、Policy 或 Scope。真实工具任务必须经现有冻结 Scope/Policy 和受支持执行边界。

## Secret 边界

- 测试身份会话保存到 macOS Keychain，数据库只持有引用；前端不回显凭据。
- Provider 密钥走后端受限配置合同，V5 前端提交后清空输入，不写 localStorage。
- 导入、日志、证据和报告继续执行现有脱敏。
- 后续 RuntimeProfile/Worker 只能持有 `secret_ref`，不能把秘密复制到 AgentTask、Research Graph、事件、收据或模型上下文。

## V5 Overlay 接入门禁

1. 在 `SEC-01` 与 `SEC-03` 完成前，只允许只读 schema/API 集成和离线测试；不开放真实多 Agent 执行。
2. Overlay 表必须增量迁移；先备份、在副本演练、验证回滚，不删除旧表，不自动运行历史目标。
3. 历史 Candidate 不自动晋升；Canonical Result 必须绑定独立 Verification Receipt。
4. 所有新 router 必须进入当前受保护 FastAPI 应用，不能另起无边界端口。
5. 不可信输入一律作为数据，不能修改 Scope、Policy、权限或执行 argv。

## 回滚

- 移除 `app.py` 中 `LocalBoundaryMiddleware` 的导入和 `add_middleware` 接线，可回滚本轮入口变更。
- 移除 `/health` 路由可恢复此前行为。
- 本轮没有数据库迁移、任务启动或外部目标访问。

## 下一任务

`V5-SEC-02`（本地实现任务名）：完成启动级 API session 与桌面身份绑定，并覆盖业务 API、下载、SSE/WebSocket、旧 API、过期凭据、端口预占和重启轮换。完成后再进入 `V5-BE-01`。
