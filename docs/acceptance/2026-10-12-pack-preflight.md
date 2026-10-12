# Research Pack 接入预检

UI-06 新增实际接入预检操作，保留清单、组件登记和原有适配器列表。POST /api/v1/workspace/packs/{pack_id}/preflight 绑定当前清单sha256，拒绝未知Pack、过期摘要和额外字段。仅解析固定第一方模块AST，返回顶层入口存在性和源码摘要，不import或运行模块，不授予能力、不安装外部代码。不存在/无效源码显示失败。UI显示能力模板及未支持范围，刷新后的旧请求不覆盖当前界面。

3专项通过，日志build/pack-preflight-tests.log；JS语法及diff检查通过。隔离浏览器实际点击src预检，18页144显示组合及既有操作通过，零模型调用；build/pack-preflight-final-browser.log。安装版0.68.72正常重启后，原生实际点击src/web3/agent预检均显示成功及摘要/未授权能力/限制。九业务表与前轮逐行摘要一致、integrity ok；build/pack-preflight-native.json。

这是静态实现预检，不证明依赖健康、签名信任、Capability Drift、实际执行质量或完整安装。第三方安装更新/启停、权限授权、完整供应链治理仍未完成。未更改schema，未新建授权或模型调用。此前全仓947通过属本轮修改前，不冒称新代码全仓重跑。完整规划目标继续。
