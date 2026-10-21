# 2026-10-21 累计版本完整回归

## 范围与判据

完整 UI-00→QA-07、15 Tasks/8 Known Gaps 保留。补齐组内草稿批量审阅、精确候选名单复验衔接、适配器源码历史/未启用恢复之后，重新运行默认完整测试，不能沿用2026-10-20的1596通过作为当前证据。

命令：`.venv/bin/python -m pytest -q`。配置 pytest.ini 指定 tests，pythonpath=backend及根目录；使用当前仓库 .venv，未显式启用付费模型、可见浏览器、原生Keychain、登录任务注册、实物Trivy等独立验收开关。默认跳过不作通过。

源码冻结：backend/static/templates/tests中的Python/JS/CSS/HTML，及真实模型矩阵运行/归档脚本。运行前快照 build/current-full-source-before-20261021.json。测试日志 build/current-full-regression-20261021.log。以最终进程终态和前后SHA核对为准。

## 配套页面证据

前一轮 build/adapter-history-workspace-ui-final-20261021.log：18页、5智能体页签、8表面、144页面/显示组合及既有写入操作通过，exit0、0模型调用。包括适配器双版本、多行源码、430px、并发失效、错误可见、切换Agent清确认、未启用恢复、组件显式源码查看。当前全量过程中不修改源码，最终核对其源摘要与全量冻结集合；不是本轮再次操作原生 App。

## 完整产品门的当前实现核对

- `research_batch_handoff.create` 仍要求对明确选中草稿的预览确认；不会自行生成独立复验或Finding。
- `guided_research.next_automation_input` 有限轮接力只作用于已授权的候选流程；不能冒充从实际采集、模型发现到自动草稿选择与正式报告的跨阶段控制器。
- `v5_continuous.configure` 明确拒绝尚未接通的cloud escalation；持续策略任务并非任意阶段自主执行授权。
- `agent_attack_review.summarize` 明确为同份合成观察的一致性检查，`independently_verified=False`；不能代替真实主机工具效果或独立行为回执。
- 自动保留、迁移激活/恢复替换、远程可验证更新及正式独立签名分发仍有未实现/未验收部分。保留原预览说明和入口，不删页掩盖缺口。
- 三域、保留集合、8/16/32真实模型质量成本、安装版三次清洁启动完整链仍需匹配范围证据。

## 原生边界

本轮 CUA 再次确认 Mac 锁屏；原生新源码加载和完整交互尚未完成。未通过 shell 重启 App，未修改主库模型配置，未运行主库研究；隔离测试不能替代原生验收。

## 最终终态

进程终态exit0：**1630 passed / 14 skipped / 2 warnings / 494.83秒**。无失败。14跳过不计完成；两警告为Starlette依赖弃用和故意构造重复ZIP条目。

339个冻结文件集合和SHA前后完全相同，见build/current-full-source-before-20261021.json与after。配套页面回归的六文件摘要仍与build/adapter-history-source-final-20261021.json一致；没有把源变化后的运行混为同一次。最终日志及快照摘要见build/current-full-acceptance-hashes-20261021.json。git diff --check通过。

本轮补齐累计版本默认回归门，没有新增功能或迁移。源码保留全部18页；真实付费模型和原生完整链等跳过/阻塞范围仍开放。下一开发优先推进UI-03跨阶段受预算控制的自动研究控制器和持久恢复，保持当前材料/授权/独立验证约束，而非继续仅把候选局部接力称为全流程自动化。
