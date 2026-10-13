# 贡献指南

感谢你帮助改进 Fieldwork。开始前请阅读[项目说明](../README.md)和[路线图](../docs/roadmap/README.md)。

## 问题与改进建议

通过 [GitHub Issues](https://github.com/haipi001/fieldwork/issues) 提交可复现的问题或改进建议。错误报告应包括操作系统、Python/应用版本、提交 SHA、复现步骤、预期行为与实际行为，以及经过脱敏的日志。

使用本地夹具或匿名示例描述问题。安全漏洞按[安全报告指南](SECURITY.md)处理。

## 开发环境

当前功能开发基于 `codex/v6-final-control-plane`。从该分支建立范围明确的工作分支：

```bash
git switch codex/v6-final-control-plane
git switch -c your-change
./scripts/bootstrap_local.sh --dev
```

完整自动化测试：

```bash
PYTHONPATH=. .venv/bin/python -m pytest -q
```

涉及工作台操作或页面显示时，准备本机 Google Chrome 后执行：

```bash
.venv/bin/python scripts/check_v5_workspace_ui.py
```

浏览器脚本使用隔离数据库和示例数据。原生应用、外部工具和模型研究质量需要相应范围的独立验收。

## Pull Request

1. 说明要解决的问题、修改后的行为和适用范围。
2. 保持提交主题集中；对行为变更增加有意义的验证。
3. 提供实际执行的测试、结果与尚未验证的环境。
4. 修改用户行为时同步更新指南；文档移动时修正链接和路径引用。
5. 以 `codex/v6-final-control-plane` 为功能修改的目标分支；首页等纯文档维护可针对 `main`。

避免提交数据库、凭据、实际目标材料、运行日志、虚拟环境和本地构建产物。文档使用相对链接；详细验收记录放入 `docs/acceptance/YYYY-MM/`，历史计划放入 `docs/archive/`。首页保留稳定的项目介绍与使用入口。

## 第三方材料

引用上游项目时记录来源及具体版本，保留适用的许可证和声明。设计参考、命令适配和源码引入应明确区分。项目级许可证目前尚未指定；本指南不构成额外的授权或贡献者协议。
