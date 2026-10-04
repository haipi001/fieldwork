# V5-ORCH-02：活跃子进程父进程退出

## 范围、实现与版本

源码0.68.14/build92/schema25。新增parent_bound_child.py，共用于v5_http_transport.py及v5_verification.py；新增tests/test_parent_bound_child.py。不变更数据库结构。

监督器在spawn前创建匿名管道，只把读端传给隔离工作进程，写端由监督器单独持有并保持close-on-exec。工作进程的受信任包装代码在执行原脚本前检查父PID、描述符及FIFO类型，并启动等待管道的线程。父进程正常关闭或被SIGKILL后，最后一个写端关闭，子进程收到EOF后以exit125立即退出，不依赖父进程finally、定时PID查询或可复用的PID。缺失或无效绑定在工作脚本运行前失败。

原工作脚本单独compile/exec，保留__future__声明合法位置；整个包装脚本进入原有SHA、沙箱与执行证明链。HTTP进程只增加此读端，并继续只继承既有授权连接；没有新增网络许可。监督器正常结束/取消仍kill、communicate回收并在finally关闭写端。

## 直接验收

- 真实HTTP工作进程已连接localhost并进入服务器响应等待时，对自己创建的监督器SIGKILL；子进程在3秒内不再运行。
- 判定进程通过真实sandbox-exec执行fixture等待脚本并输出READY后，对自己创建的监督器SIGKILL；子进程在3秒内不再运行。
- 使用ps检查执行状态，不把僵尸状态当作仍在执行；孤儿由系统收养，测试不宣称原父进程能在死亡后wait回收。
- 两种父死亡专项2 passed，1.45秒。原HTTP/判定/跨进程恢复专项25 passed，17.47秒。最终全量756 passed/3 skipped，215.93秒，只有既有Starlette/AnyIO弃用警告。
- 实际开发包构建0.68.14/build92，codesign --verify --deep --strict和包版本一致检查通过；未覆盖安装App，安装检查点仍0.68.2/build80。

## 限制与后续

这覆盖上述两个受信任独立工作进程的存活绑定，不代表所有工具、任意不可信原生代码或整个进程树已经具备同样能力。沙箱process-fork仍禁止子进程自行创建后代；不宣称线程在CPU饥饿或操作系统停顿时具有硬实时响应保证。进程死亡时尚未提交的响应、临时文件及断电恢复仍是独立验收问题。

没有生产数据、外部目标或实际凭据。无迁移、无基准delta。回滚可撤销本增量代码与版本号，既有检查点和回执保留；历史脚本SHA不会回填。下一任务仍V5-ORCH-02：实际App/ASGI冷启动、其它执行类型父死亡边界、真实发现及跨claim/三域独立验证。15 Task和Known Gaps保留完整范围，目标仍进行中。
