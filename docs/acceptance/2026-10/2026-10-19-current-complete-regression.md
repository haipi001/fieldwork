# 当前累计代码完整回归与页面保留验收

日期：2026-10-19。代码：HEAD 52187a6416fd82091f814d8f5197db17677326d0 加当前未提交工作区。没有Git提交或推送。

## 直接证据

1. `.venv/bin/python -m pytest -q`：1491 passed、14 skipped、2 warnings，484.76秒，进程exit0。日志：`build/full-current-regression-20261019.log`。
2. `.venv/bin/python scripts/check_v5_workspace_ui.py`：18导航页、5Agent页签、144页面/显示组合通过；包含队伍动态调整、能力提议/授权/撤销、记忆导入/比较、事件筛选、证据关联、调查分诊、注册、优先级/暂停、备份恢复演练。隔离真实API界面、0模型调用。日志：`build/workspace-preservation-current-20261019.log`。
3. backend/static/templates/tests 中314个 .py/.js/.css/.html 文件的集合与字节SHA运行前后完全一致。仅这四目录纳入摘要，不能扩称整个仓库未变化；摘要：`build/full-regression-source-before-20261019.json`、`build/full-regression-source-after-20261019.json`。
4. 日志SHA、代码HEAD与汇总：`build/full-current-acceptance-summary-20261019.json`。`git diff --check`通过。

## 范围与限制

- 本轮未编辑产品源码/UI；验证累计资料导入、草稿审核、候选衔接、输出上限、浏览器投影及历史重采兼容后的默认测试集。页面保留结论只对应上述检查枚举的页面、组合与交互。
- 14项跳过不算完成。测试源码保留显式启用门：真实付费模型、真实浏览器链、Trivy、Keychain、登录任务等；默认测试通过不能替代这些实际验收。浏览器资料链及付费模型此前的专项记录分别保留，不把它们合并成当前冻结版本的完整三域矩阵。
- 两警告为Starlette/anyio弃用与故意构造重复ZIP条目的Python警告，没有忽略测试失败。
- 原生App当前仍受Mac锁屏阻挡；上一轮新补丁的正常重启加载与原生三次干净启动未验收。未请求关闭或绕过锁屏。
- 整体规划保持进行中：UI-00→QA-07、15Task、8KnownGaps。主动隔离攻击派发、独立行为真实性、通知实际交付/退出后台、三域真实质量、8/16/32质量成本、情报源治理、完整迁移激活和正式独立分发等仍需匹配范围的证据。

## 下一行动

解锁后先完成安装App的当前源码加载检查；开发侧继续接通Agent主动测试派发的完整范围/隔离执行/反证/回执链，保持已有记录列表和其余页面。不能以默认回归替代这项功能实现。
