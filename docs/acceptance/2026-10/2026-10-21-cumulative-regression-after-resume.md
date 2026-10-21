# 续跑与恢复增量后的累计回归 — 2026-10-21

## 范围
累计版本包含调度所有权、模型中断租约核对、明确批准的未开始任务续跑、轮转公平性、实际任务进度及中英失败提示。旧1630通过早于这些改动，不能作为本版本默认回归证据。

## 本次直接证据
- 命令 `.venv/bin/python -m pytest -q`，日志 `build/cumulative-regression-20261021.log`；精确进程终态 exit0。
- **1660 passed, 14 skipped, 2 warnings，605.92秒**。警告为Starlette依赖弃用及重复ZIP条目的故意负例。跳过不计通过；未显式启用付费模型/可见实际浏览器模型、原生凭据/系统注册等可选验收。
- 341个 backend/static/templates/tests Python/JS/CSS/HTML文件集合及SHA完全一致：`build/cumulative-source-before-20261021.json`、`build/cumulative-source-after-20261021.json`。
- 命令 `.venv/bin/python scripts/check_v5_workspace_ui.py`，日志 `build/cumulative-workspace-ui-20261021.log`，精确进程终态 exit0。
- 隔离真实API界面：18导航页、5智能体详情标签、8表面及144页面/显示组合，含队伍增减、权限提案/授予/撤销、记忆导入/比较、事件过滤、证据关联、候选调查/分诊、登记、优先级/暂停、备份与恢复演练；零模型调用。
- `git diff --check` exit0。没有生产代码改动、schema迁移、提交或推送。

## 原生与完整产品边界
上一轮已用CUA重启安装中的源码关联App并核对实际中文续跑错误与恢复说明，详见 `2026-10-21-resume-localized-errors.md`；本轮未重复原生完整链。安装版不是独立签名分发包。

本轮关闭累计默认测试与页面保留检查的证据缺口。不能将它计为原15Tasks/8KnownGaps全部完成，不能替代真实三域、8/16/32质量成本、安装版三次清洁启动完整研究、主机真实独立行为、存储激活回滚和正式分发。

## 下一开发
继续UI-03跨阶段受预算控制的研究流程：把可核验草稿衔接为未验证候选，保留来源与反证、当前Scope/Profile/预算以及单独独立复验边界；避免沿用原续跑批准扩大为新阶段授权。现有批量手工审阅、所有18页和高级控制继续保留。尚未实现的自动阶段不能由当前默认套件通过推断完成。
