# 冻结监控窗口策略重放回执


## 2026-10-18 冻结监控窗口策略重放回执

继续完整UI-05范围，未把整审计旧验证器用于晋升版本化监控策略线索。新增monitor_window_replay.py与固定monitor_window_replay_worker.py：确认审阅摘要后，在独立Python子进程重算冻结事件的策略判定/信号，2MB/500事件/15秒界限，固定入口与清理环境，不接受命令、目标或自定义程序，不连接研究目标、不调用模型。执行全程持有存储共享门，前后验证来源/审阅摘要及worker、agent_audit两份实现文件摘要；超时/来源变化/维护占用不保存成功回执。结果按窗口幂等持久到完整性校验产物与既有v5_events，记录App/Python版本、实际执行PID、两份源码SHA256。并发重复提交复查后只存一份，刷新只读回执不自动重放。

来源审阅区保留原事件/策略/证据与打开所属监控功能，追加明确确认的执行表单、重放一致/不一致状态和持久回执。独立进程复用相同策略判定函数，只证明记录重算一致，不是独立算法、OS沙箱、实际行为回放或真实性验证。所有结果verification_status=human_review、behavior_authenticity_verified=false、canonical_finding_created=false，不创建正式Finding，不改变原告警处理状态。自动独立复验派发及真实第二来源验证仍待开发，不能据此关闭UI-05或完整目标。

证据：build/monitor-window-replay-final-tests.log为76相关通过/1既有warning（11.95s），覆盖重放/告警/Agent Audit/调度退避；随后新增维护/超时及错误映射后build/monitor-window-replay-final-boundaries.log为8相关通过/1既有warning（2.94s），不可把二者相加为当前独立测试数。真实子进程执行PID不同于测试父进程、实现摘要记录、持久读取/重复不再执行、拒绝旧审阅摘要、工作进程失败/执行中处理状态变化无回执、模拟不一致结果不晋升均通过。build/monitor-window-replay-ui-final.log真实API隔离合成窗口：未确认不提交、实际子进程重放一致、人审边界、刷新找回回执且仅1次执行提交、所属历史/处理/停用及英文4宽度检查通过，0页面错误/模型/正式Finding/系统任务变动。不是实际目标采集或完整独立行为验证。JS语法与git diff --check通过，未重跑当前全仓或全站144组合。

主库0活动Run/执行任务/模型调用/活动监控且登录与告警关闭后，CUA正常退出并curl确认8000端口关闭，再启动来源链接/Applications/Fieldwork.app，health ready，原生监控页/原导航/收件箱保留且关闭。主库没有告警，重放交互证据来自隔离真实API浏览器，不冒充原生真实告警验收。完整15Task/8Gaps、实际三域模型发现/独立复验/报告、通知及正式分发等仍未完成，目标继续；无Git提交推送。
