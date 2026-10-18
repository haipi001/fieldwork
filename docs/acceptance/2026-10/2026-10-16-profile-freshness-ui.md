# Profile 过期状态


## 2026-10-16 Profile 当前性与过期 UI

Profile API 新增动态 provider_configuration_current/status 与 changed_provider_ids，比较冻结Provider哈希和当前注册表；配置内容与immutable_hash不变，Provider缺失也标stale。config列表复用同次Provider查询。模型注册表显示过期与创建新版本提示；路由选择器保留历史并标过期；组队选择器保留历史但禁选过期项，默认不选过期activeProfile。路由执行守卫保持不变。

20相关测试通过（build/runtime-profile-freshness-final-tests.log），覆盖费率更新保留凭据/健康/其他metadata、旧版本内容不变与实际路由阻断、新版本current、Provider删除stale。原整对象相等测试更新为不可变字段相等加动态状态断言。真实API浏览器含编辑费率后过期注册表/选择器提示，中英文/主题/窄屏及原配置策略流程通过，0页面错误/0模型请求（build/runtime-profile-freshness-ui-verified.log）。先前浏览器测试的语言错误与刷新竞态失败日志保留；改为当前English断言并等待选择器具体状态后通过。JS/diff检查通过。

空闲主库0运行Run/执行任务/模型调用/活动监控后正常重启来源链接App，原生页面确认旧DeepSeek Profile提示Provider已变化，新费用上界版本保留且Provider HEALTHY。未自动改绑组或启动主库任务。组队禁选代码已交付，未额外声称原生组队对话框点击验收。整体规划仍未完成。
