# NodeBridge

NodeBridge is a Windows-first desktop GUI for browsing and managing files on SSH-accessible nodes. It uses Python 3.12, PySide6, and Paramiko. Other desktop platforms may be supported later.

NodeBridge 是面向 Windows 的 SSH 多节点文件管理桌面应用，采用 Python 3.12、PySide6 和 Paramiko；未来可能支持其他桌面平台。

## Current features / 当前功能

- Browse local files, a directly connected jump node, and multiple indirectly connected nodes; inspect one indirect node or a merged directory view.
- Copy files by dragging between panes, distribute to connected indirect nodes, and collect from them with node-name suffixes to the direct or local pane; confirm remote deletion before it runs.
- Open multiple xterm.js SSH terminals per node, organize connection batches into editable Terminal Groups, broadcast within the active group, and view group output together in node order.
- Save site profiles with a choice of plaintext, master-password-protected, or no password storage.

- 浏览本地、直连跳板节点及多个间接节点的文件，可查看单个间接节点或合并目录。
- 通过拖放复制文件，可向已连接的多个间接节点并行分发，也可按节点名后缀汇集到直连或本地面板；远程删除前须确认。
- 每个节点可打开多个 xterm.js SSH 终端；连接批次形成可重命名的终端组，广播仅限当前组，并可按节点顺序联合查看输出。
- 保存站点资料，并选择明文、主密码保护或不保存密码。

The project is under active development. Multi-node operations and terminal broadcast still need live verification on the owner's cluster; see [current status](docs/CURRENT_STATUS.md) for the exact implementation boundary.

项目仍在开发中。多节点操作和终端广播尚需在项目所有者的集群上实测；准确的实现范围见[当前状态](docs/CURRENT_STATUS.md)。

## Run from source / 从源码运行

Create the Python 3.12 environment from `environment.yml`, then run on Windows PowerShell:

按 `environment.yml` 创建 Python 3.12 环境，然后在 Windows PowerShell 中运行：

```powershell
conda env create --prefix .\.conda-env --file environment.yml
.\.conda-env\python.exe -m nodebridge
```

An experimental portable Windows build is available from `scripts/build_portable.py`. It bundles Python and writes its settings, sites, and logs beside the executable in `data`. Clean-machine and live-cluster verification remain pending.

现有 `scripts/build_portable.py` 可生成试验性的 Windows 绿色版，自带 Python，并将设置、站点资料和日志保存在程序旁的 `data` 目录。仍需在干净的机器和实际集群验证。

## Project structure / 项目结构

| Root entry / 根目录项目 | Purpose / 用途 |
| --- | --- |
| `nodebridge/` | Application source and bundled interface assets. / 应用源码及界面资源。 |
| `tests/` | Automated tests. / 自动化测试。 |
| `docs/` | User and developer guides, architecture, status, decisions, and roadmap. / 使用与开发指南、架构、状态、设计决定和待办。 |
| `scripts/` | Build tools for the portable edition. / 绿色版构建脚本。 |
| `design/` | Design references and app icon generation. / 设计参考资料与应用图标生成脚本。 |
| `environment.yml` | Python 3.12 Conda environment and dependencies. / Python 3.12 Conda 环境及依赖。 |
| `nodebridge.spec` | PyInstaller configuration for the Windows build. / Windows 版 PyInstaller 打包配置。 |
| `AGENTS.md` | Working rules for contributors and coding agents. / 贡献者及开发 Agent 的工作规则。 |
| `LICENSE` | GPL-3.0-only license text. / GPL-3.0-only 许可证正文。 |
| `.gitignore` | Files excluded from Git, including local environments and build outputs. / Git 忽略规则，包括本地环境和构建产物。 |

The `.conda-env/`, `build/`, and `dist/` folders are generated locally; `dist/` contains portable build outputs. They are not application source. A portable copy stores its own settings and logs in `data/` beside `NodeBridge.exe`.

`.conda-env/`、`build/` 和 `dist/` 是本地生成的目录；其中 `dist/` 保存绿色版构建产物，均非应用源码。绿色版的设置和日志保存在 `NodeBridge.exe` 旁的 `data/`。

## Documentation / 文档

Start with the [user guide](docs/USER_GUIDE.md) or the [developer guide](docs/DEVELOPER_GUIDE.md). See the [change log](docs/CHANGELOG.md) for update history; the developer guide links to detailed project notes.

使用者请看[中文使用指南](docs/USER_GUIDE.md)，作者和 Agent 请看[开发者指南](docs/DEVELOPER_GUIDE.md)。更新历史见[更新记录](docs/CHANGELOG.md)；详细项目资料由开发者指南汇总。

## License / 许可证

Copyright (C) 2026 Charles Hua. Licensed under [GPL-3.0-only](LICENSE). Distributed modifications must provide their corresponding source code under the same license; third-party dependencies retain their own licenses.

版权所有 (C) 2026 Charles Hua。项目采用 [GPL-3.0-only](LICENSE) 许可证。分发修改版时须按同一许可证提供对应源代码；第三方依赖仍遵循各自许可证。
