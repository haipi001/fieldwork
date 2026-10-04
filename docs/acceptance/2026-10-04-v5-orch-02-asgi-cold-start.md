# V5-ORCH-02：真实ASGI冷启动与会话轮换

## 范围与版本

源码保持0.68.14/build92/schema25，本增量仅新增tests/test_asgi_cold_start.py及进度文档，无运行时或schema改动。

三套干净临时SQLite/Artifact数据分别启动两个真实uvicorn进程，完整执行app.lifespan，而不是用TestClient代替后端启动。监听器预先绑定localhost动态端口，传入服务进程，避免检测空闲端口再绑定的竞争。会话令牌与实例标识通过匿名stdin传入；子进程移除PYTEST_CURRENT_TEST及synthetic demo，API不能依赖测试认证旁路。

初始Run/来源Observation/运行中job/过期租约是明确fixture；Artifact来自一次真实localhost隔离HTTP未认证负对照响应，再经实际检查点持久化入口保存。没有真实自动发现声明，没有外部目标或生产凭据。

## 直接验收

- 第一次启动后，传统来源任务interrupted，1/10响应检查点引用与字节/SHA保留；guided任务及执行步骤interrupted，result保留；Run由running转paused。
- V5过期任务由running回queued，attempt保持1，lease_owner/lease_expires_at清空，没有自动消费任务。
- 第一个真实服务进程SIGKILL后，再启动新的服务进程：上述材料和job完成时间仍保持，不重复修改已中断任务。
- 两次启动期间真实HTTP目标调用总数始终1；没有V5回执或CanonicalFinding。
- public health仅返回ready，版本与本次实例标识匹配；业务页面无令牌401，新令牌200。第二次启动拒绝第一次令牌，错误Host/Origin即使带正确令牌也403。
- 默认后台监控/调度循环已启用；每个启动检查Campaign Scheduler第一轮tick完成、running=true、last_error为空、last_processed=0，然后核对任务/HTTP调用没有被自动重放。
- 最终默认循环专项3 passed，6.56秒；共6次真实ASGI启动，其中3次实际SIGKILL后重启。初始关闭后台的专项3 passed/5.54秒不是最终范围。增加退出日志检查后最终全量759 passed/3 skipped，227.58秒；仅既有Starlette/AnyIO弃用警告。

## 限制、回滚与下一Task

后台Agent Monitor与Campaign Scheduler使用默认启用状态，fixture没有活动监控、到期计划或连续研究条目；已覆盖启动第一轮空调度，不证明完整后台周期、真实监控/到期作业、默认安装数据或整个桌面WKWebView链。环境中的其他应用/生产服务和/Applications/Fieldwork.app均未操作，安装检查点仍0.68.2/build80。开发包构建检查点仍0.68.14/build92。

无迁移或性能基准delta；回滚只撤销测试/文档。本验收补足实际ASGI lifespan和会话轮换直接证据，不把App安装启动、其它工具进程树、提交中途退出、三域生产图、模型质量和完整15 Task/8 Known Gaps标为完成。下一Task继续V5-ORCH-02：实际桌面启动/退出、真实发现与跨claim/三域独立验证。
