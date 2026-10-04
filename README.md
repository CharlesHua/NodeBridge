# NodeBridge

NodeBridge is a Windows-first desktop GUI for browsing and managing files on SSH-accessible nodes. It uses Python 3.12, PySide6, and Paramiko. Other desktop platforms may be supported later.

NodeBridge 是面向 Windows 的 SSH 多节点文件管理桌面应用，采用 Python 3.12、PySide6 和 Paramiko；未来可能支持其他桌面平台。

## Current features / 当前功能

- Browse local files, a jump site, and multiple work nodes; inspect one work node or a merged directory view.
- Copy files by dragging between panes and distribute copies to connected work nodes in parallel; confirm remote deletion before it runs.
- Open multiple SSH terminals per node, organize connection batches into editable Terminal Groups, broadcast within the active group, and view group output together in node order.

- 浏览本地、跳板及多个工作节点的文件，可查看单个节点或合并目录。
- 通过拖放复制文件，并可向已连接的多个工作节点并行分发；远程删除前须确认。
- 每个节点可打开多个 SSH 终端；连接批次形成可重命名的终端组，广播仅限当前组，并可按节点顺序联合查看输出。

The project is under active development. Multi-node operations and terminal broadcast still need live verification on the owner's cluster; see [current status](CURRENT_STATUS.md) for the exact implementation boundary.

项目仍在开发中。多节点操作和终端广播尚需在项目所有者的集群上实测；准确的实现范围见[当前状态](CURRENT_STATUS.md)。

## Run from source / 从源码运行

Create the Python 3.12 environment from `environment.yml`, then run on Windows PowerShell:

按 `environment.yml` 创建 Python 3.12 环境，然后在 Windows PowerShell 中运行：

```powershell
conda env create --prefix .\.conda-env --file environment.yml
.\.conda-env\python.exe -m nodebridge
```

The planned Windows release must be self-contained and run without Python preinstalled on the target machine. Packaging has not been implemented yet.

计划中的 Windows 发行包必须自带运行所需组件，目标机器无需预装 Python；打包功能尚未实现。

## Documentation / 文档

[User guide / 使用指南](USER_GUIDE.md) · [Roadmap and TODO / 路线图与待办](TODO.md) · [Current status / 当前状态](CURRENT_STATUS.md) · [Architecture / 架构](ARCHITECTURE.md) · [Decisions / 设计决定](DECISIONS.md) · [Handoff / 接手说明](HANDOFF.md) · [Agent rules / Agent 工作规则](AGENTS.md)

## License / 许可证

Copyright (C) 2026 Charles Hua. Licensed under [GPL-3.0-only](LICENSE). Distributed modifications must provide their corresponding source code under the same license; third-party dependencies retain their own licenses.

版权所有 (C) 2026 Charles Hua。项目采用 [GPL-3.0-only](LICENSE) 许可证。分发修改版时须按同一许可证提供对应源代码；第三方依赖仍遵循各自许可证。
