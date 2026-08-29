# 02 — 极简前端规格

## 一级页面：只保留 5 个

### 1. 新建分析 `/new`

默认首页。

元素只有：

- `传统 SRC | Web3 / Immunefi`
- 一个超大 Target 输入框
- Scope / Program Rules 卡片
- “开始分析”主按钮
- 最近项目

高级配置收进 `高级设置` 折叠区。

### 2. 分析过程 `/runs/:id`

核心界面不是 terminal，而是 Timeline：

```text
目标解析
Scope
攻击面
自动分析
数据处理
漏洞验证
影响判断
报告
```

页面分三层：

A. 默认层：
- 当前进度
- 已分析资产
- 已发现候选
- 已验证漏洞
- 时间/预算
- Pause / Resume / Stop

B. 展开层：
- Agent activity
- tool activity
- coverage
- evidence

C. 专家层：
- raw logs
- command traces
- protocol graph
- request/response
- fuzz corpus
- fork trace

### 3. 漏洞结果 `/findings`

默认只显示：

**Verified Findings**

每张卡：
- Title
- Severity
- Target
- Impact
- Verification
- Evidence count
- Report readiness

候选问题单独放：
`候选 / 待验证`

用户不会把 scanner noise 当漏洞。

### 4. 报告中心 `/reports`

选 Finding → 选平台：

- Generic SRC
- 中国 SRC 通用
- HackerOne
- Bugcrowd
- Intigriti
- Immunefi
- Markdown
- HTML
- JSON
- SARIF

显示“材料完整度”：

```text
Scope               ✓
Affected target     ✓
Reproduction        ✓
PoC                 ✓
Impact              ✓
Evidence            ✓
Severity            ✓
Platform fields     2 missing
```

只有用户自己点击导出。
v1 不自动外部提交。

### 5. 设置与工具 `/settings`

普通用户默认只看到：
- Model provider
- Budget
- Sandbox status
- Tool health

专家模式才显示：
- Tool Registry
- Skills
- MCP
- Agent policies
- Runtime
- environment variables

## Mode Switch

顶栏固定：

`传统 SRC | Web3 / Immunefi`

切换的是工作域和 Engagement 列表，不改变已有 Engagement 的 mode。

## 必须合并的旧页面

旧的 42 屏应映射为：
- intake → 新建分析
- recon / map / agent / evidence / fuzz / fork → 分析过程
- candidate / oracle / finding → 漏洞结果
- report / platform adapter → 报告中心
- tools / skills / MCP / runtime / policy → 设置与工具

不要再恢复为 42 个一级页面。
