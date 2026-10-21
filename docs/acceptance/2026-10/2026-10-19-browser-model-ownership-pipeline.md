# 浏览器材料 → 实际模型 → 独立复验与报告验收

日期：2026-10-19。完整规划 UI-00→QA-07、15 Tasks / 8 Known Gaps 继续开放。

## 本轮结果

使用现有 DeepSeek 服务，仅发送明确公开构造的 loopback 开发夹具；未发送主库私有研究数据、Cookie、请求头或原始身份字段。未预置候选或模型 Claim。

| 场景 | 真实模型调用 | 草稿 | 独立结果 | 正式发现 |
| --- | --- | --- | --- | --- |
| 存在跨账号读取 | 1 | 2 | verified | 1 |
| 已修复，含拒绝响应反证 | 1 | 3 | refuted | 0 |
| 业务允许共享 | 1 | 3 | business_access_permitted，未执行复验 | 0 |

正例走通实际浏览器采集、登记元数据、研究图谱、真实模型草稿、人工审阅候选、受控重放、独立回执和限定报告。审阅和启动复验仍需明确操作，不是全自动研究。

正例与共享例先于最终拒绝响应补丁通过；最终版本只重跑修复例，不能宣称最终冻结版本完整三例矩阵或模型稳定性通过。模型对反例仍提出草稿，依靠业务规则和独立复验防止错误晋升。

含较早修复例的4次付费调用，保守配置费率估算合计213210 cost_micros，非服务商账单。两次前置失败均未调用模型，日志保留。

## 实现

- 浏览器仅保留同源、无查询 GET 的有界 JSON：200 的既有最小字段，401/403 丢弃响应体但保留状态与摘要。
- 登记 http.ownership_projection 产物、观察及拒绝反证；文件0600、排他创建、数据库失败清理文件。
- 图谱导入、草稿审核、付费派发前重新检查登记文件及原始 HTTP 事实；来源变化即阻止。
- 已确认业务规则可将审阅后的所有权材料转入 authorization 候选；拒绝证据保持原反证，不变为支持证据。
- 长指纹在审阅 UI 自动换行，既有页面保留。

## 验证与证据

- build/browser-ownership-final-regression-20261019.log：61 passed。
- build/browser-ownership-preservation-current-20261019.log：实际浏览器3 passed。
- build/ownership-workspace-preservation-20261019.log：18页、5智能体页签、144显示组合通过，0模型调用。
- build/live-browser-model-positive-current-20261019.log：正例1 passed。
- build/live-browser-model-controls-20261019.log：较早控制组2 passed。
- build/live-browser-model-denial-current-20261019.log：最终拒绝反证修复例1 passed。
- build/live-browser-model-pipeline-20261019/：冻结输入、Claim、元数据、回执及 vulnerable-report.md；12份采集产物SHA已核对。

## 明确限制与后续

1. 捕获限定有 Content-Length 的 JSON、最多65536字节；所有权指纹支持固定九个字段路径，不覆盖所有业务响应。
2. 指纹仍是私有数据，不表示匿名化；默认材料保持私有。
3. 历史已去重但未登记图谱产物的旧 HTTP 记录尚无补录；同Run重采相同记录可能继续被去重。需新Run或后续显式迁移，未重写旧库。
4. 开发夹具不是 holdout；三研究域质量、原生三次干净启动、规模、独立Agent行为验证及其余规划尚待完成。
5. App仍是源链接、临时签名的本机安装，不能作为独立正式发行验收。
6. 旧全量1452测试早于当前增量，本轮仅报告上述相关验证。未提交或推送Git。

## 原生安装加载状态

源链接App已通过正常退出停止本地服务（curl exit7），重新启动显示启动页；随后Mac锁屏阻止CUA访问。最终启动后页面验收待用户解锁，不能报告本轮原生UI检查完成。主库runtime_calls=0、identities=0、无运行/租约任务；1旧排队任务保留。
