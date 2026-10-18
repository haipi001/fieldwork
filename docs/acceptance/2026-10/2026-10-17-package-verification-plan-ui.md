# 软件包适用性复验衔接


## 2026-10-17 软件包适用性复验计划与页面操作

新增GET intelligence/matches/{id}/verification-plan及POST verification-request：从实际持久化Trivy产物、精确artifact包指纹、当前情报/Scope/Policy及图引用派生固定package_applicability_v1契约。预览不写表、不发请求；预览摘要绑定match整行、证据节点摘要、产物SHA与当前授权。提交重算摘要，原子创建确定性Claim/derived_from边/复验请求，随后复用已有独立Verifier材料守卫，失败整事务回滚；重复提交幂等。执行不自动发生，保留单请求离线执行门。该域仅包适用性，不能当成可利用漏洞或正式Finding。

保留全部原UI，在研究假设页有选中Campaign时增加软件包适用性复验区：分页匹配、准备、明确确认创建、执行指定request_id、回执页交接、中英文。项目/代际变化后异步结果不写回新上下文，网络/材料/空态明确显示。新增static/v5-package-review.js并接入模板和原页面，不删除页面或原控件。

26相关测试通过，build/package-verification-plan-final-tests.log：预览无写入、提交幂等、实际Seatbelt独立子进程回执、版本不符不确认、过期预览/变化产物/变化来源/合成产物阻断、后置契约失败无半成品。Trivy输出为明确持久化协议fixture，本轮不是实际Trivy扫描/真实模型发现。真实API浏览器模块操作通过，1实际独立回执/0正式Finding、0页面错误、无扫描/模型调用，build/package-verification-ui-retry.log；首次默认Playwright浏览器未安装，使用已有Chrome后通过。原研究页候选衔接/结果/跨项目/删除确认回归通过，build/package-verification-research-page-ui.log。JS/diff检查通过。

主库0活动Run/执行任务/模型调用/活动监控后正常退出确认端口关闭，再启动来源链接App加载；原生研究假设页可用，当前选中项目无Campaign，未为演示写主库数据或声称真实匹配点击通过。全量1353通过基线先于此增量，本次未重跑全仓；以上专项为当前证据。完整15Task/8Gaps和真实工具+模型自动发现/三域确认/报告验收继续未完成。
