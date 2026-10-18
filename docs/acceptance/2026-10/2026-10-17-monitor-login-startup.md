# 监控登录启动与异常重启验收


## 2026-10-17 监控登录启动与异常重启

新增 monitor_startup.py 与固定前台入口 monitor_login_worker.py。macOS 登录任务绑定当前工作区、解释器、入口及版本，仅处理已启用监控，不自动授权采集或调用模型。预览摘要绑定现有配置与注册状态，确认后私有原子写入 plist 并 bootstrap；停用按固定自有 label bootout，拒绝未知配置及未解决手动进程。执行锁与 management 锁围栏阻止重复进程，仅恢复可证明属于登录任务的崩溃遗留；正常停止不立即重启。恢复备份在登录配置存在时拒绝，防止未来登录写入。

安全监控页保留原入口，新增登录启动/确认/撤销表单、配置与实际心跳分离提示；旧高级预览保留，自动独立复验与告警明确未完成。启动已有登录任务使用 kickstart，不另起手动子进程。

验收：build/monitor-startup-regression-tests.log 为82通过/1既有warning（31.38s）；build/monitor-startup-native-launchd.log 为实际隔离 launchd 1通过（73.54s），验证当前gui域注册、按自有label注入异常退出、不同身份/PID重启、正常停止35秒不重启以及撤销清理，DB字节不变。未测试真实注销/重登录或重启电脑。build/monitor-startup-focused-ui.log 为真实API隔离文件+模拟launchctl的表单启用/撤销及未确认不提交、英文/680宽度检查，2提交/1bootstrap/1bootout/0页面错误/0模型；初次脚本漏 TestClient lifespan导致agent_audits表缺失，修复测试初始化后通过，原失败日志不作通过证据。build/monitor-startup-workspace-ui.log 为18页面/5Agent页签/144显示组合原工作区回归通过；该次不含新增表单点击，不以零字节的controls-final日志作通过证据。JS语法与git diff --check通过，当前全仓未重跑。

主库检查0活动Run/执行任务/模型调用/活动监控且无登录配置后，CUA正常退出App，确认8000端口关闭，再启动来源链接 /Applications/Fieldwork.app。health ready，原生安全监控页显示0.68.75、独立进程未运行、尚未启用登录启动及两个未勾选确认项。没有启用主库登录任务、采集或研究。现有DeepSeek选择继续沿用，凭据未写日志/归档。来源链接App可变代码路径不等于独立签名可信辅助程序或正式分发；当前gui域测试不等于冷登录验收。完整15Task/8Gaps继续，自动独立复验/告警、真实三域模型发现与报告、来源治理、迁移激活及发布门均未整体完成。未提交或推送Git。
