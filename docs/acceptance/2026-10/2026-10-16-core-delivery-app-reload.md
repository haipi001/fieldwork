# 核心增量与安装App重载

## 2026-10-16 核心增量组合与安装App重载验收

用户目标：继续完成整个文件夹开发规划并安装App，UI不缺失页面功能。
组合：核心API、assisted研究、合约路由/固定Fork上下文、工具线程存储生命周期、模板合同、实际Nuclei正反例、存储访问门共198通过、1既有Starlette警告（85.51秒），build/core-delivery-regression-20261016.log。不是全仓全量回归。
重载前：主库无queued/running研究、无已租用Agent任务、零runtime_calls；保留1条queued但无租约Agent任务，模型Provider/Profile仍均0。原生0.68.75/build153源码关联安装，正常退出后原进程终止；CUA重新启动/Applications/Fieldwork.app，主进程79493及其desktop_server子进程79509、127.0.0.1:8000监听归属一致，启动参数为当前仓库；health ready。本次PID仅当时进程证据，后续不得据此发信号。
原生UI：实际逐页点击18个导航，无缺失导航/加载错误；项目页标题按当前项目显示，监控和模型页标题按产品命名显示。智能体六页签（清单、安全、能力、记忆、MCP/供应链、攻击测试）可选择且未见加载错误，留在清单。研究项目/Campaign/Run绑定、配置模型与创建研究组入口保留；当前未选Campaign时显示建立Campaign前置提示，不创建夹具数据或研究任务。
数据：重启前后九类业务表计数及逐行摘要全部一致，quick_check=ok，无运行研究/租用任务/模型调用，build/core-delivery-native-before.json与build/core-delivery-native-after.json。快照初次脚本字段/表名不符已按实际schema修正，未写业务库。
交付边界：近期显式模板、合约真实路由与固定上下文、取消线程锁源码已通过源码关联服务重启加载；未重新打包独立发行版或改变版本号，不代表签名公证/干净机器安装。真实本机Anvil与Nuclei工具已有受限验收，实际模型发现→独立验证→影响报告、多Agent研究收益、长时恢复、迁移激活回滚与发布门仍开放。未Git提交推送，完整规划继续。
同步状态：当前目标、操作、证据及最终边界同步八个记忆入口，无秘密或真实研究目标信息。
