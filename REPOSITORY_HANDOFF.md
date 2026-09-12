# 接力开发与同步

GitHub： https://github.com/haipi001/fieldwork （私有）

此仓库为 2026-09-12 从原工作区建立的源码快照，包含未提交的开发成果、测试、完整计划和交接文档，不包含生产数据库、日志、凭据、构建产物和原仓库历史。

先读 HANDOFF_TO_NEXT_AI.md、DEVELOPMENT_MASTER_PLAN_2026-09-11.md、APPLICATION_ASSESSMENT_2026-09-11.md。原机器旧工作区继续保留；这个仓库是独立同步副本，后续编辑应明确选用同一 checkout，避免两边改动分叉。

website/dist 是公开产品介绍网站的完整静态源码，不连接应用后端；其中流程图为示意，无真实目标数据。Sites 注册清单位于 website/.openai/hosting.json。当前网站由原工作区 website 的独立 Sites 仓库发布；以后接手必须复用同一 project_id 并按 Sites 流程同步源码，不重新创建站点。

GitHub 当前默认私有，接力 AI 需要你的 GitHub 授权或本地 clone。网站设为公开，不意味着源码仓库也公开。

没有附加开源许可证；仓库同步不自动授予公众使用许可。
