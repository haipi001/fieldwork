# 仓库执行控制边界

## 2026-10-16 仓库与通用工具链控制边界及本机模型选择

用户目标：继续完整开发规划，保留所有UI；用户回复真实模型选择：使用本机模型服务。
实现：通用工具链只从queued/running进入执行，逐能力及目标、原生模型前、候选关联前检查running，完成状态条件更新成功才写完成事件；显式API启动paused/completed时先条件置queued，保留原重新执行入口。started_at保留首次时间。仓库检出先进入running，Git clone/commit后、代码工具后及Web3分析后检查运行状态，暂停/停止/预算/超时不继续下一阶段或误报完成；Git commit读取非零/空/超时/缺程序进入明确失败，分析错误不覆盖用户控制状态。
验证：新增27仓库控制专项通过，build/repository-control-focused-tests.log（6.49秒，1既有警告）。首次较早测试有两条断言误用旧表名run_events，已修正为实际run_events_v2。首轮组合191通过/2准备错误（90.65秒），其中明确database or disk is full及unable to open database file，记录build/repository-control-regression-tests.log；磁盘当时约1.4GiB可用。顺序重跑同一组合193全部通过/1既有警告（79.01秒），build/repository-control-regression-retry.log。未清理用户数据。替代Git/分析器验证状态控制，不是实际远程仓库研究质量验收。暂停不强杀当前工具进程，也未证明快速暂停恢复时旧执行者与新执行者的代际隔离。
安装App：无活跃研究/租用任务/模型调用后，CUA正常退出确认8000停止监听，重启源码关联0.68.75/build153，health ready。研究项目/运行队列/智能体三入口可打开无加载错误，停留智能体清单，未创建研究。九类业务表重启前后计数/摘要一致、quick_check ok，build/repository-control-native-before.json与-after.json。未重打独立发行包或Git提交推送。
本机模型：用户明确选择本机服务；只读检查11434/1234/8080无监听，直连枚举均连接失败（初次受环境代理影响超时，禁用代理后确认）。常用安装位置无Ollama/LM Studio，Ollama CLI未找到，已有模型manifest目录为空。尚不能实际推理，不自动下载大模型。已异步询问应用名称或自定义服务地址/模型名，等待具体信息；仍可继续其他工程工作，整体目标不标完成或阻塞。
同步状态：当前用户原话、实现、测试/安装证据、本机模型偏好与待输入信息同步八个入口，无秘密或真实目标信息。
