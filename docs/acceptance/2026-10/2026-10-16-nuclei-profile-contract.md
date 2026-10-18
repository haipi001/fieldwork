# 标准扫描模板完整性合同

## 2026-10-16 标准扫描模板合同

用户目标：继续完成文件夹开发规划，UI不缺失功能页面。
实现：研究表单原已传递scan_profile，发现command_for在standard模板不齐时静默退回tech标签。新增统一nuclei_profile_templates检查，标准档四模板缺失/叶链接拒绝；执行计划返回nuclei_templates阻断，单工具实际执行在扣预算前检查，不以quick技术识别替代标准扫描。quick/deep既有行为保留。
验证：完整模板生成四个显式-t且不带回退-tags；缺一模板拒绝；执行计划阻断；单工具拒绝且不扣tool_calls_used；真实Nuclei配置正反例保留，共8通过，日志build/nuclei-profile-contract-tests.log。git diff --check通过。本次UI与主数据未改，尚未原生重载。
剩余：deep完整受控模板范围、供应链固定与真实模型发现/独立复验/报告全链路仍需继续；整体15 Task/8 Gaps范围保留。
同步状态：当前可见请求、实现与结果同步八个入口，无秘密或实际研究目标。
