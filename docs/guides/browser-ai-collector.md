# 浏览器 AI 采集器

0.42.0 提供 Chrome Manifest V3 采集器与本机接收接口。
安装与站点授权尚须用户确认；未安装或没有心跳时，Fieldwork 不显示已连接。

## 用户操作

1. 在 AI Agent Audit 开始本机监控。
2. 展开“浏览器 AI 采集”，下载 ZIP 并解压。
3. 在 Chrome 扩展页面加载解压目录，点击扩展，选择 AI 站点并授权。
4. 后台自动记录；可在扩展停止采集，或在 Fieldwork 撤销连接。

配对信息已经包含在下载包中，不用填写服务器、Token 或审计 ID。
下载包仅适用于当前监控，包含本机会话凭据，不应分享或上传。
监控结束会撤销所有对应配对；新监控需要重新下载。

## 覆盖与证据边界

- 支持配置内的 ChatGPT、Claude、Gemini、Copilot、Perplexity、Grok、DeepSeek、豆包、元宝域名。
- 只在用户授权的 AI 域名上监听导航、子页面与 fetch/XHR 请求的开始、完成或错误。
- 仅保留域名、HTTP 方法、时间、阶段和状态码；完整 URL、路径、查询参数、请求/响应正文、Cookie、提示词不进入本机接收接口。
- 不安装内容脚本，不读 DOM、聊天正文、历史记录或键盘输入，不修改或阻止请求。
- 外部资源、其他域名、WebSocket 内容、其他浏览器与内嵌浏览器不在当前覆盖内；请求元数据不等于 AI 工具调用或模型生成行为。
- 记录不能证明动作由 AI 而非人类发起。未签名浏览器元数据保持非独立证据，不能单独产生 Verified Finding。
- 连接状态只证明接收接口近期收到心跳，不证明每个站点、每个请求都被捕获。

## 本机接收与重试

- 接收接口检查 loopback 客户端、Host 与 Origin；配对凭据随机生成，数据库只存哈希。
- 严格字段模式拒绝 URL/正文等额外字段，只接收已知域名，单批最多 100 条。
- 配对关联固定审计，序号重试幂等，重试批次冻结，避免丢响应后误删后续事件。
- 队列与待处理回调有界，重试、溢出、暂停丢弃与配对失效在扩展显示。
- 暂停期间与恢复前积压不补录；恢复后重新开始接收。停止监控自动撤销配对。

## 验证状态

Python 集成测试覆盖包生成、凭据不泄漏、来源限制、隐私字段拒绝、幂等重试、暂停、停止与撤销。
Node 测试覆盖元数据裁剪、丢响应重试批次与未选站点排除。
这些测试尚不能替代获授权后的真实 Chrome 端到端验证。

实现依据：[Chrome webRequest](https://developer.chrome.com/docs/extensions/reference/api/webRequest)、
[扩展跨域请求](https://developer.chrome.com/docs/extensions/develop/concepts/network-requests)、
[可选权限](https://developer.chrome.com/docs/extensions/reference/api/permissions)。
