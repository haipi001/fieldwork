# V5-ORCH-02 Local Verifier Process Progress

日期：2026-09-30
版本：Fieldwork 0.55.0（Build 64 / Schema 23）

增加显式 `POST /api/v1/verification/local/tick`：仅领取当前活动 Campaign、已确认 Scope/Policy 内的 `loopback_http_status_v1` Verification Task。服务端启动独立 Python OS 进程，移除继承环境变量，限制输入大小、执行时间与回环 HTTP GET；子进程对正例和负对照各复测两轮，禁用代理及重定向。Supervisor 核对 PID/PPID、退出码、定版脚本 hash 与输入/输出 hash，再将观察值绑定到不可变 Receipt。外部 Runner 自报的回执仍标记 `runner_attested`，不冒充进程证明；内置 Runner ID 不接受公开注册、租约或直接提交回执。

Verification Request 现绑定当前 ScopeSnapshot/Policy ID；变更后旧任务不可领取、签发或本地提交回执。负对照失败产生 `inconclusive`，子进程失败重试而不留下部分 Receipt。HTTP Oracle 仅证明显式声明的本机状态码关系，不证明更广泛的权限漏洞或远端目标安全性；该进程与主应用仍使用同一 OS 用户，不能称为完整沙箱。通用外部 Verifier 路径尚无受信任进程证明，`V5-ORCH-02` 整项仍未完成。

验证：`python -m pytest -q` 为 618 passed、3 skipped（1 条第三方弃用警告），含真实回环 HTTP、独立 PID、负对照失效、越界 URL、Scope 轮换与子进程失败重试测试；`python -m compileall -q` 和 `git diff --check` 通过。`./scripts/build_macos_app.sh` 成功生成未安装的 `build/macos/Fieldwork.app`，`codesign --verify --deep --strict` 通过，包内版本为 0.55.0 / Build 64。本轮未运行真实外部目标扫描、未安装或替换 `/Applications/Fieldwork.app`。
