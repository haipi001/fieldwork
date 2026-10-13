# 原生延迟启动自动恢复 — 2026-10-12

## 实现

- 首段启动探测后，若当前App拥有的Process仍在运行，显示“仍在启动”，每5秒继续核对健康端点。后台就绪后自动加载工作台；不再次启动或终止现有进程。
- 实例UUID和版本匹配规则保留；陌生/旧版服务阻断，不自动接入。进程已退出时显示真实停止状态，等待明确重试。
- 每次启动/手动重试采用新generation；成功、冲突和退出取消旧generation。Cookie完成回调、HTTP结果和定时回调均核对generation，防止旧回调覆盖新页面。
- 保留既有重试入口、会话Cookie、父存活管道、退出后端清理及18导航/6 Agent子页。

## 证据

- build/startup-recovery-tests.log：30相关通过，含编译执行生产Swift策略、延迟就绪/退出/陌生服务/重试旧回调拒绝，以及真实后台会话/父管道测试。
- build/startup-recovery-build.log：真实生产包构建与签名通过。
- 隔离原生fixture延迟38秒启动：CUA直接观察“仍在启动”→自动加载“Delayed startup acceptance”，未点击重试；同一个唯一后台子进程保留。正常退出后后台及端口均关闭。证据build/startup-recovery-delayed-process.json；fixture源码留存build/startup-recovery-fixture-source/。
- 该fixture只证明原生启动恢复，不是实际研究质量或主数据库验收。

## 本机部署

已正常退出旧App并核对8000端口关闭，保留build/macos/rollback-06873-startup-recovery/Fieldwork.app，安装匹配0.68.73/build151的生产根目录包，签名校验通过。本包仍链接项目源码和本机Python。

本机实际Python初始化目录阻塞的根因尚未消除；本改动接通延迟后的自动恢复，不证明冷启动耗时已经达标。正式目录迁移、发布以及完整规划仍保持进行中。

实际安装版观察“正在启动”→“仍在启动”→无需手动操作自动打开工作台；本轮仅观察，没有点击重试。同一后台PID/父进程关联保持，18导航逐项打开无加载失败，设置0.68.73/151；9业务表摘要与上轮相同、schema41/integrity ok。证据build/startup-recovery-main-process.json。没有重跑完整Python全量；本轮仅Swift启动逻辑变更，30相关与原生直接证据为当前范围。
