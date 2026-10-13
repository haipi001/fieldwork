# 依赖与上游项目


Fieldwork 的能力建立在下列库、工具及公开设计参考之上。Python 库由环境安装；外部工具需按所用能力另外准备；参考项目不因列在这里就成为默认运行依赖。仓库没有将所有这些项目打包进 App。

### Python 运行与开发依赖

直接声明以 [`requirements.txt`](https://github.com/haipi001/fieldwork/blob/codex/v6-final-control-plane/requirements.txt) 和 [`requirements-dev.txt`](https://github.com/haipi001/fieldwork/blob/codex/v6-final-control-plane/requirements-dev.txt) 为准：

| 项目 | 用途 | 声明版本 |
| --- | --- | --- |
| [FastAPI](https://github.com/fastapi/fastapi)、[Uvicorn](https://github.com/encode/uvicorn) | 本地 API、ASGI 服务 | `fastapi==0.115.12` / `uvicorn[standard]==0.34.3` |
| [Jinja2](https://github.com/pallets/jinja)、[python-multipart](https://github.com/Kludex/python-multipart) | 页面模板与表单/文件上传解析 | `3.1.6` / `0.0.20` |
| [PyYAML](https://github.com/yaml/pyyaml) | YAML 配置与材料解析 | `6.0.2` |
| [OpenAI Python SDK](https://github.com/openai/openai-python) | 模型提供方与兼容接口客户端 | `>=1.68,<2` |
| [Playwright Python](https://github.com/microsoft/playwright-python) | 浏览器自动化与工作台验收 | `>=1.48,<2` |
| [HTTPX](https://github.com/encode/httpx) | Python HTTP 客户端 | `>=0.27,<1` |
| [cryptography](https://github.com/pyca/cryptography) | 签名与密码学操作 | `>=43,<49` |
| [certifi](https://github.com/certifi/python-certifi)、[packaging](https://github.com/pypa/packaging) | CA 证书集合与依赖版本解析 | `2026.7.22` / `26.2` |
| [pytest](https://github.com/pytest-dev/pytest)、[jsonschema](https://github.com/python-jsonschema/jsonschema) | 开发测试与 JSON/SBOM schema 验证 | `9.1.1` / `4.23.0`，开发依赖 |

这张表列出直接依赖；Pydantic、Starlette 等传递依赖随解析结果进入运行环境。匹配平台的 wheel 版本和哈希见运行锁文件；当前安装元数据可通过工作台供应链页与 SBOM 查看。范围约束不等于精确锁定。

### 按能力选用的外部工具

工具登记与探测入口见 [`capability_registry.py`](https://github.com/haipi001/fieldwork/blob/codex/v6-final-control-plane/capability_registry.py)。登记、检测到命令、实际执行及独立复验是不同状态，具体能力需要相应安装、配置和授权。

| 项目 | Fieldwork 中的用途 |
| --- | --- |
| [Nuclei](https://github.com/projectdiscovery/nuclei)、[Nuclei Templates](https://github.com/projectdiscovery/nuclei-templates) | 模板扫描与模板材料；扫描器和模板集合分别管理 |
| [Katana](https://github.com/projectdiscovery/katana)、[ProjectDiscovery httpx](https://github.com/projectdiscovery/httpx)、[Subfinder](https://github.com/projectdiscovery/subfinder) | 爬取、HTTP 探测与子域发现；此 `httpx` CLI 与 Python HTTPX 是两个项目 |
| [Semgrep](https://github.com/semgrep/semgrep)、[Gitleaks](https://github.com/gitleaks/gitleaks)、[Trivy](https://github.com/aquasecurity/trivy) | 静态分析、秘密检测、依赖/镜像分析入口 |
| [Foundry](https://github.com/foundry-rs/foundry) | Forge 构建与属性测试、Anvil 本地 EVM、Cast RPC 工具；编译器需匹配项目配置 |
| [Slither](https://github.com/crytic/slither)、[Aderyn](https://github.com/Cyfrin/aderyn) | Solidity 静态分析入口 |
| [Echidna](https://github.com/crytic/echidna)、[Medusa](https://github.com/crytic/medusa)、[Halmos](https://github.com/a16z/halmos) | 属性模糊测试与符号测试入口 |
| [pentest-ai](https://github.com/0xSteph/pentest-ai) | HTTP oracle 与 proof capsule 相关外部能力入口 |
| [Strix](https://github.com/usestrix/strix)、Shannon | 可选 Agent 适配；需要各自模型配置与 Docker 条件，不是默认研究任务的必需工具 |

原生外壳还使用 macOS Cocoa、WebKit、Security 与 Swift 工具链。模型服务、RPC、情报源和浏览器采集器属于额外配置项，Python 环境安装不会自动配置它们。

### 设计参考与致谢

历史设计资料记录了 [PentAGI](https://github.com/vxcontrol/pentagi) 的控制平面思路、[SWE-agent](https://github.com/SWE-agent/SWE-agent) 的 Agent-computer interface、[Argus](https://github.com/R0x7e/Argus) 的搜索/决策思路，以及 Strix、pentest-ai 的运行与证明组织参考。Web3 参考材料还包括 [Chimera](https://github.com/Recon-Fuzz/chimera)、[crytic/properties](https://github.com/crytic/properties)、[OpenZeppelin Contracts](https://github.com/OpenZeppelin/openzeppelin-contracts) 等属性测试与合约基线项目。

完整历史清单见 [开源参考矩阵](https://github.com/haipi001/fieldwork/blob/codex/v6-final-control-plane/FINAL/SRC_AI_Security_Research_OS_FINAL_2026-08-27/references/OPEN_SOURCE_REFERENCE_MATRIX.md)。其中列出的 ZAP、CodeQL、OSV-Scanner、Kontrol、revm 等规划/参考项，不能据此认定为当前已接线或必需依赖。项目关系依据现有声明与设计资料归类，不表示已完成逐文件代码来源审计。

各依赖、工具、模板、规则与参考材料的许可证以所用具体版本的上游 LICENSE/NOTICE 为准。当前登记中的许可证名称属于元数据；完整版本来源、再分发要求及发行许可证核对仍在开发规划中，不能将这份致谢表视为已完成的合规审查。
