# V5-ORCH-02 Structured Worker Progress

日期：2026-09-30
版本：Fieldwork 0.54.0（Build 63 / Schema 23）

本轮完成 Critic 与 Synthesizer 的专用任务/结果合同，以及显式本地执行 tick。任务 Context Capsule 只传目标、已选 Claim/Evidence/Counterevidence ID、当前 Scope/Policy 与输入节点 hash，不复制全局对话。专用 Runner 能力、本地路由、租约、Token 预算、输出 Schema 和提交时输入 hash 复核共同约束图谱写入。普通任务接口不能冒充这三个保留角色，通用完成接口不能绕过结构化结果或不可变验证回执。

Critic 只能链接任务上下文中已有的一等 Counterevidence；单纯文字弱点进入 `open_question`，不能冒充反证。Synthesizer 拒绝显式冲突、失效或 Scope 不兼容的来源 Claim；生成带来源、范围、支持证据、反证和生产 Runner 标记的 `draft` Claim，不自动晋升正式结果。

显式 `POST /api/v1/workers/local/tick` 只领取 Critic/Synthesizer 专用任务，只接受健康、无密钥、字面回环地址的本地模型 Provider。模型看到脱敏、32 KB 上限的图谱子集，输出严格 JSON 后仍经结构和图谱边界验证；失败以脱敏错误类型重试，不写入部分结果。无本地 Provider 时保持排队。真实回环 HTTP 传输和模拟模型结果均有测试。未启用自动后台模型执行，也未触碰外部目标。

Verifier 继续使用单独 Verification Request/Task/不可变 Receipt；本轮要求 `isolated-verifier` Runner 类型，普通 Worker 即使声明验证能力也不能领取或签发。生产 Runner 不可自证，生产组不得与验证组相同。`/api/v1/workers/status` 明确报告进程级隔离尚无可验证证明，故 `V5-ORCH-02` 整项仍未达到“verifier isolated”的强验收门槛，不提前标记完成。

Schema 不变；回滚可移除 Workers 路由和专用角色门禁，既有图谱/任务事实保留。下一步补 Verifier 进程身份/隔离证明与负向验收，再复核 `V5-ORCH-02` 完成条件。

验证：`python -m pytest -q` 为 612 passed、3 skipped（1 条第三方弃用警告）；`git diff --check` 通过。`./scripts/build_macos_app.sh` 成功生成未安装的 `build/macos/Fieldwork.app`，`codesign --verify --deep --strict` 通过，包内版本为 0.54.0 / Build 63。未覆盖或替换 `/Applications/Fieldwork.app`。
