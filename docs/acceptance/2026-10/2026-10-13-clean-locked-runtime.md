# 独立锁定运行环境验收

2026-10-13补齐上轮验收记录；App 0.68.73 / build151。完整开发规划继续，本文仅覆盖运行环境与已有页面保留。

## 实现与安装

`requirements-macos-arm64-py313.lock` 锁定36个运行依赖及wheel SHA256，适用于macOS arm64 / CPython3.13。`scripts/install_locked_runtime.py` 在私有新目录离线安装，经锁文件快照、wheel摘要复核和pip check后生成清单。缺包、改包、重复记录和目录符号链接拒绝。

安装命令：`python3 scripts/install_locked_runtime.py build/runtime-wheelhouse --result build/prepared-runtime-final.json`。这是准备新环境的命令，不自动切换App。

上轮已构建并安装 `/Applications/Fieldwork.app`，保留 `build/macos/rollback-06873-host-python/Fieldwork.app`。本轮重新读取已安装Info.plist，确认选中的Python与准备清单一致。来源源码仍依赖当前仓库；非独立发行包。

## 验证证据

- 干净环境全量1016通过、3跳过、2警告：`build/clean-runtime-full-suite.log`。此后安装器安全专项4通过：`build/locked-runtime-safety-tests.log`。
- 浏览器18页、144显示组合及相关真实交互通过：`build/clean-runtime-browser.log`。显示组合不代表所有业务组合均验收。
- 上轮原生18导航、重启、供应链版本和无核对项提示通过；本轮未重复原生交互。
- 36组件SBOM离线schema验证通过：`build/locked-runtime-sbom-validation.log`。依赖版本差异和缺包已在选定运行环境清零。
- 本轮只读比较迁移前备份与主库九表的全部原有列/行：全部原有行保留，SQLite quick_check为ok。产物1087→1090，观察1374→1377，其余七表不变。模型调用和agent任务均0。
- 新增记录归属同一传统工具Run，包含httpx、katana、nuclei。日志不能确认触发者，未删除、未归因为用户或测试。不能宣称全表完全不变。详见 `build/locked-runtime-native.json`。

## 仍未完成

wheel摘要不等于安装后全代码完整性或发布者信任。原生二进制/系统库/外部工具完整SBOM、许可证审核、签名公证、干净机器发行验收，以及正式数据迁移/恢复和完整15 Task、8 Gaps继续保留。
