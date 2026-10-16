# Change log / 更新记录

This file records user-visible changes. `Unreleased` describes work in the current working tree and is not a published version. Add a dated version heading when a build is actually released; build-folder numbers are not release versions. See [CURRENT_STATUS.md](CURRENT_STATUS.md) for the exact implementation and verification boundary.

本文记录用户可见的更新。“未发布”指当前工作区中的改动，不代表已经发行。实际发布时再添加带日期的版本标题；构建目录编号不是发行版本号。具体实现及验证边界见 [CURRENT_STATUS.md](CURRENT_STATUS.md)。

## Unreleased / 未发布

- Closing a Combined terminal tab now hides it without disconnecting group sessions or deleting its history. The tab dropdown can reopen hidden Combined views.

- 联合终端标签的关闭按钮现可隐藏该视图，不会断开组内连接或删除历史；标签下拉菜单可重新打开已隐藏的联合视图。

- Common-command buttons preserve the previous input target and restore keyboard focus there. Broadcast drafts place the caret at the end; empty terminal prompts no longer receive unnecessary clear-line keys that can ring the Shell bell.

- 常用命令按钮保留此前的输入目标，并将键盘焦点恢复到对应终端或广播框。广播草稿光标位于命令末尾；空终端提示符不再发送多余的清行按键，避免触发 Shell 提示音。

- Reachability probing now updates each node in Files and Terminal as soon as its result arrives, without waiting for slower nodes. Cancelled or stale results cannot replace newer connection status.

- 可连接性检测每获得一个节点结果，就立即更新文件页和终端页，无须等待较慢节点。取消或过期结果不会覆盖新的连接状态。

### Portable build verification — 2026-10-13 / 绿色版构建验证 — 2026-10-13

Build `20261013-195551-a82b98` produced `dist/NodeBridge-portable.zip` (SHA-256: `0cec69d9204ee7eabaf3760fc17aeaa208eefed1515c151e17f95a077ec610cd`). Six focused checks passed. Archive integrity and required assets were checked; the ZIP excludes runtime `data` and site profiles. The executable started on the build machine with empty error/crash logs. This is an unpublished build of the working tree; clean-machine and live-cluster acceptance remain pending.

构建 `20261013-195551-a82b98` 生成了 `dist/NodeBridge-portable.zip`（SHA-256：`0cec69d9204ee7eabaf3760fc17aeaa208eefed1515c151e17f95a077ec610cd`）。6 项定向检查通过，已核对压缩包完整性及必要资源，ZIP 排除了运行时 `data` 和站点资料。可执行文件在构建机器启动成功，错误及崩溃日志为空。这是尚未发布的工作区构建；干净机器及真实集群验收仍待完成。

### Changes / 改动

- Added workspace settings and a local stylesheet for a consistently light VS Code built-in Markdown preview.

- 补充工作区设置和本地样式，使 VS Code 内置 Markdown 预览固定显示为浅色。

- Switching nodes in single-node view first reads the same absolute directory, then tries successive parents only when the directory is missing, stopping at root. Operation-result rows now match the compact file-list height. Expanded the architecture reference with SSH layers, transfer isolation, xterm.js integration, and Qt task/layout principles.

- 单节点查看时切换节点，先读取同一绝对目录，仅在目录不存在时逐级尝试父目录，直到根目录。操作结果表使用与文件列表相同的紧凑行高。架构文档补充 SSH 分层、传输隔离、xterm.js 集成和 Qt 任务／布局原理。

- Indirect-node connections now use their own background task slot, keeping local and direct-node browsing and operations available. Probe failures appear gray in both Files and Terminal; a later successful connection restores the node, and stale probe replies cannot undo that success.

- 间接节点连接使用独立后台任务位，不再阻止本地和直连节点浏览及操作。探测失败在文件页与终端页均显示为灰色；后续实际连接成功后恢复，过期探测结果不会覆盖新的连接成功状态。

- Broadcast input supports multiple lines: Shift+Enter inserts a newline and Enter sends the entire draft. Tools → Command Manager edits built-in and custom command snippets, optional descriptions, and persistent toolbar visibility.

- 广播输入支持多行：Shift+Enter 换行，Enter 发送整段内容。“工具 → 常用命令管理”可编辑预置和自定义命令、可选功能介绍及持久保存的工具栏显示勾选。

- Automatic SHA-256 comparison reuses successful results for up to 60 seconds when the node connections, absolute paths, sizes, modification times, and file types match fresh listings. File operations invalidate this memory-only cache; cached results have an explanatory tooltip. Metadata equality is a heuristic, not proof that bytes are unchanged.

- 自动 SHA-256 比较在节点连接、绝对路径、大小、修改时间及文件类型与新目录列表一致时，最多复用 60 秒内的成功结果。文件操作会清除这一内存缓存，缓存结果带有说明提示。元数据相同是变化判断线索，不能证明字节内容未变。

- Fixed terminal Ctrl+V and Ctrl+Shift+V pasting clipboard text twice by cancelling the browser's default paste when the custom clipboard handler runs. Bracketed paste remains supported.

- 修复终端 Ctrl+V 和 Ctrl+Shift+V 将剪贴板内容粘贴两遍的问题：自定义粘贴处理时取消浏览器的默认粘贴，仍支持括号粘贴模式。

- Indirect-node browsing now uses at most four sessions per jump SSH connection. Parallel copies use a separate pool of authenticated jump connections, opening SFTP channels only for active node tasks and reusing idle jump connections. This keeps queued nodes from consuming transfer channels and lets browsing continue during a copy.

- 间接节点浏览现每条跳板 SSH 连接最多承载四个会话。并行复制使用独立的已认证跳板连接池，仅为正在运行的节点任务打开 SFTP 通道，并复用空闲跳板连接。排队节点不占传输通道，复制期间仍可继续浏览。

- The local file list now matches the remote lists' centered, regular-weight headers, Chinese file types, and hyphenated modification dates.

- 本地文件列表现与远程列表一致：表头居中且不加粗，文件类型使用中文，修改日期使用短横线分隔。

- The View menu can now hide or show the indirect-node pane independently; it starts visible and remembers the choice across launches.

- “查看”菜单现可独立隐藏或显示间接节点面板；默认显示，并在下次启动时恢复选择。

- Local file sizes now use the same byte counts with thousands separators as direct and indirect remote files; size sorting still uses the filesystem model's numeric values.

- 本地文件大小现与直连及间接节点一致，按字节数并使用千位分隔符显示；大小排序仍使用文件系统模型的数值。

### Windows portable preview — 2026-10-12 / Windows 绿色预览构建 — 2026-10-12

Build `20261012-214316-204b85` regenerated `dist/NodeBridge-portable.zip` (SHA-256: `47abf90a0c2df5eb6cee1404bbcbeb7b486173601e2d303b55c68d296e88da7e`). The archive integrity check passed and contains no `data` or site profiles. Its build log is valid UTF-8 and preserves the Chinese project path without replacement characters. The packaging tests passed; this rebuild did not repeat the application startup or live-cluster checks.

构建编号 `20261012-214316-204b85` 重新生成了 `dist/NodeBridge-portable.zip`（SHA-256：`47abf90a0c2df5eb6cee1404bbcbeb7b486173601e2d303b55c68d296e88da7e`）。压缩包完整性检查通过，不含 `data` 或站点资料。构建日志是有效的 UTF-8，中文项目路径没有被替换。打包测试通过；本次重建没有重复执行程序启动或真实集群测试。

- The portable build script now captures PyInstaller output directly into a UTF-8 log, avoiding PowerShell redirection that corrupted Chinese paths in earlier build logs.

- 绿色版构建脚本现将 PyInstaller 输出直接保存为 UTF-8 日志，避免 PowerShell 重定向导致早期构建日志中的中文路径乱码。

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
