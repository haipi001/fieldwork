# V5-ORCH-02 macOS 本地 Verifier 沙箱进展

日期：2026-10-01
版本：Fieldwork 0.58.0（Build 67 / Schema 23）

内置本地 Verifier 的独立 Python 进程现在必须经 `/usr/bin/sandbox-exec` 启动；沙箱不可用时失败并按原有重试策略处理，不签发 Receipt，也不退回无沙箱执行。Profile 禁止进程 fork、默认拒绝网络；HTTP 状态码 Oracle 仅开放 localhost 出站，包适用性 Oracle 不开放网络。文件策略拒绝用户主目录、外接卷和通用临时目录的读取，再为 Python 运行时与本次暂存目录开例外；文件写入仅开放暂存目录。子进程环境仅保留显式白名单，不继承应用凭据。

Supervisor 保存 Profile SHA-256、子进程 PID/PPID、脚本/输入/输出 hash 和退出码。固定 Verifier 脚本还实际探测并上报：主目录内专用临时 canary 在沙箱外不可读；包 Oracle 的回环 socket 绑定被拒绝。若探针失败，不签发 Receipt。正式晋升门现要求 macOS 沙箱标识、Profile hash 和上述拒绝证据；只有旧 PID 证明的历史回执不再具有当前晋升资格，历史记录不删除。

边界：这是对现有两种固定本地 Oracle 的系统能力收紧，不是通用不可信代码容器。Profile 仍允许系统运行所需的默认非文件/非网络操作，并非完整 deny-default 沙箱；本地 HTTP 仍只证明声明的回环状态关系，包 Oracle 仍只证明扫描产物的精确包版本适用性。通用/远端可信 Verifier、跨平台等价隔离尚未实现，`V5-ORCH-02` 仍未整体完成。

验证：最终全量测试 625 passed、3 skipped；本地 Verifier 与 Intelligence 专项测试 18 passed。Python 编译、diff 检查及未安装的 0.58.0 Build 67 bundle 构建和 deep/strict 签名校验通过。未执行外部目标扫描，也未替换 `/Applications/Fieldwork.app`。
