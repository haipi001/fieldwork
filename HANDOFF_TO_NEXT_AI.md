# 给接力 AI 的交接说明

更新：2026-09-11。

## 项目位置

完整工作区：`/Users/lizekai/Documents/ChatGPT/SRC漏洞`。
已安装桌面壳：`/Applications/Fieldwork.app`。
当前桌面壳依赖工作区源码及 `/Users/lizekai/anaconda3/bin/python3`，不是独立完整安装包。跨机器复制后，需要修正 macos 下启动器路径或按开发方式配置运行时。

## 首先阅读

1. `DEVELOPMENT_MASTER_PLAN_2026-09-11.md`：最新完整开发计划，尚未执行。
2. `APPLICATION_ASSESSMENT_2026-09-11.md`：最新实际评估、已验证证据与安全缺口。
3. `UI_REVIEW_2026-09-11.md`：最近 UI 交付。
4. `README.md`、`OPERATIONS.md`、`requirements.txt`：启动和维护。
5. `PRODUCT_COMPLETION_STATUS.md`、`WEB3_CAPABILITY_PLAN.md`：历史实现说明，部分旧段落已过时，以最新评估为准。

## 当前状态

运行时 0.32.1 build 32，schema 11；桌面启动壳仍为 0.32.0。上一轮 153 项回归测试通过；评估/计划轮未重新运行。工作区包含大量未提交修改和新增文件，不能只 git clone 或复制已跟踪文件，否则会遗漏当前成果。接力前查看 git status，不要 reset/clean 或覆盖用户修改。

应用为 FastAPI/Python + static JS/CSS + templates + Swift WebView。入口 app.py；核心 final_core.py；Web3 web3_analysis.py/web3_ast.py/web3_practice.py；前端 static/、templates/；macOS 构建 scripts/build_macos_app.sh。

正式数据在 data/src_control.db；其他数据库包含历史备份。评估时正式库有 77 条候选、14 条归档、0 条正式漏洞。不要将 QA 记录写入正式数据库，不要向历史目标自动发起测试。

## 接力优先级

先按主计划 M0/M1 加固 API 鉴权、Host/Origin、桌面服务身份绑定、不可信项目执行隔离和资源上限，再推进候选闭环、真实协议经济证明、独立基准、自包含安装。

确认的风险：Forge 直接采用项目配置且继承主机环境，临时配置 ffi=true 会生效；本地 API 无会话认证；启动器仅以固定端口 HTTP 200 判断服务身份。未执行恶意命令或完整跨站攻击验证。不要把风险推断改写成已完成利用。

## 复制方式

同机接力可直接打开原工作区。跨目录/机器接力复制整个工作区，包括未跟踪源码；.venv、__pycache__、.pytest_cache 可省略，目标机器重新建立环境。只复制 .app 不够。

data/、logs/、工作缓存和配置可能含目标资料或凭据。仅交给可信的本地接力环境；如果上传第三方 AI，先做脱敏源码副本，排除这些目录并检查其他文件。不要把整个生产数据目录直接上传。

如需携带正式数据，先正常退出 Fieldwork 并确认后端和任务停止，再复制；活动 SQLite 应使用备份接口，不要只复制主 .db 而遗漏 WAL。保留原件，先在副本启动验证。外部 Forge/Slither/Anvil、模型配置等不保证随目录携带，按 requirements 和能力检查重建。

用户偏好：用中文，直接推进已授权工作；保留现有数据，明确测试证据和未完成项。原机器记忆库为 `/Users/lizekai/Documents/仓库/Codex/记忆库`，包含其他私人主题，不需要整体交给新 AI；当前交接以本项目文档为准。
