# Web3 合约自动导入

输入合约地址或受支持的浏览器 `/address/{address}` URL，Fieldwork 获取公开已验证源码，生成独立 Foundry 工程，执行真实隔离编译。原有页面与功能保留。

## 最短 App 操作

1. **新建研究 → Web3 Security**，填写合约浏览器 URL，保存并核对授权。URL 会自动识别链；裸地址需在导入区明确填写 Chain ID。
2. 进入“确认实际执行”后系统**自动准备源码**，恢复已有任务或启动获取、工程恢复和编译，并填写“本地源码目录”、自动重新预检。不必额外点击获取；“重新获取”用于手动刷新。
3. 默认保留 **只读资料研究**，核对预算后开始。导入资料与源码结构分析（合约、入口、调用关系、风险线索）会进入原有 Run、Observation、Evidence、研究图谱和报告流程；不调用模型、不运行 Fork、不执行属性测试、不晋升漏洞。

需要链上部署对齐时，在导入前填写 **Web3 JSON-RPC / 本地 Fork 节点**，再次点击自动获取。它会检测链、固定区块和标准代理，并获取正确实现合约的源码。完整字节码匹配才可自动生成已对齐快照；有解释的差异仍需人工审查。实际深度研究需取消只读选项、满足既有授权、对齐、Fork 和预算条件后确认执行。

**源码获取、服务端源码验证、编译、部署对齐、研究完成、漏洞确认是不同状态。** 指定目标若编译成功但未配置 RPC，显示“已编译；尚未检查链上部署”，不是已验证漏洞。

## 首次配置

Sourcify v2 不需要 API Key，优先使用。只有 Sourcify 不可用时才使用 Etherscan V2。可在启动 App 的终端环境设置 `ETHERSCAN_API_KEY` 与 `ETHEREUM_RPC_URL`，不要把真实值写进仓库、报告或终端共享输出。

```bash
cd /Users/lizekai/Documents/ChatGPT/SRC漏洞
read -s 'ETHERSCAN_API_KEY?Etherscan API Key（可直接回车跳过）: '
export ETHERSCAN_API_KEY
read -s 'ETHEREUM_RPC_URL?Ethereum JSON-RPC HTTPS URL（可直接回车跳过）: '
export ETHEREUM_RPC_URL
# 先正常退出已经运行的 App，再从该终端启动；Finder 启动不会继承此终端环境。
/Applications/Fieldwork.app/Contents/MacOS/Fieldwork
```

App 的 RPC 文本框只用于本次任务，RPC URL 不持久化。CLI `--rpc-env` 从指定环境变量读取。不要把 Etherscan 网页 URL 填入 RPC；界面会立即指出错误。

## 可复制的 CLI

以下三个入口调用与 App 相同的业务服务。`source` 不要求 RPC；`build` 恢复工程并编译；`align` 仅用只读 RPC。

```bash
cd /Users/lizekai/Documents/ChatGPT/SRC漏洞
.venv/bin/python scripts/web3_auto_import.py --chain-id 1 --address 'https://etherscan.io/address/0x73E35C5c35A274E34AdE6EB13cC7f62aEE323728?utm_source=immunefi' --mode source
.venv/bin/python scripts/web3_auto_import.py --chain-id 1 --address 0x73E35C5c35A274E34AdE6EB13cC7f62aEE323728 --mode build
.venv/bin/python scripts/web3_auto_import.py --chain-id 1 --address 0x73E35C5c35A274E34AdE6EB13cC7f62aEE323728 --mode align --rpc-env ETHEREUM_RPC_URL
```

无 RPC 的 `align` 保留编译结果，返回 `chain_unchecked`，退出码为 2。成功源码收集、成功 build 的退出码为 0；失败/降级为 2。`--refresh` 强制重新获取，默认同链、地址和源码指纹复用缓存与编译结果。

## 工程与依赖

工程位于当前批准的数据根目录下 `web3-imports/{chainId}/{源码地址}/{fingerprint}/`，代理输入地址与实现源码地址分别记录。每个工程包括：

| 文件 | 用途 |
| --- | --- |
| `sources/` | 保留逻辑路径与多文件 import，禁止路径穿越 |
| `source-manifest.json` | 来源、查询时间、原响应 SHA256、各源码 SHA256、ABI、元数据及编译配置 |
| `standard-input.json` | 原始逻辑路径和精确编译设置 |
| `foundry.toml` | 隔离 Foundry 工程，FFI 禁用、离线编译 |
| `compile-log.json`、`build-result.json` | Forge 真实结果与缓存状态 |
| `standard-output.json` | 精确 solc 编译结果与 immutable/link references |
| `out/` | Forge 编译产物、AST 和 build info |

点击 **打开本地工程** 可通过受会话保护、仅允许当前导入目录的接口打开 Finder。未完成导入时按钮不可用。

工具检查：

```bash
.venv/bin/python --version
~/.foundry/bin/forge --version
~/.foundry/bin/cast --version
~/.foundry/bin/anvil --version
```

缺少精确 solc 时，macOS 自动下载 `ethereum/solc-bin` 官方二进制，并以官方列表 SHA256 和 commit 校验后安装到现有 `.solc-select/artifacts/`。不执行远程安装脚本，不修改 pragma。Forge、源码和 Standard JSON 编译在现有 `sandbox-exec` 边界中运行，禁止网络与 FFI，限制输出、单文件大小和超时。此边界是本机沙箱，不是虚拟机。

不自动 clone GitHub 仓库或安装来源不明确的依赖。缺失的源码依赖、未知编译设置/库链接或不支持的语言需要人工处理。

## 状态与常见错误

| 状态或错误 | 操作 |
| --- | --- |
| `chain_required` | 裸地址填写 Chain ID；不要默认主网 |
| `chain_id_mismatch` | 修改所选链或节点，部署对齐和 Fork 保持阻止；公开源码仍可获取 |
| `unverified_source` | 查看服务降级原因；设置 Etherscan Key 后重新获取；资料保留，可做只读研究 |
| `compile_failed` | 查看真实编译日志；提供准确缺失依赖或精确版本，不改 pragma |
| `compiler_download_failed` | 联网重试，或预先安装官方精确 solc |
| `alignment_blocked` | 查阅链、区块、代理和 matcher 证据；不能据此确认链上漏洞 |
| `unknown_proxy_pattern` | Diamond/非标准代理需要人工审查，不猜实现地址 |
| `explained_equivalence` | compiler-proven immutable/完整 CBOR 区间有解释；仍需独立部署证据，不自动视为 aligned |
| `interrupted` | App 重启保留任务状态；再次获取/重试复用缓存 |
| `cancelled` | 当前有界阶段结束后停止；已获取文件保留 |

EIP-1967、Beacon、EIP-1167 代理以固定区块只读查询，最多四层跳转，循环/深度超限阻止。库链接地址必须精确匹配，不任意屏蔽 library 差异。

## API 与验证

接口继承现有本地来源与会话保护：`POST /api/v1/web3/contract-imports`，`GET /{id}`，`POST /{id}/retry`、`/cancel`、`/attach`、`/open`。创建任务绑定 Engagement 地址与链，不能跨项目绑定；RPC URL/Key 不进入任务 JSON、快照或 Run 配置。重启中断任务保留材料并可重试。只读 Run 使用 `execution_mode=read_only` 和 `contract_import_id`，必须先人工确认 Scope。

```bash
PYTHONPATH=. .venv/bin/python -m pytest -q tests/test_web3_auto_import.py
.venv/bin/python scripts/check_web3_auto_import_ui.py
PYTHONPATH=. .venv/bin/python -m pytest -q
FIELDWORK_BUILD_PYTHON="$PWD/.venv/bin/python" ./scripts/build_macos_app.sh
open build/macos/Fieldwork.app
```

目前尚未以真实授权 RPC 验收指定主网合约的部署对齐、真实 Beacon/多层 Proxy 或 Fork 深度研究。Etherscan 在线备用源需要用户 Key；离线响应格式与错误行为已有测试。不能把这些项目写成已实测成功。

来源协议：[Sourcify v2 官方 API](https://docs.sourcify.dev/docs/api/)、[Etherscan V2 官方源码接口](https://docs.etherscan.io/api-reference/endpoint/getsourcecode)。

## 本机启动验收补充（2026-10-14）

已安装版本 0.68.74/build 152，关联当前源码，沿用旧 App 的锁定 CPython 3.13 运行时。终端直接启动已验证进入真实工作台，health=ready。该机器从 Finder/LaunchServices 启动仍曾卡在 Python 读取/导入阶段（零服务日志），不能声明 Finder 冷启动通过；未扩大 macOS 权限。遇到此情况先正常退出 App，再执行 `/Applications/Fieldwork.app/Contents/MacOS/Fieldwork`。独立分发、公证不属于此次安装结果。

## 0.68.75 自动化优化

执行弹窗自动准备新目标，并恢复持久化任务；已有编译结果不重复下载或编译。中断任务在重新进入时复用缓存继续准备，已取消任务不自动重启。主网项目选择自动模式时，可使用启动环境中的 ETHEREUM_RPC_URL 自动进行只读对齐；RPC 凭据不返回前端。实际研究执行仍需核对授权及预算后确认。

原生启动使用 Python `-I` 与显式入口加载，避免初始化阶段搜索 Documents 项目目录。新版本已通过 LaunchServices 的隔离原生冷启动，替代上一节 0.68.74 的启动受阻结论；最终安装状态见本轮验收补充。

0.68.75/build153 已安装到 `/Applications/Fieldwork.app`，最终 LaunchServices 冷启动和实际工作台通过，可正常打开 App；完整回归1115通过/3跳过，授权与研究执行确认保留。
