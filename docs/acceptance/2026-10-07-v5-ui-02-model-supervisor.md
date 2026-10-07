# UI-02 模型传输监督器增量（2026-10-07）

## 实现与边界

模型 HTTP 请求由可信 standalone Python 子进程完成；stdin 只传脱敏请求和数值预算，不继承数据库句柄或完整环境。child 再校验数字 loopback/显式端口与禁止重定向，输入 64KB、输出 256KB 上限。

监督器以单调时钟计算总预算（含启动），至多每 100ms 轮询实际任务租约、attempt、授权与图材料；超时、取消、材料变化或数据库检查错误进入 finally，kill 并 communicate 回收 child。数据库检查使用 busy_timeout=0，锁冲突停止调用。复用已存在 parent lifetime pipe，监督器死亡关闭 writer 后 child 自行退出。

这是可信模型传输的进程生命周期控制，不是任意不可信工具的沙箱。关闭连接不能证明模型服务端已经停止推理。实际用量缺失仍是 unknown，不能作为零消耗证明。

## 直接验收

43 项相关测试通过：test_v5_model_transport、test_v5_workers、test_v5_runtime、test_v5_evolution、test_v5_continuous、test_v5_team_plan。

- 实际 HTTP fixture 持续 25ms 返回一字节，总耗时远超预算，监督器按墙钟结束，child 已退出、life writer 关闭、服务端观测连接断开。
- 实际取消 API 中断已发送模型请求的 local tick，任务保持 cancelled，不写图/结果或伪造已知用量。
- 实际监督进程 SIGKILL，child 退出且服务端连接断开。
- Profile 实际 HTTP 调用、用量、重定向与结构化语义回归仍通过。
- 首次 400ms 冷启动测试未能到达服务器，改为 1500ms 预算确保慢响应场景确实建立连接；仍断言总耗时 <3s，未放宽生产预算。

随后只增加 busy_timeout=0，重新执行 transport/workers 专项。原生 App 本轮调用操作验收尚未完成；没有真实模型质量证明，没有安装版更新。

## 迁移和回滚

无 DDL/schema 或生产数据变化。新增 v5_model_transport.py、v5_model_transport_child.py，替换 v5_workers.py 本地直接 urllib 段。回滚仅恢复该段与移除新增模块；保留此前路由、Profile、时长及 UI 增量。工作区未提交。

## 剩余

继续原子 durable 调用预留、跨重试累计/小时词元/费用、未知用量和崩溃恢复，再接通 UI-01 research-worker 与真实模型研究。整个 UI-02 与原完整目标仍未完成。
