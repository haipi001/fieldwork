# 实际工具与包适用性独立复验


## 2026-10-17 实际Trivy产物到包适用性独立回执

本机Trivy0.74.0、初始无漏洞库，使用CLI默认官方OCI源mirror.gcr.io/aquasec/trivy-db:2下载128.03MiB压缩库（展开约1.5GiB），正例命令90s内完成；关闭遥测/依赖识别API，未装待测包。固定本地requirements三样本：requests2.31.0、requests2.32.5、idna3.10。固定/无关样本用已缓存DB、skip-db-update及45s时限。三个实际CLI都exit0，原始JSON未伪造。OSV API实际获取GHSA-9wx4-h78v-vm56（151精确受影响版本），原始SHA07a0249f7b5b44ffde672d5a117b004317a1664a81099ad5827f08d4a4154354。官方Requests公告https://github.com/psf/requests/security/advisories/GHSA-9wx4-h78v-vm56说明此问题修复于2.32.0；本轮只针对这份公告。

实际发现适配缺口：只保存Vulnerabilities，遗漏Trivy Packages。新增code.dependency_inventory，保存实际Name/Version/PURL；普通清单不进入安全Candidate。缺字段/错误结构/每结果超过200项显式inventory_incomplete，独立Verifier拒绝，不信任静默截断清单。Oracle接受原漏洞条目及包清单并保持PURL/版本一致性守卫。

三个隔离数据库从实际CLI输出经过正常parse/_persist_result、实际公开OSV导入、由实际包清单派生fingerprint、自动契约计划、请求、Seatbelt独立Verifier：正例选定CVE-2024-35195命中1，包适用性verified；定向修复样本该CVE命中0，回执inconclusive；无关包该公告match0，无回执。三个样本均0正式Finding/0模型调用。固定和无关样本各存在其他2026CVE，不能称其整体无漏洞。本轮是工具CLI产物经验收harness导入链，不是App完整工具启动、模型自动发现或3次冷启动全链验收。fixed的inconclusive也不是修复确认。无新增用户真实研究目标。

63相关通过、1既有warning，14.20s，build/trivy-inventory-verified-tests.log。包含3实际产物样本、清单普通观测/不入Candidate、错误或过长清单显式标记、后置验证器拒绝不完整清单且请求事务回滚，及包计划/情报/本地Verifier/仓库执行回归。实际产物验收需FIELDWORK_REAL_TRIVY=1，普通测试默认跳过，避免依赖本机build样本。

证据目录build/actual-trivy-package-20261017：三原始stdout/log/requirements、公开advisory.json、三个落库产物和acceptance.json回执、manifest.json。记录二进制/漏洞库/文件SHA及DB时间；DB SHAbf1f1b29d76cae9ff085d418cb4bfdb3906b6069f837abdef06558b8102dd1db。来源使用条款尚未完整核实，局部导入明确标source terms not verified/local acceptance，不宣称情报源治理完成。没有分发这些原始数据库。

主库0活动Run/执行任务/模型调用/活动监控后正常重启来源链接App加载适配，healthready，未注入主库样本。UI无页面/功能删除。全量1353基线早于此改动；本轮专项不代替全量或真实三域自动发现/独立确认/正式报告。全部规划保持进行中。
