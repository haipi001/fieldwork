# 真实扫描配置正反例

## 2026-10-16 真实扫描配置正反例与误候选修复

用户目标：继续完成文件夹开发规划，UI不缺失功能页面。
环境核对：主数据库runtime_providers与runtime_profiles均0，无法完成实际模型驱动的质量验收；继续独立工程与真实工具验证，不将模拟协议当推理。
实现：单工具TraditionalToolInput新增scan_profile并传入run_capability，修复请求standard被忽略始终quick。Nuclei纯技术识别（tech-detect或info纯发现标签）记录surface.technology，保留原观察，不再自动建立安全候选；真正安全/配置模板继续scanner.template_match。
证据：真实已安装Nuclei经现有Scope/预算/受控代理访问隔离loopback，标准档每样例15请求；缺CSP样例自动产生1候选，修复后0候选，均0正式Finding/0模型调用。未替换扫描器或注入其输出。模板SHA、产物SHA及stdoutSHA保存build/actual-scanner-discovery/{missing_csp,repaired}.json。14正反例/分类/候选下一步测试通过，build/actual-scanner-control-tests.log；git diff --check通过。首次修复样例额外候选经保留测试库定位为Wappalyzer技术识别，并已通过本次真实对照复核。
边界：缺安全头仅是配置线索，不等于可利用漏洞；本轮不是模型发现/独立复验/报告全链路。UI与主数据未改，本次未原生重载；完整三域与15 Task/8 Gaps目标继续。
同步状态：当前目标、动作与结果同步八个入口，无秘密或真实研究目标。
