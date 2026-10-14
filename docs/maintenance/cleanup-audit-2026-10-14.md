# 历史代码与生成物清理审查 — 2026-10-14

## 审查范围与方法

核对 GitHub main（2a4b918）与开发分支（397c401），并检查原工作区正在开发的 UI/脚本引用。通过模板加载、API 路由、脚本/测试引用、源码资源定位与安装 App 配置进行分类。本报告没有删除文件；引用检索不能证明不存在仓库外消费者，删除批次仍需匹配范围的验证。

当前结构不是每个日期对应一套废弃产品：V5 主工作台、兼容工作台、公共网站和原始规格原型有不同入口。历史文档中的文件名也不能单独作为运行依赖。

## A. 建议第一批清理

| 文件/目录 | 当前依据 | 建议 |
| --- | --- | --- |
| `static/app.css` | main、开发分支模板/JS/CSS/源码/脚本/测试中未发现引用；6,685 bytes | 可以列入删除批次，回归两个页面入口及官网资源 |
| `static/details.css` | 同范围未发现引用；7,179 bytes | 可以列入删除批次，回归详情页显示 |
| `.pytest_cache/` | pytest 可重建缓存，当前本地约 179 KiB | 可清理本地缓存，不影响 Git 源码 |
| 各源码/测试目录的 `__pycache__/` | Python 自动重建缓存；原工作区根缓存约 4.2 MiB | 可清理缓存，保留 `.py` 源码 |

两份 CSS 共 13,864 bytes。它们是最明确的已无页面加载入口的早期样式。当前已全局审查的代码范围未包含运行数据、密钥内容或第三方程序。

## B. 清理前需完成一个配套动作

| 对象 | 依赖或条件 | 建议 |
| --- | --- | --- |
| `static/app.js`（16,941 bytes） | 未被当前两个模板加载；`tests/test_repository_layout.py` 有文件存在断言。其他测试中的 CDN `app.js` 是样例 URL；官网 `/app.js` 是 `website/dist/app.js`，不对应本文件 | 调整资源定位测试为真正加载的 `static/final.js`，再删除并验证；不要误删官网同名文件 |
| `dist/native-ai/fieldwork-native-collector-preview-0.42.0.zip` | 本地生成的旧预览包，18,861 bytes；不是当前业务源码 | 确认没有作为保留发布产物后可删除，必要时单独归档 |
| `build/clean-runtime-qa/` | 本地干净环境测试副本，约 217 MiB；安装 App 启动配置不指向该目录 | 确认无正在运行的测试/工具且验收证据已留存后可清理 |
| `build/startup-retry-fixture/`、`build/startup-retry-fixture-app/`、`build/startup-recovery-fixture-source/` | 隔离启动恢复夹具/测试 App；安装配置不指向它们 | 检查对应进程已退出并保留必要结果，再清理 |
| `build/ui01-native/`、`build/web3-native/`、`build/web3-automation-native/` | 原生 UI 验收副本，安装配置不指向这些目录 | 同上；不能用“旧目录”代替进程及回滚检查 |
| `build/` 中历史截图、日志和检查输出 | 一部分被验收记录作为证据引用 | 可以归档后按保留规则清理；不要整个删除 `build/` |

本地目录容量为本次观测值，会随运行改变。未承诺上述目录都没有活动进程。

## C. 旧 UI 可整批退休，但现在仍有入口

开发分支的 `/` 和 `/v5` 提供 `templates/v5.html`；下面 8 类兼容路由仍提供 `templates/index.html`：

`/new`、`/run`、`/runs/{resource_id}`、`/findings`、`/findings/{resource_id}`、`/reports`、`/reports/{resource_id}`、`/settings`。

main 的业务基线也仍使用这套兼容页面。因此以下 25 个文件不能直接删。应先核对 V5 功能等价覆盖、迁移所有链接/路由与必要脚本，再退休该文件组；业务 API 是否删除必须另外审查。

| 兼容页面/资源 | 当前关系 |
| --- | --- |
| `templates/index.html` | 路由直接渲染 |
| `static/agent-audit.css` | 被 `templates/index.html` 加载 |
| `static/agent-audit.js` | 被 `templates/index.html` 加载 |
| `static/candidate-queue.js` | 被 `templates/index.html` 加载 |
| `static/candidate-review.js` | 被 `templates/index.html` 加载 |
| `static/deep-research.js` | 被 `templates/index.html` 加载 |
| `static/final.css` | 被 `templates/index.html` 加载 |
| `static/final.js` | 被 `templates/index.html` 加载 |
| `static/finding-report-fields.js` | 被 `templates/index.html` 加载 |
| `static/guided-research.css` | 被 `templates/index.html` 加载 |
| `static/guided-research.js` | 被 `templates/index.html` 加载 |
| `static/http-verification.js` | 被 `templates/index.html` 加载 |
| `static/i18n-catalog.js` | 被 `templates/index.html` 加载 |
| `static/i18n.js` | 被 `templates/index.html` 加载 |
| `static/identities.js` | 被 `templates/index.html` 加载 |
| `static/onboarding.js` | 被 `templates/index.html` 加载 |
| `static/reading-layout.css` | 被 `templates/index.html` 加载 |
| `static/settings-polish.css` | 被 `templates/index.html` 加载 |
| `static/settings-ui.js` | 被 `templates/index.html` 加载 |
| `static/state.css` | 被 `templates/index.html` 加载 |
| `static/target-import.js` | 被 `templates/index.html` 加载 |
| `static/web3-compiler.js` | 被 `templates/index.html` 加载 |
| `static/web3-practice.js` | 被 `templates/index.html` 加载 |
| `static/web3-verification.js` | 被 `templates/index.html` 加载 |
| `static/workspace-polish.css` | 被 `templates/index.html` 加载 |

这套 UI 的日期虽然早，但仍可通过真实路由进入。直接删会造成页面、样式或操作缺失。

## D. 应保留的代码与数据

| 对象 | 保留原因 |
| --- | --- |
| `templates/v5.html` 与 `static/v5-*` / `v5.css` / `v5.js` | 开发分支的当前工作台；页面、功能和验收脚本仍引用 |
| `backend/` 业务 API、编排、验证、采集模块 | 文件名含 V5/V6 表示职责和合同版本，不代表废弃；尚无可直接删除的整套后端证据 |
| 根目录 `app.py`、`desktop_server.py`（开发分支） | 兼容现有启动配置的入口，实现在 backend |
| `backend/report_adapters/` | 报告预览/导出的实际配置；此前被忽略规则漏同步，已补齐 |
| `website/dist/` 与官网资产 | 当前公共网站发布内容；不能因名称带 dist 就当作临时产物 |
| `collectors/browser-ai/popup.html` | 浏览器采集器界面，不是旧工作台原型 |
| `tests/`、`fixtures/`、`benchmarks/` | 回归、反例、隔离实验与历史质量对比依据 |
| `data/`、凭据存储、证据、备份、`build/backups/`、`build/rollback/` | 业务或恢复材料；本轮不进入删除候选 |
| `build/macos/Fieldwork.app/Contents/MacOS/FieldworkKeychain` | 后台 session_capture 的凭据辅助路径仍可能使用，不能随 build 整体删除 |
| 原工作区未提交的前端、研究辅助和 Web3 导入代码 | 当前并行开发，未因其未提交或位于旧布局就判为弃用 |

## E. 原型与历史规格的归档选项

`FINAL/SRC_AI_Security_Research_OS_FINAL_2026-08-27/prototype/index.html` 是历史交互原型，当前服务和模板没有运行引用。它被原始 `PACKAGE_MANIFEST.json` 列入包清单，规格包还有内部 SHA256SUMS 和核对脚本。

如果不再需要随源码保存原始交接包，可先将**整个规格包**制作可校验归档，再调整运行及文档引用。不要只删除 prototype 让原始包结构/清单失配。报告运行配置已独立放入 backend；其他规格/Schema 依赖仍需逐项检查。本轮只建议归档评估，没有授权删除该包。

## 推荐执行顺序

1. 先清理两份无引用 CSS、本地缓存；处理 app.js 的资源断言后再清理该脚本。
2. 将旧测试环境和预览包的进程、发布及回滚关系核对完，归档验收材料后清理生成物。
3. 把兼容 UI 的功能差异列成迁移清单，完成路由和验证后再删除整组文件。
4. 独立处理原始规格包归档；业务数据库、备份、当前源码和官网资产继续保留。

上述 A/B 是候选清单，并非已执行的删除。直接删 CSS/JS节省约 30 KiB；主要磁盘回收空间来自测试环境副本，不能通过删除活跃功能换取目录简洁。
