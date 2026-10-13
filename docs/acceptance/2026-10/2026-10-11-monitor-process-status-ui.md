# 独立监控进程状态入口

2026-10-11，UI-05增量，整体未完成。

复核发现仓库已有agent_monitor_service.py独立只监控进程，以及 `/api/v1/agent-audit/monitor/service` 状态接口；旧工作区UI关于驻留完全未实现和退出必停的笼统说明需要纠正。现在监控后台生命周期面板并行读取maintenance和service，展示stopped/starting/current/degraded；状态接口失败明确unavailable，不用旧状态冒充正常。运行时显示PID、版本、最近心跳和错误。说明独立进程仅处理已启用监控、App退出后是否继续取决于独立进程；在线不等于完整覆盖或已确认漏洞。

保留原监控按钮、策略编辑和研究配置入口；进程安装/启停、自动独立复验与告警派发仍未接通，未来配置仍标预览。本轮未安装/启动/停止独立监控，也未扩大权限。

`build/monitor-process-ui-tests.log`60相关测试通过，1既有弃用警告。`build/monitor-process-ui-browser.log`18页及原操作通过，5种服务显示状态通过，零模型调用。5状态用隔离HTTP响应fixture验证显示，不是实际驻留/心跳/错误故障演练。随后仅把运行字段JSON改成人类可读表格，JS语法/diff检查通过，未重复全部浏览器。8显示组合仅为设置布局。

本轮源码UI增量未原生重载；运行App仍0.68.71此前页面。没有真实DB变更、Git提交推送。独立服务安装和UI管理、App退出续采集实际验收、复验告警闭环、原规划质量发布继续未完成。
