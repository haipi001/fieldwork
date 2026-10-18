# 历史证明异常格式与范围归属

## 2026-10-16 历史证明异常格式与范围归属

用户目标：继续完成整体开发规划，保留所有UI功能页面。真实模型按用户已选本机服务，具体服务信息待异步回复，本轮继续独立工程工作。
实现：finding_report_inputs严格解析impact/eligibility/verification为对象、evidence_ids为字符串数组，非法JSON/字节/过深嵌套转换明确409，拒绝缺失的已引用证据；build_proof_attachments校验候选/运行/Scope归属及绑定，收据必须对象，材料映射/Artifact引用/步骤/Oracle及V5证明绑定结构有效。异常被既有运行详情证明检查归为待复核，历史verified登记不改，证明包不能导出；不吞掉任意程序异常。
验证：51历史格式与真实本机HTTP收据组合通过、1既有警告（65.64秒），build/historical-proof-shape-tests.log。追加非法字节/过深JSON并完成归属检查后，53历史格式/报告/发现/复验接口选择回归通过（111未选择、1既有警告，7.20秒），build/historical-proof-final-regression.log。覆盖null/数组/数值/损坏JSON、嵌套材料错误、证据缺失、异项目Scope、详情保持200待复核/证明包409/历史状态保留。真实HTTP正例仍可导出并显示有效证明，后续材料变动继续失效；不是模型自动发现质量验收。git diff --check通过。
安装App：确认无活跃Run/已租用任务/模型调用，CUA正常退出并确认8000不再监听，重启源码关联0.68.75/build153，health ready。证据/独立复验/报告/智能体四页实际打开无加载错误，保留智能体清单。九类业务表计数/摘要一致、quick_check ok，build/historical-proof-native-before.json与-after.json。未在主库注入损坏材料，未重打独立发行包或Git提交推送。
边界：本轮证明格式与来源绑定检查，不代表全历史证明已真实重放或完整闭环完成；源文件安全读取、快速暂停恢复旧执行者隔离、本机模型接入与真实研究质量、迁移切换回滚及发布门等继续。
同步状态：当前目标、实现、验证和安装边界同步八个记忆入口，无秘密或真实目标信息。
