# 本机请求边界与简洁恢复提示

状态：开发工作区已实现；未打包、安装或发布。对应 SEC-02 第一层、SEC-09 部分响应头及 UX-04 连接恢复。未改数据库结构和用户项目。

## 问题与改动

原服务接受任意 Host/Origin。新增纯 ASGI 中间件，统一覆盖旧/新 API、页面、静态文件、下载、流式响应和 WebSocket。仅接受配置端口上的 127.0.0.1、localhost、[::1]；Origin 必须与请求的 HTTP 本机 authority 完全一致。拒绝重复 Host/Origin 和跨站/同站非同源 Fetch Metadata。拒绝发生在路由前，不读取数据库、不执行动作、不回显攻击输入。

响应增加 no-sniff、禁止嵌入、限制 object/base 与 no-referrer；非静态响应禁止缓存。CSP 仅是部分加固，尚未清除内联脚本和事件处理器。

默认端口 8000。自定义端口时必须同时设置 FIELDWORK_PORT 与 uvicorn --port；未提供通配或远程地址开关。没有 Origin/Fetch Metadata 的本机 CLI 请求仍允许，因此此层不是身份认证，不能抵御本机程序伪造请求。SEC-01 启动凭据及 SEC-03 桌面身份握手仍待实现。

新增 /health 只返回 ready，不查工具、数据或凭据，也不提供服务身份保证。

UI 在断线或来源拒绝时才显示一条常驻提示和“检查连接”；平时隐藏。检查仅发送 GET，成功后读取最新状态，不自动重试创建/启动/修改请求；保留输入草稿。网络中断提示明确操作结果尚未确认。现有页面、专家设置和导航保持原有布局。

## 验收证据

- 原有 153 项回归 + 最初 35 项边界测试：188 passed，55.44s。
- 随后新增真实 app 路由防护接线用例；边界模块再次运行：36 passed。未重复运行其余未受影响测试。
- static_contract_check.py：PASS，64 tasks。
- node --check static/final.js、Python 编译与 git diff --check 通过。
- scripts/check_local_boundary_ui.py：独立 Chrome 验证 800/1024/1440px 无横向溢出；失败检查保留提示、成功检查隐藏；目标草稿保留；无写请求重发。API 使用浏览器拦截的模拟响应，不访问生产 API；已实际查看 800px 截图。
- 未执行真实外部扫描、Forge 项目或生产数据迁移。

UI 复验启动命令（单独测试端口，不启用数据库 lifespan）：

```sh
FIELDWORK_PORT=8917 .venv/bin/python -m uvicorn app:app --host 127.0.0.1 --port 8917 --lifespan off
.venv/bin/python scripts/check_local_boundary_ui.py
```

此服务器仅供上述拦截 API 的 UI 测试，不作为工作服务使用。截图写入 /tmp/fieldwork-boundary-{width}.png。

## 依赖、限制和回滚

本轮不提升产品支持等级。API 会话凭据、动态端口/进程身份、Forge 隔离、完整前端 XSS 审计、自包含打包仍未完成。原生启动器的旧版本探活未改变。工作区代码须后端重启、页面重新加载后生效；未重启正在运行的用户服务。

无需数据库回滚。若需撤销，单独移除 app.py 的 LocalBoundaryMiddleware 注册/import 与 /health，移除模板 connectionIssue 区块、恢复 api helper 和相关连接处理、移除 reading-layout.css 末尾对应样式。保留此前未提交开发成果，禁止整文件 git checkout/reset。本轮新增 local_boundary.py、边界测试和 UI 检查脚本可单独移除；测试客户端 base_url 可保留。

下一阶段：先完成启动会话凭据、受限桌面交接与统一 API 鉴权合同，再做受控执行隔离；保持错误按需展示，避免为安全功能堆叠设置卡片。

## 本地 App 同步（2026-09-12 21:30）

用户已要求同步到本地 App。已更新运行时与原生 Info.plist 为 0.32.2 build 33，构建并通过 codesign --verify --deep --strict，安装至 /Applications/Fieldwork.app，重启完成。此前“未安装”描述为本轮开发阶段历史状态。

重启前确认 Traditional/Web3 活动运行及 verification jobs 均为 0。旧应用、版本文件和 SQLite 一致性备份位于 build/backups/local-app-20260912-212951；备份 integrity_check=ok。

验收：原生窗口可进入新建分析并显示原有项目；runtime OpenAPI 与 App 均为 0.32.2；新版 UI 资源加载；/health=200，直接连接伪造 Host/外部 Origin 均=403。未启动分析任务。仍为开发签名与工作区依赖安装，未宣称公证或自包含。
