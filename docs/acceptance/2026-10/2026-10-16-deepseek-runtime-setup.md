# 2026-10-16 DeepSeek 运行配置接入

用户明确选择复用现有 DeepSeek 服务。调用现有后端配置函数，将已有凭据在进程内写入 V5 私有凭据存储；未输出密钥。新增 cloud Provider（deepseek-v4-flash）与不可变 cloud Profile，单次 8192 tokens、并发 1、每小时 100000 tokens。未绑定或恢复既有敏感任务。

健康检查 unavailable。无凭据 HTTPS 探测进一步确认 Python TLS 返回 SSLCertVerificationError：self-signed certificate in certificate chain。未关闭证书校验、未修改信任链。尚未验证密钥有效性、模型可用性或真实推理，调用次数为零。费用费率仍默认 0，不能作为实际免费使用或准确费用证明。

原生 App 点击刷新配置后，显示 DeepSeek Provider 与研究组 Profile。证据：build/deepseek-runtime-setup.json。完整开发与自动化验收仍未完成。


## 后续：健康检查 CA 修复

发现健康检查未使用已锁定 certifi CA，而实际推理传输已使用该 CA。健康检查改为同一标准 CA 包，保持 CERT_REQUIRED、主机名校验、禁用环境代理及拒绝重定向。新增脱敏证书错误类型，UI 显示证书/认证失败原因。修复后真实已配置服务 GET /models 返回 HTTP 200 healthy；未调用推理。运行配置与 TLS 回归 11 passed、云传输证书/主机名/重定向回归 9 passed；日志 build/runtime-health-tls-tests.log、build/runtime-health-cloud-tls-tests.log。JS 语法与 diff 检查通过。正常退出空闲 App 并重新启动以加载修复。全规划尚未完成。
