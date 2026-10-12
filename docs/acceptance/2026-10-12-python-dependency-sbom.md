# Python依赖与许可证、SBOM — 2026-10-12

## 实现

智能体→MCP/供应链保留组件登记、修订历史和检索，追加读取本机运行Python依赖、声明许可证、安装状态和差异表，导出CycloneDX 1.6 JSON。

从requirements.txt出发，读取已安装发行包的METADATA/PKG-INFO，按当前环境marker和extra展开传递依赖，处理循环、缺包和重复安装。不导入被清点包。固定大小/数量上限超限失败，不静默截断。字段从同一读取的元数据文本解析并绑定文本摘要；此摘要不是安装文件的代码摘要。元数据仍不可信，不证明合规或发布者信任。

导出排除安装路径、原始来源URL/凭据、无关宿主包；UI使用已认证请求及Blob下载，保留原生保存流程。读取清单时生成revision，UI导出重查当前revision，变化409要求重读。添加明确运行依赖packaging==26.2；开发验证依赖jsonschema==4.23.0（本轮安装在隔离build虚拟环境，不修改宿主安装）。

## 当前证据

- build/dependency-inventory-tests.log：84相关通过，包括extra/marker/循环、无关包排除、缺包/歧义/版本/许可证、应用名冲突、URL凭据不泄露、图大小界限、不能解析的配置拒绝、导出revision冲突。
- build/dependency-inventory-browser.log：真实依赖读取/下载、430窄屏无根溢出、18页/144显示组合及原操作通过，零模型调用。矩阵不是所有组合全部业务操作的证明。
- build/native-python-bom-final.json：CUA原生读取状态、SavePanel选择build并保存；实际33组件/5核对项。build/native-python-sbom-validation.log、build/browser-python-sbom-validation.log及build/python-sbom-validation.log：官方CycloneDX 1.6 schema离线验证通过，依赖引用无悬空/重复。schema来源及字节摘要见tests/schemas/cyclonedx/SOURCES.json。
- Python编译、JavaScript语法与git diff --check通过；本轮没有重跑完整全量。
- build/dependency-inventory-native.json：主库9业务表摘要不变，schema41/integrity ok；安装App0.68.73/build151正常重载新入口。本轮没有数据库迁移。

## 实际未闭合项

原生当前环境：FastAPI要求0.115.12、实际0.140.1；uvicorn要求0.34.3、实际0.51.0；PyYAML要求6.0.2、实际6.0.3；python-multipart、openai发行元数据缺失。对缺失安装的许可证显示未确认，不能推断其未声明许可证。

SBOM限于声明的运行Python依赖和现有传递元数据；缺失依赖的传递树未知。原生二进制、系统库、外部工具、开发依赖、完整文件哈希/签名、许可证审核、干净环境重现和完整发行SBOM仍需推进。整体15 Task/8 Gaps及规划未完成。

规范来源：https://github.com/CycloneDX/specification/blob/master/schema/bom-1.6.schema.json ，https://cyclonedx.org/specification/overview/ 。
