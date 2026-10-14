# Python 后端

本目录集中保存 API、研究域、任务编排、模型运行、验证及存储模块。模块名保持兼容；仓库资源（static、templates、fixtures、data）仍以仓库根目录定位。

- `app.py` / `final_core.py`：API 与共享业务逻辑。
- `traditional_*.py` / `web3_*.py` / `agent_*.py`：研究域与采集。
- `v5_*.py` / `v6_*.py`：编排、验证、运行与控制平面。
- `storage_*.py`：数据位置与维护。
- `report_adapters/`：固定报告字段配置，作为源码资产随仓库保存。

根目录 `app.py` 和（存在时）`desktop_server.py` 是兼容启动器，不包含业务实现。CLI 调用其它模块时设置 `PYTHONPATH=backend:.`，或使用已配置源码路径的维护脚本。pytest 和原生构建脚本已配置后端路径。
