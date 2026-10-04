# V5-ORCH-02：原生App冷启动与桌面后端存活绑定

## 版本、改动与配置

源码0.68.15/build93/schema25。改动macos/FieldworkApp.swift、scripts/build_macos_app.sh、desktop_server.py、capability_registry.py、version.py及专项测试/进度文档，无schema迁移。

构建包从version.py写入版本，并携带FieldworkProjectRoot、FieldworkPythonExecutable、FieldworkPort。Swift从Bundle读取并检查绝对路径、脚本存在、解释器可执行和端口范围；不再写死个人目录或解释器路径。Health、启动参数、环境和导航边界使用同一端口；仍拒绝实例/版本不符的服务。构建时可通过FIELDWORK_BUILD_PROJECT_ROOT/PYTHON/PORT/OUTPUT/IDENTIFIER指定隔离配置，配置写入包后再签名；运行时不通过网页或API更改启动路径。

原生App持有Pipe写端，后端stdin只接读端；desktop_server检查父PID及FIFO后启动uvicorn和EOF守卫。App死亡或正常退出导致写端关闭，后端请求优雅停止，3秒任务退出超时、5秒强制exit125兜底。原生正常退出仍调用terminate。守卫针对本机桌面拥有的后端，不把任意stdin命令解释成应用指令。

## 冷启动实际发现与修复

首次真实WKWebView加载V5页面后，Runtime Readiness与Capability Registry超过前端15秒超时，页面显示部分数据不可用。原工具版本探测串行，多个首次请求还会重复探测。现在inventory以锁共享同一次冷探测、最多8线程并发，缓存从完成时计算60秒；同一工具多个可执行候选共用8秒总预算，返回仍保持SPECS顺序与原可用性判定。

没有把探测失败伪装成成功或放宽前端超时。极端情况下多个慢工具的批次仍可能超过15秒，并不声称任意安装环境下都有15秒硬上限；当前本机真实冷启动修复后直接验证如下。

## 直接证据

- 使用独立源码副本、空数据库、动态localhost端口59142和独立bundle identifier构建实际Swift App；没有复制生产data或覆盖安装App。
- 健康握手0.68.15，原生WKWebView实际URL为127.0.0.1:59142/v5；修复后AX显示“创建你的第一个研究项目”“本机工具已就绪”“工具就绪”，Runtime/Capability失败横幅消失。空图谱提示属于没有项目，不是API失败。
- 核对实际仍在运行且路径匹配的fixture App/Python PID后，SIGKILL原生App；其后端约0.931秒停止。初始shell直接启动的App PID在进入LaunchServices后已终止，因此没有向旧PID发信号，也不把旧PID失效当作SIGKILL证据。
- 修复后由原生App重新启动实际后端，正常Command-Q后原生窗口、后端及health监听均关闭。CUA返回App quit，随后ps和端口检查再次确认。
- 后端专项：真实writer关闭、owner SIGKILL后停止及无父绑定拒绝，3 passed/4.45秒。冷探测共享/并发上限/候选总预算加后端专项5 passed/5.24秒。桌面安全契约测试更新新入口/计算URL并保留令牌不进URL/argv检查，专项12通过。最终全量764 passed/3 skipped，235.21秒，仅既有Starlette/AnyIO弃用警告。
- 开发包实际Swift编译与ad-hoc签名；最终默认包0.68.15/build93、源码根目录为当前仓库、Python为构建时解释器、端口8000，codesign严格验证及zsh语法通过。

## 限制、回滚与下一Task

这证明实际开发构建的原生窗口、Cookie/API加载、冷工具探测及桌面后端存活绑定。没有把/Applications安装版、生产数据历史任务恢复、所有工具进程树、任意第三方代码或提交中途/断电恢复标为已通过。安装检查点仍0.68.2/build80，App仍依赖本机源码和Python，不是独立分发包；没有发布签名/公证。

无迁移；回滚本增量代码/版本并重建即可，历史检查点/回执不改。隔离fixture及其空数据保存在忽略的build/native-cold-start-06815目录，不提交Git。下一Task继续V5-ORCH-02/安全门：安装配置核验、真实发现到独立验证闭环及跨claim/三域证明。完整15 Task和8 Known Gaps保留，整体目标仍进行中。
