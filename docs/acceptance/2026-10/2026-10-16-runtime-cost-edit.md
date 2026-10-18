# 模型费用上界编辑


## 2026-10-16 Provider 费用上界编辑

新增 ProviderUpdate.cost_micros_per_million_tokens（严格整数0..10^10）。SQL json_set仅更新费率，保留其余metadata、密钥、地址、模型及健康记录；现有配置哈希使旧Profile路由阻断，新不可变版本绑定当前配置。UI新增每Provider费用上界输入/保存、零费率云研究阻断提示、变更影响确认与创建新Profile交接，中英文保留原操作。

隔离后端15 passed，build/runtime-cost-edit-final-tests.log。首轮测试错误假定Profile视图有current字段，改为检查哈希差异及实际旧Profile路由拒绝。真实API浏览器验收通过：1Provider、1Profile、2策略版本、1健康请求、0模型请求、0pageerrors，build/runtime-cost-edit-ui-retry.log。首轮浏览器因新增交接替换按钮而旧测试仍找原按钮超时；重排交互同时覆盖两条原/新交接后通过。JS/diff检查通过。

按已授权DeepSeek接入，将主库现有Provider设置1200000µ/百万词元统一保守预算上界（不是账单价），创建DeepSeek · 研究组 · 费用上界版本；旧Profile保留，不自动改绑旧研究组或重启任务。依据当前官方页面https://api-docs.deepseek.com/quick_start/pricing/ Flash最高输出1.2USD/百万，统一输入/输出上界会高估输入费用；价格变化需复核。主库0活动Run、0执行任务、0调用、0活动监控后正常重启来源链接App，原生实际显示输入1200000、保存按钮、新Profile及HEALTHY。未运行主库研究，未Git提交/推送，完整规划仍未完成。
