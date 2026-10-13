# 安装与使用

以下步骤面向当前开发分支。


### 1. 获取开发分支

```bash
git clone --branch codex/v6-final-control-plane https://github.com/haipi001/fieldwork.git
cd fieldwork
```

已有仓库先保留本地改动，再切换和更新分支。当前原生构建面向 Apple Silicon macOS；其他操作系统不能据此视为已完成原生适配。

### 2. 准备 Python 环境

```bash
./scripts/bootstrap_local.sh
```

脚本创建 `.venv`、安装 `requirements.txt` 并进行导入检查。需要开发依赖时使用 `--dev`；需要重建虚拟环境时使用 `--recreate`。普通 bootstrap 使用版本约束文件，**不使用逐 wheel 哈希锁定**。

对于 **macOS arm64 + CPython 3.13**，仓库另有 [`requirements-macos-arm64-py313.lock`](https://github.com/haipi001/fieldwork/blob/codex/v6-final-control-plane/requirements-macos-arm64-py313.lock) 与锁定运行环境安装工具。使用方式和已验证范围见 [干净锁定环境验收](https://github.com/haipi001/fieldwork/blob/codex/v6-final-control-plane/docs/acceptance/2026-10/2026-10-13-clean-locked-runtime.md)；其他平台需自己的匹配锁文件。

### 3. 构建并打开本地 App

在安装 Xcode Command Line Tools、准备上述依赖后：

```bash
FIELDWORK_BUILD_PYTHON="$PWD/.venv/bin/python" ./scripts/build_macos_app.sh
open build/macos/Fieldwork.app
```

目前生成的 App 关联当前源码目录与 Python 环境；移动或删除这些路径会影响启动。构建使用本地 ad-hoc 签名，尚不是独立分发、公证完成的安装包。

日常从桌面 App 进入工作台。后台接口要求启动会话，桌面握手设置 HttpOnly Cookie；普通浏览器直接打开 uvicorn 地址不会自动获得此会话。CLI 与测试客户端需要显式 `FIELDWORK_SESSION_TOKEN`。详细配置、外部工具与模型接入见 [运行与维护](https://github.com/haipi001/fieldwork/blob/codex/v6-final-control-plane/docs/guides/operations.md)。

### 4. 完成一次研究

1. 创建项目，确认目标与授权范围。
2. 导入材料或使用隔离 Demo，查看观察和候选。
3. 配置可用工具与模型；需要时创建队伍并检查策略、预算及运行条件。
4. 调查候选、关联证据，安排适用的独立复验。
5. 核对影响、授权规则与验证回执，再预览和导出报告。

## 数据、备份与迁移

默认数据存于本地 `data/`。`FIELDWORK_DATA_DIRECTORY` 可在进程启动前指定已存在、当前用户所有且权限私有的绝对目录；无效显式配置停止启动，不静默回退。完整规则见 [存储位置配置](https://github.com/haipi001/fieldwork/blob/codex/v6-final-control-plane/docs/guides/STORAGE_LOCATION_CONFIGURATION.md)。

设置页提供目录/容量预检；离线工具可暂存文件并校验摘要，对部分操作性文件引用进行重映射。**预检或暂存成功不等于完成迁移**：外部写入者停机、全部路径审计、配置切换、真实 App 启动与回滚仍需要完整验收。不要把暂存目录直接当成已激活数据目录。

Git 同步保存源码、测试与文档，不包含生产数据库、凭据、运行日志、研究证据或 App 包。业务数据备份应使用专门的备份流程，不能用 Git 提交代替。
