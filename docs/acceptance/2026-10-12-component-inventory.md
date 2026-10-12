# 组件检索到声明历史

扩展页与Agent MCP子页保留登记和手动ID读取入口，新增已登记组件检索：身份/类型/名称/来源/版本过滤，固定50条页大小，从结果直接打开声明详情与修订历史。后端GET /api/v1/workspace/components按更新时间和ID稳定排序，limit上限100，offset非负，关键词为字面匹配不作SQL通配。输出不包含原始JSON权限/元数据，来源凭据脱敏。重复请求序列及脱离页面的响应不能覆盖新界面。

5组件专项及70相关回归通过；build/component-inventory-tests.log、build/component-inventory-regression.log。第一次隔离浏览器检索/打开/修订/历史及18页144显示组合通过；build/component-inventory-browser.log。最后52条记录浏览器实际翻页50→2→50通过，检索直接打开声明/修订历史及18页144显示组合通过，零模型调用；build/component-inventory-final-browser.log。JS/Python语法及diff检查通过。

原生工具仍报告电脑锁屏，前轮解锁请求未收到确认；不绕过锁屏，不宣称本轮已原生加载。主库无登记组件，不生成主库演示记录。第三方实际安装更新、权限和供应链信任仍未完成；完整开发规划继续。全仓947结果早于本轮实现。
