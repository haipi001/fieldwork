# HTTP 对象读取的业务权限判定

2026-10-04：两轮身份/对象/匿名对照可以建立读取事实，但合法共享也可能满足所有读取断言。正式确认额外要求已确认 ScopeSnapshot 中存在唯一、有效、精确匹配对象 URL 的业务权限规则，且该规则拒绝测试主体读取。

## 配置来源与约束

通过项目创建接口的 `scope.http_object_read_rules` 提供规则，并使用已有项目确认流程确认整个 Scope。这是项目方提供的业务预期，需要真实业务依据；模型不应根据 `owner_id` 自动填入 owner_only。当前尚无专用 V5 规则编辑器。

```json
{
  "http_object_read_rules": [
    {
      "target": "https://authorized.example/api/object/42",
      "access": "owner_only",
      "source": "项目业务规范：对象仅限其所有者读取"
    }
  ]
}
```

- `owner_only`：仅所有者。不同主体读取违反本条规则。
- `allowlist`：所有者及 `allowed_principals` 中列出的主体可读。主体值使用身份检查接口所选字段的字符串值。
- `public`：允许公开读取；不作为对象读取越权证明。
- `source` 必须非空，用于记录业务依据。它不代表软件已经验证了该依据的真实性。
- URL 精确匹配，不支持通配符、目录继承或自动合并。重复匹配、格式错误、缺少规则、主体变化均不能确认。

## 执行与收据

先执行原有两轮真实请求，再按两轮稳定的主体评价规则。业务判断保留为 `business_boundary`：`denied`（规则拒绝）、`permitted`（规则允许）、`unknown`（规则/身份不足）。该值描述规则许可，不等于实际 HTTP 状态或漏洞结论。

正式成功仍要求两轮读取断言通过且结果稳定，并额外要求 `denied`。`permitted` / `unknown` 进入 human_review，保留原始产物和原因，不签发成功收据。Guided 路径继续保存读取事实及业务判断，尚不自动生成正式 Finding。

产物绑定目标、完整 Scope 内容摘要、规则摘要和主体摘要。收据签发和验证分别检查绑定，Scope 替换使旧结果不能晋升。修复确认也要求相同业务规则依据。旧 HTTP 收据若缺少此证据，不能重新用于晋升；历史 Finding 不在此升级中自动修改。

本变更没有数据库迁移。新增 JSON 字段兼容现有存储；回退旧代码会恢复旧判定行为，应保留当前版本。

## 验收与剩余工作

`tests/test_http_business_boundary.py` 使用实际本地 HTTP、实际范围/DNS检查和数据库收据路径：owner_only、allowlist 拒绝为正例；合法共享、缺规则、重复规则、错误目标、错误格式进入复核；执行后规则变化拒绝晋升。候选由测试构造，仍不代表模型自主发现验收。

`tests/test_final.py::test_real_http_replay_oracle_with_negative_control` 验证带已确认业务规则的正例、报告导出和实际修复复测。另保留 `tests/test_guided_http_live.py` 的四类读取对照。

下一步：V5 业务规则录入/审阅与分诊执行联通、从真实发现材料重建独立执行任务、隔离进程复验与回执、三次清洁启动和故障恢复。规则准确性、真实影响和独立执行仍需单独验收。
