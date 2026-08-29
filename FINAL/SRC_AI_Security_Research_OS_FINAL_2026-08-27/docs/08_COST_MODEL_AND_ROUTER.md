# 08 — 成本 / 时间 / 模型路由

## 目标

用户不懂技术，所以默认只选择：
- 快速
- 标准
- 深度

系统内部映射到预算。

## Fast
- passive/recon
- high-value static
- shallow browser
- limited hypotheses
- verification on strong signals

## Standard
- full mapped surface
- multi-role auth tests
- code correlation
- targeted fuzz
- broader verification

## Deep
- expanded business logic
- larger search budget
- symbolic/formal where useful
- Web3 economic/invariant exploration

## Token reduction

1. CLI output adapter 先结构化。
2. 去重后再给模型。
3. 一次只发送相关 target slice。
4. large files 使用 symbol/window retrieval。
5. Agent 不看完整 raw log。
6. Evidence 保存在 Artifact Store，不塞上下文。
7. Model Context 只保存摘要 + pointers。

## Cache

缓存 key：
- target fingerprint
- source commit
- scope hash
- tool/version
- config hash
- input hash

Scope 变化或目标变化必须失效。
