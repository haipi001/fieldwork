# 当前工作区全量回归


## 2026-10-17 当前工作区全量回归

命令 `.venv/bin/python -m pytest -q` 正常完成：1353 passed、4 skipped、2 warnings，523.69秒。日志 build/full-current-regression-20261017.log。本次覆盖截至研究组生产者与独立复验修复的当前工作区，不沿用1176通过的旧快照。记录330个Python/JS/HTML/CSS/lock源码文件SHA及Git/Python环境，build/regression-source-manifest-20261017.json；测试期间快照到完成核对0文件变化，aggregate SHA256 2785e5fa91dc83da37287f05d53798ffdbbf79013caae1fb41d39d0c3eefa1da。工作区未提交，不是Git HEAD已包含这些修改的证明。

定向核对跳过原因（2 passed、4 skipped，0.92秒；build/full-regression-skips-20261017.log）：可见Chrome隔离流程2项需FIELDWORK_TEST_BROWSER；原生Keychain1项需FIELDWORK_TEST_KEYCHAIN；真实付费DeepSeek1项需FIELDWORK_LIVE_DEEPSEEK。均保持默认未开启，没有偷偷运行付费/可见浏览器/钥匙串操作。警告：Starlette依赖弃用；故意构造重复entry.py ZIP的拒绝测试警告。无失败、未为转绿禁用测试。git diff --check通过。本轮无应用源码修改/DB配置改动/Git提交或推送。

全量工程回归并非完整产品验收。当前独立Verifier实际支持三类明确契约：http_object_read_v1、package_applicability_v1、loopback_http_status_v1；普通研究假设不自动获得可执行复验契约。完整真实工具+模型自动发现→独立复验→Receipt→报告三域闭环、正/修复/合法反例与3次清洁启动、8/16/32组队收益、全部源治理/迁移激活回滚/独立发行仍未完成，原全部15Task/8Gaps保持进行中。下一步应在有实际结构化输入的类型推进契约派生与复验衔接，不为任意模型文本生成伪验证入口。
