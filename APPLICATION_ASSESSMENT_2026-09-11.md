# Fieldwork 综合评估 · 2026-09-11

评估对象：当前工作区及运行时 0.32.1 build 32。方法：代码审阅、本机只读 HTTP 检查、正式 SQLite 只读统计、临时目录 Foundry 配置检查、主流方案官方资料对照。没有向历史目标发起测试、修改候选、执行恶意 FFI，未对竞争产品做同条件实测。上一轮 153 项回归通过，本轮未重新运行；测试通过数量不代表真实漏洞召回率。评估期间无产品代码改动。

## 判断

定位为有真实执行链和证据门禁的个人安全研究工作台，成熟度约为功能型 Alpha / 早期可用产品。研究流程与材料管理较完整，通用未知漏洞发现、协议经济证明、隔离运行和跨机器交付尚不成熟。不能据此称为专业审计替代品，也没有足够证据给出行业百分位或商业漏洞发现率。

## 能力与市场方案

| 维度 | 当前能力 | 主流方案与差距 |
| --- | --- | --- |
| 范围及证据管理 | Scope、Run、Observation、Candidate、收据、报告、哈希导出与部分独立重放 | 是主要产品价值；流程完整不等于检测准确 |
| Web/API | 工具编排、导入资产、候选分诊、双身份对象读取复验、部分修复确认 | Burp 的 API 参数/认证扫描与人工代理工作流更广；当前只读导入会舍弃请求体、参数等语义 |
| Solidity | 源码模型、编译器 AST、入口/调用关系、Forge 属性复测，外部分析器适配 | 不等于 Slither 的成熟规则及 IR 深度；有适配接口也不代表所有引擎在所有项目运行成功 |
| 模糊测试 | 64 fuzz-runs、两 seed 重放、三类经济夹具 | Foundry/Echidna 提供属性与状态序列测试，效果取决于 harness/invariant；需要覆盖、语料、mutation 和独立案例验收 |
| 形式化证明 | 未见等价完整能力 | Certora 依赖明确规格开展证明；当前 AST 和有限测试不能替代 |
| AI Agent | Native 流程及可选 Strix/Shannon 适配 | 不能继承外部工具的公开成绩；需同版本、目标、预算和人工投入对照 |
| 桌面产品 | 已能在本机运行，有备份与版本接口 | 硬编码个人工作区/Python，仍未完成干净机器自包含安装验收 |

## 实际能解决的问题

1. 将分散的扫描输出与研究记录组织为可追溯证据，避免直接把模型判断当漏洞。
2. 对支持的 HTTP 对象权限问题执行身份基线、负对照和稳定性验证；依赖研究者提供正确的身份/对象语义。
3. 帮助 Solidity 研究定位入口及交互点，并重现已经编码为属性测试的失败。
4. 用正反夹具学习账本、权限、份额舍入问题；结果只证明夹具，不证明真实协议。
5. 汇总复现、影响和报告材料，降低整理成本；完整度评分不代表平台认可。

不能稳定承诺自动找到业务漏洞、通用经济攻击链、准确赏金级别，不能把零已验证结果解读为安全。

## 当前数据及能力缺口

正式 data/src_control.db：13 个 analysis_runs、91 条候选记录（77 candidate、14 archived）、0 canonical_findings。全状态候选有 37 correlated_observation，其他多为业务/结构信息；只有 1 access_control 分类，但分类并不证明该问题存在。没有人工真值，不能计算误报率/召回率。

最新 logic-v4.json 只有 10 个自建 case；没有形成独立真实项目盲测。153 回归测试检验软件行为，不是 153 个真实漏洞。两 seed/768 夹具 fuzz 输入不能外推为 DeFi 检测成功率。

主要实战缺口：
- 候选到可执行专用验证之间仍需要大量人工，分类提示不是验证器。
- 合约交互的运行时地址、代理/升级、虚分派、storage alias、跨合约状态仍有空白；静态调用关系不能证明具体资产损失。
- 部署状态、区块固定、字节码/实现地址、真实流动性、费用与滑点、攻击者权限和初始本金，需要共同进入真实协议 PoC 验收。
- 普通属性失败可能来自错误测试、错误前置条件或测试专用权限。重复失败只证明可重现，不能自动证明可利用。
- HTTP 记录包离线检查与在独立环境重发请求不同。收据/哈希证明的是本地记录一致性，不是第三方公证；数据库写权限属于信任边界。
- Forge/ptai 的负向修复确认、真实成本核算和干净安装仍有未完成项。
- 状态文档保留多轮过时段落，易把早期阻塞和后期完成混淆，需要统一最新验收矩阵。

## 应用自身安全与可靠性问题

### A. 不可信项目执行缺少隔离（优先 P0；配置机制已确认，未执行恶意命令）

web3_analysis.py:411、454、472 使用 cwd=root 直接执行 Forge，未指定清理后的 env，未强制关闭项目 FFI 或提供 OS 沙箱。inspect_source 在编译后运行项目测试。临时 foundry.toml 设置 ffi=true 后，forge config --json 实测返回 true。Foundry 官方明确 FFI 可执行主机命令，默认关闭但项目配置可开启。

触发条件：用户分析包含恶意配置/测试的不可信项目。潜在影响是当前用户权限下命令执行及文件/环境访问；本轮只确认配置与调用链，不声称完成端到端攻击。整改应结合配置约束、可信编译器、环境白名单、隔离目录、进程资源限制与 VM/容器等执行边界，仅删 --ffi 参数不足以解决。

### B. 本地 API 缺少会话鉴权及 Host/Origin 边界（优先 P0/P1）

app.py:203 创建 FastAPI 并挂载路由，未见统一认证/Host/Origin 中间件。禁用测试客户端代理后，对只读 /api/v1/system/version 无凭据、任意 Host、任意 Origin 均返回 200。路由含设置与任务等写操作，但本轮未实际调用。

监听 127.0.0.1 限制网络暴露，不隔离同机进程。跨站能否读取/调用还受浏览器同源、预检、私网访问等限制，本轮没有证明网页一键利用或完整 DNS rebinding 链。整改：启动会话凭据、严格 Host/Origin、写操作授权、明确资源归属，并加拒绝测试。

### C. 桌面端口服务身份未验证（优先 P1）

macos 启动器 serverIsReady 仅检查固定 8000 端口 /new 的 HTTP 200。代码路径允许把预先占用该端口并返回 200 的服务视为就绪；未实测占用用户端口。应增加随机端口、启动握手与会话绑定，避免误连/仿冒界面。

### D. 原生子进程资源与环境隔离不足（优先 P1）

Forge capture_output=True 先捕获输出、之后才截断为 12000 字符；timeout 600 秒不限制期间内存/输出量，也不是完整进程树沙箱。长输出/重编译会影响桌面稳定性。应流式限额收集、取消进程组、设置并发与资源上限。未进行资源耗尽测试。

## 建议推进顺序与验收

1. 先修本机执行和 API 信任边界，使用无害哨兵夹具验证不能访问隔离目录外资源或执行主机命令；不要先追求更多扫描按钮。
2. 建立至少 20–30 个独立案例的第一批评估，覆盖真实公开修复前后项目、健康负样本与编译失败；固定版本/seed/预算，去除重复根因，报告精确率、召回率、重放率、人工分钟和成本。此规模只是起点，不足以证明行业领先。
3. 聚焦 HTTP 对象权限与两三类 DeFi 属性，打通真实协议本地 Fork、修复版反证、经济断言和第二环境重放。
4. 干净 macOS 账户/机器安装、升级回滚、异常中断恢复、工具版本锁定验收；应用可以托管工具和 VM，用户不必手动操作 Docker，但隔离能力必须保留。

## 官方来源（2026-09-11 检索）

- [Slither 检测器](https://github.com/crytic/slither/wiki/Detector-Documentation)
- [Foundry 不变量测试](https://getfoundry.sh/forge/invariant-testing)
- [Foundry 测试指南](https://www.getfoundry.sh/guides/index.html)
- [Foundry cheatcode 安全边界](https://foundry-rs.github.io/foundry/foundry_cheatcodes/struct.Cheatcodes.html)
- [Echidna](https://github.com/crytic/echidna)
- [Certora Prover](https://www.certora.com/prover)
- [Burp API 扫描](https://portswigger.net/burp/documentation/scanner/api-scanning-reqs)
- [业务逻辑问题](https://portswigger.net/web-security/logic-flaws/examples)
- [Nuclei 模板签名](https://docs.projectdiscovery.io/templates/reference/template-signing)
- [Shannon](https://github.com/KeygraphHQ/shannon/blob/main/README.md)
- [Strix](https://github.com/usestrix/strix)
- [OWASP Smart Contract Top 10](https://scs.owasp.org/sctop10/)
- [Immunefi 公开 PoC](https://github.com/immunefi-team/bugfix-reviews-pocs/blob/main/README.md)

资料对照覆盖主要方法，不等于穷尽全网。外部项目介绍不能替代独立对比测试。
