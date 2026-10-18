# Web3 Contract Auto-Import 本地验收（2026-10-14）

## 当前结论

P0 地址解析、公开源码真实下载、工程生成、精确编译和 CLI 已完成；增量接入 App 导入、持久任务、缓存/重试/取消、源码字段交接和只读 Run。已安装并启动 `/Applications/Fieldwork.app` 0.68.74/build 152。

工作分支 `codex/v6-final-control-plane`；基线 HEAD `a7c9c29c42ca1907cae542ffd08e20aa8ad31731`。本次源码仍是未提交改动，未推送 Git。开始前的 16 个前端/验收改动已备份到 `build/web3-auto-import-baseline/`，其他并行前端改动保留。项目根没有指定 prompt，实际完整读取 `/Users/lizekai/Downloads/fieldwork_web3_auto_import_codex_prompt.md`。

## 实测

| 项目 | 实测结果 |
| --- | --- |
| 源码获取 | 已实测成功：指定主网目标 Sourcify API v2，27 文件，PSM |
| Foundry 工程生成 | 已实测成功：多文件路径、manifest、原 Standard JSON、Foundry 配置 |
| 编译 | 已实测成功：Forge 和 solc Standard JSON；0.8.30+commit.73712a01、optimizer 1500、viaIR、Osaka |
| 代理检测 | 已实测成功：离线 EIP-1967/Beacon/1167、循环、非合约、错误链和未知代理测试；真实主网未验证 |
| 字节码对齐 | 已实测成功：真实编译的离线 fixture 完整匹配与真实差异阻断；immutable/CBOR 差异证据测试；指定目标因无 RPC 未验证 |
| CLI | source、build 已实测成功；align 已实际执行，缺少 RPC 返回 chain_unchecked/退出码 2 |
| UI | 已实测成功：4 个中英文/宽度导入交互组合；完整 18 页面 × 6 宽度 × 2 主题 = 216 布局通过 |
| 单元/集成测试 | 最终完整 1108 passed、3 skipped、2 warnings，301.74 秒；专项及隔离/发行相关 64 passed |
| 真实联网测试 | 已实测成功：Sourcify 查询及公开源码下载；Etherscan Key/RPC 缺失，备用源在线查询与链上只读对齐未验证 |
| App 运行 | 已实测成功：隔离原生 App 点击下载/编译/启动只读研究；已安装正式路径 App 从终端启动、health=ready、原生工作台可见 |

隔离原生验收使用 `build/web3-native/data/` 与端口 8025，实际完成 1 个 Run、1 个 Observation、1 个证据产物，0 个 canonical finding，未调用模型、未 Fork、未发送交易。错误键盘输入产生的草稿也只在该隔离目录，未迁入用户数据库。`build/web3-native/acceptance-summary.json` 保存实际数据库计数。

官方 solc 二进制通过 `ethereum/solc-bin` 官方列表 SHA256 校验：`738dcdc6afddeb505ee4e4ef24f1c1fdba2b8c924e614cbbf5801a5b062dd683`。Foundry forge/cast/anvil 1.8.0，CPython 3.13.7。构建命令按指南实际执行。安装沿用旧 App 可用的锁定运行时，保持代码关联当前工作树；旧 App 保存在 `build/rollback/Fieldwork-0.68.73-before-web3-auto-import.app`。

前两轮完整回归分别发现：原生发行工具还引用已移动文档（已修复）；监控子进程与测试进程在版本号变更期间不一致（固定版本后完整回归通过）。最终 `git diff --check` 和 Python 编译检查通过。

## 修改文件

| 文件 | 用途 |
| --- | --- |
| `web3_contract_import.py` | 地址/RPC 校验、Sourcify v2、Etherscan v2、路径限额、缓存、工程、官方精确编译器、隔离双编译、固定区块代理、matcher |
| `web3_import_api.py` | Session/Engagement Scope API、持久任务、幂等、取消/重试、附件/快照、Finder 工程打开、只读材料 Run |
| `scripts/web3_auto_import.py` | 与 App 共用业务服务的真实 CLI |
| `app.py` | 注册路由、关闭时等待有界导入任务 |
| `final_core.py` | 浏览器 URL 不再被误判仓库；保存推断 chainId；只读模式保留 Scope 和 Run Budget |
| `web3_analysis.py` | 旧部署对齐 API 增量 matcher 证据、RPC 页面校验和错误脱敏，完整匹配门禁保留 |
| `isolated_execution.py` | Standard JSON stdin，工作区总磁盘/文件量监控和超时/限额阻断 |
| `static/web3-auto-import.js` | 真正后端按钮、轮询、错误、源码交接、RPC 即时校验、只读切换、打开工程 |
| `static/v5.js`、`templates/v5.html`、`static/v5-locale.js` | 现有执行弹窗增量接线及中英文，保留既有修改 |
| `tests/test_web3_auto_import.py` | Parser/providers/sanitizer/proxy/matcher/API/Scope/Session/缓存/重启状态/凭据保护/真实编译/只读预算/沙箱超时和磁盘限额 |
| `scripts/check_web3_auto_import_ui.py` | 真实仓库资源的按钮交互、宽度、中英文、RPC 和 HTML 注入验证 |
| `scripts/prepare_native_release.py` | 修复先前文档移动造成的测试失败 |
| `version.py` | 0.68.74/build 152，无数据库 schema 变更 |
| `docs/guides/web3-auto-import.md`、`docs/README.md` | 中文操作、命令、错误处理、边界及文档导航 |

## 未完成/未验证

- 指定主网目标的代理解析、固定区块部署对齐、实际 Fork 与深度研究：未提供用户 RPC，未执行。
- Etherscan 在线备用源：未提供 Key；响应格式及错误分支已离线测试。
- immutable/metadata 的 explained_equivalence 只输出证明来源区间和哈希，仍保持人工审查；没有足够独立部署证据时不自动晋升 aligned。
- Diamond/非标准代理、缺失的精确依赖、旧式库链接及非 Solidity 工程需要人工处理。
- Finder/LaunchServices 冷启动在本机仍曾卡在 Python 读取/导入阶段；`.venv` 和锁定运行时均试过。没有扩大系统权限。已验证可复制启动方式为先正常退出，再运行 `/Applications/Fieldwork.app/Contents/MacOS/Fieldwork`。不能把终端启动成功写成 Finder 冷启动通过。
- App 仍关联当前源码与已有 Python 环境，未完成独立打包、公证或发行验收。

## 复现与证据

使用命令见 [Web3 自动导入指南](../../guides/web3-auto-import.md)。实际日志：`build/web3-auto-import-source.json`、`build/web3-auto-import-build.json`、`build/web3-auto-import-align.json`、`build/web3-auto-import-tests-final.log`、`build/web3-auto-import-regression.log`、`build/web3-auto-import-ui-final.log`、`build/web3-auto-import-all-pages.log`、`build/web3-auto-import-app-runtime-build.log`。这些属于本地验收证据，不应混作发布包或用户秘密备份。


## 0.68.75 / build 153 自动化优化复验

用户要求“还有可以优化的地方吗？把自动化程度做到最高”，随后两次要求继续。

- 执行弹窗自动准备公开源码，不再要求手动点击获取；通过受会话/项目 Scope 保护的 latest 接口恢复已完成或进行中的任务。完成的缓存结果不重复抓取/编译，中断可继续，取消不自行重启。
- API auto 模式在明确 chainId=1 且启动环境配置 ETHEREUM_RPC_URL 时自动选择只读对齐；不向 UI 返回凭据，不猜公共节点。
- 只读 Run 增加真实源码结构分析。隔离原生复验实际产物为35合约结构、254入口、179调用关系、113状态变量、19待审查假设；没有调用模型、Fork、交易或漏洞晋升。
- Python 改用 -I 隔离初始化，再显式加载受信 desktop_server；避免启动时从项目目录搜索标准库。测试包和正式 /Applications/Fieldwork.app 都通过 LaunchServices 冷启动，health ready，原生工作台可见；本机0.68.74的Finder启动阻塞已被这次实测结果取代。旧0.68.74 App保存在build/rollback/Fieldwork-0.68.74-before-automation.app。
- 最终全量1115 passed/3 skipped/2 warnings，336.13秒。源码导入/会话/并行自动准备组合专项73 passed；桌面/启动/Web3专项72 passed。Web3自动准备与恢复4布局、所有18页216布局、项目自动准备及组队自动化浏览器验证通过，没有真实模型调用。
- 中间全量失败分别为旧会话测试写死 -P 命令、异步排队测试立即检查启动事件。前者更新隔离启动契约并保留token不进入argv/URL/页面的检查；后者等待明确的启动事件，不改变生产调度。最终全量全部通过。

实测日志：build/web3-automation-regression-complete.log、build/web3-automation-combined.log、build/web3-automation-ui-complete.log、build/web3-automation-all-pages-complete.log、build/web3-automation-assisted-ui.log、build/web3-automation-team-ui.log。原生源码结构统计在build/web3-automation-native/analysis-acceptance.json。授权范围和实际执行确认保留，未提供RPC/Key的真实链对齐与Etherscan备用源仍未验证。源码仍未提交或推送，所有现有及并行修改保留。
