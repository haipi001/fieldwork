# Deep显式模板范围与真实对照

## 2026-10-16 Deep显式只读模板与公开API观察

用户目标：继续完成文件夹开发规划，UI不缺失功能页面。
实现：Deep不再依赖工具默认全库加载，显式选中standard四模板并扩展Git配置/OpenAPI共六模板，缺失拒绝。执行预览显示所选只读模板数量及范围预算约束。OpenAPI/Swagger纯info检测记录surface.api_description，不自动建立漏洞候选。
验证：13模板合同/分类/真实扫描测试通过，build/deep-template-profile-tests.log。实际Nuclei标准/Deep正反例均执行；Deep每例17个真实loopback请求，缺CSP并暴露Git配置的样例自动2候选，修复后公开API文档仍存在但0候选；均0正式Finding/0模型调用。摘要build/actual-scanner-discovery/*-deep.json（missing_csp名称的Deep样例同时包含Git配置暴露，定义见测试代码）。这是明确六模板覆盖，不宣称全模板库或通用漏洞能力。
状态：主数据和UI组件未改，尚未原生重载；完整模型驱动发现/独立复验/报告等规划继续。已通过异步问题询问真实模型服务选择，当前主库Provider/Profile为空，凭据请用户在App配置；等待输入，不以模拟输出替代质量验收。
同步状态：当前目标、实现、结果与待输入项同步八个入口，无秘密或真实研究目标。
