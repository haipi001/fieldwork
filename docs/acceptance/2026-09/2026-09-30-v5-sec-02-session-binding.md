# V5-SEC-02 启动会话与桌面身份绑定

日期：2026-09-30

## 已实现合同

- 后端每次进程启动使用 `FIELDWORK_SESSION_TOKEN`；桌面 App 以系统随机源生成 48 字节随机值，通过子进程环境传入，不进入 argv、URL 或日志。
- WKWebView 在加载应用前安装仅会话期、HttpOnly、SameSite=Strict 的 `fieldwork_session` Cookie；页面 JavaScript 无需也不能读取凭据。
- CLI 可使用 `X-Fieldwork-Session`，无 Origin 的请求也必须提供凭据。
- `/health` 是唯一公开桌面探针，只返回 ready；实例 ID 与应用版本位于响应头，桌面必须同时匹配才加载页面。
- 端口上若存在旧版、非本次启动或伪装服务，桌面失败关闭，不把其页面载入 WebView。
- WebView 只允许 `http://127.0.0.1:8000`；外部主框架链接交系统浏览器，其他嵌入导航拒绝。
- 浏览器采集事件端点保留独立 Bearer/配对身份，不错误要求桌面 Cookie；其 Origin 仍由精确路径和扩展 ID 格式约束。
- `/health` 以外的 UI、静态资源、旧 API、V1 API、下载、SSE 和 WebSocket 统一经过 Session middleware。

## 安全性质

- Cookie 与 CLI Header 同时出现、重复 Cookie、错误值和旧进程值均拒绝。
- 比较使用 SHA-256 固定长度摘要和 `hmac.compare_digest`。
- 直接运行 uvicorn 且未显式提供 token 时，后端生成不可知随机 token，业务 API 默认不可访问，避免开发启动意外暴露。
- pytest 只在当前请求确实带 `PYTEST_CURRENT_TEST` 时使用现有集成测试兼容旁路；独立 middleware 测试不使用旁路。

## 实际进程证明

在临时端口 8123 使用固定测试 token 与实例 ID 启动真实 uvicorn：

- `GET /health` → 200，响应头返回 `desktop-fixture-instance` 与当前版本；
- 无凭据 `GET /api/v1/engagements` → 401 `session_required`；
- 正确 `X-Fieldwork-Session` → 200；
- 正确 HttpOnly Cookie 等价请求 → 200；
- 验证结束后服务正常关闭。

测试使用非秘密 fixture 值，未访问外部目标或正式数据写接口。

## 尚未声称完成的发布项

- 本轮未覆盖安装 `/Applications/Fieldwork.app`，避免在整体 V5 尚未到发布阶段时覆盖用户当前版本。
- 尚未做真实旧进程占用 8000 的安装版 UI 截图验收；逻辑由实例/版本双匹配和 Swift 编译/源合同测试覆盖。
- 动态端口仍未实现；当前严格绑定 8000。规划中的动态端口可在自包含发行阶段继续收口。

## 回滚

移除 `SessionAuthMiddleware` 接线、`session_auth.py` 以及 Swift 的会话 Cookie/实例检查即可回到此前本机来源边界；不涉及数据库迁移。

## 下一任务

`V5-BE-01`：在保留本安全门禁的前提下，以增量表和可回滚迁移接入 Research Graph。
