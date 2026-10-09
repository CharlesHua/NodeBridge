# Change log / 更新记录

This file records user-visible changes. `Unreleased` describes work in the current working tree and is not a published version. Add a dated version heading when a build is actually released; build-folder numbers are not release versions. See [CURRENT_STATUS.md](CURRENT_STATUS.md) for the exact implementation and verification boundary.

本文记录用户可见的更新。“未发布”指当前工作区中的改动，不代表已经发行。实际发布时再添加带日期的版本标题；构建目录编号不是发行版本号。具体实现及验证边界见 [CURRENT_STATUS.md](CURRENT_STATUS.md)。

## Unreleased / 未发布

### Windows portable preview — 2026-10-09 / Windows 绿色预览构建 — 2026-10-09

Build `20261009-204911-ac842e` produced a self-contained one-folder package and `dist/NodeBridge-portable.zip` (SHA-256: `707baf6b72eec66e8009308807372efe3f6ecc3b25fc8a20b7f62e88e9120847`). The archive integrity check passed; it contains Python 3.12, Qt WebEngine, the Chinese user guide and GPL license, and excludes `data` and site profiles. Thirty-nine focused tests passed, and the executable remained running in a local startup smoke check with empty diagnostic logs. This is a preview build, not a tagged release; a clean Windows machine and live cluster still need verification.

构建编号 `20261009-204911-ac842e` 生成了自包含的单目录程序和 `dist/NodeBridge-portable.zip`（SHA-256：`707baf6b72eec66e8009308807372efe3f6ecc3b25fc8a20b7f62e88e9120847`）。压缩包完整性检查通过，包含 Python 3.12、Qt WebEngine、中文使用指南和 GPL 许可证，不包含 `data` 或站点资料。39 项定向测试通过；本机启动冒烟检查中程序持续运行，诊断日志为空。这是预览构建，并非带标签的正式发行版；仍需在干净 Windows 机器和真实集群验证。

- File copies now run in a separate background task lane, so local, direct-node and indirect-node browsing stays available during a transfer. Remote transfers use separate SFTP channels; connection shutdown waits for the copy to finish.

- 文件复制现在使用独立的后台任务通道，传输期间仍可浏览本地、直连及间接节点。远程传输使用独立 SFTP 通道；关闭连接需等待复制完成。

- Fixed local Delete failing with a `TypeError` because PySide6 returns a boolean from `QFile.moveToTrash`.

- 修复本地 Delete 操作因误把 PySide6 `QFile.moveToTrash` 的布尔返回值当作元组而报 `TypeError` 的问题。

- The merged remote file view now checks same-name, same-size regular files automatically with remote SHA-256. Hash commands batch up to 32 files and use at most two concurrent command channels. The manual selected-file check and its JSON report have been removed; live-cluster performance still needs verification.

- 合并远程文件视图现自动使用远程 SHA-256 核对同名、同大小的普通文件。每条哈希命令最多处理 32 个文件，最多同时运行两个命令通道。手动选中文件核对及 JSON 报告已移除；仍需在真实集群验证性能。

- A reachable but disconnected node now has a hollow-circle status marker; connected nodes retain their solid-circle marker.

- 可连接但尚未连接的节点现使用空心圆标记；已连接节点仍使用实心圆。

- Current development work also adds background node reachability checks, refreshed directory listings, a Chinese in-app user guide, persistent activity and diagnostic logs, and an experimental self-contained Windows folder build. These features still have the acceptance limits described in the current-status document.

- 当前开发中的改动还包括后台节点可连接性探测、目录列表刷新、程序内中文使用指南、持久操作及诊断日志，以及试验性自包含 Windows 单目录构建。这些功能的验收限制仍以当前状态文档为准。

## Development checkpoints / 开发提交记录

These Git commits are historical checkpoints, not tagged releases. Their commit messages summarize the work at each point; later uncommitted work is listed above.

以下 Git 提交是开发节点，并非带标签的发行版本。提交信息概括了各阶段工作；之后尚未提交的改动列在上方。

- 2026-10-07 — `1bc417a`: fixes and debugging following the multi-node workspace changes. / 多节点工作区后续修复与调试。
- 2026-10-06 — `c941793`: multi-node drag and drop, site password management, and file UI improvements. / 多节点拖放、站点密码管理与文件界面改进。
- 2026-10-04 — `20579fb`: multi-node workspace and grouped SSH terminals. / 多节点工作区与分组 SSH 终端。
- 2026-09-30 — `359c548`: GPL license and project documentation. / GPL 许可证与项目文档。
- 2026-09-30 — `631a697`: initial NodeBridge project. / NodeBridge 项目初始提交。
