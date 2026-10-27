# Change log / 更新记录

This file records user-visible changes. `Unreleased` describes work in the current working tree and is not a published version. Add a dated version heading when a build is actually released; build-folder numbers are not release versions. See [CURRENT_STATUS.md](CURRENT_STATUS.md) for the exact implementation and verification boundary.

本文记录用户可见的更新。“未发布”指当前工作区中的改动，不代表已经发行。实际发布时再添加带日期的版本标题；构建目录编号不是发行版本号。具体实现及验证边界见 [CURRENT_STATUS.md](CURRENT_STATUS.md)。

## Unreleased / 未发布

- Added exit confirmation for active site/terminal connections, with a minimize option that keeps connections alive. Startup can reconnect the last active sites and terminal groups after normal or forced exit; explicitly detached tmux sessions are excluded. Recovery snapshots contain no passwords or commands.

- 新增活动站点／终端的退出确认，可选择最小化并保持连接。正常或强制退出后，启动时可询问重连上次活动站点和终端组；排除已主动分离的 tmux 会话。恢复快照不保存密码或命令。

- Added Simplified Chinese/English interface selection with a saved preference that applies immediately, preserving live connections and terminal views. File logs and the built-in Chinese guide remain unchanged; commands, paths and server output are not translated. Portable builds now validate and compile Qt translation resources.

- 新增简体中文／英文界面选择，保存设置后立即生效。文件日志和内置中文指南保持不变，命令、路径和服务器输出不翻译。绿色版构建会检查并编译 Qt 翻译资源。

- Fixed the separate Windows whole-window disappearance on first terminal use: prepare the Qt graphics surface before showing the main window, using one persistent hidden, unloaded Qt Quick widget. This prevents the later Raster-to-Direct3D native-window recreation while keeping terminal pages lazy-loaded and the normal graphics backend unchanged.

- 修复 Windows 首次使用终端时整个主窗口短暂消失的问题：显示主窗口前，通过一个常驻、隐藏且不加载内容的 Qt Quick 控件准备图形表面，避免首次插入终端时从普通绘制切换到 Direct3D 而重建原生窗口。终端页面仍按需加载，沿用原图形后端。

- Node rows now refresh status, address, CPU, memory, and process users together. Each sample refreshes users and their timestamp; unavailable nodes clear stale metrics. Node/session rows are reused during connection changes to preserve selection, checks, and collapsed branches. Terminal startup uses a matching dark WebEngine background and batches buffered output to avoid white loading flashes and partial initial rendering.

- 节点行统一刷新状态、地址、CPU、内存和进程用户。用户及其更新时间随每轮采样更新，不可连接节点清除过期指标。连接变化时复用节点／会话行，保留选择、勾选和折叠状态。终端启动统一 WebEngine 的深色背景，并合并首次缓存输出，避免加载白闪及初始输出分段绘制。

- Permanently ending a terminal group now removes all successfully terminated tabs and the empty group even if tmux closes their SSH channels before the termination results arrive. Hidden tabs and the combined view are cleaned up too; failed sessions and unrelated groups remain available.

- 永久结束终端组时，即使 tmux 提前关闭 SSH 通道，也会一次清理所有成功结束的终端标签及空组，包括隐藏标签和联合视图；失败会话及其他组仍保留。

- File operations now reserve only their local, direct-node, or work-node panes. Delete, rename, directory creation, and independent copies run in the background without locking the other panes; directory reads have separate jobs. Remote mutations use isolated SFTP channels, completion preserves later navigation, and apply-all conflict choices stay within one copy task. Background file errors use nonmodal notifications.

- 文件操作改为只占用涉及的本地、直连或间接节点侧。删除、重命名、新建目录及互不涉及的复制在后台执行，不再锁住另外两侧；目录读取独立运行。远程修改使用独立 SFTP 通道，完成后保留用户后来切换的目录，“全部覆盖／跳过”选择仅作用于对应复制任务。后台文件错误提示不再锁住主窗口。

- Remember main-window size and maximized state, panel splitter proportions, and file/terminal/task table column widths. Normal and maximized windows keep separate layouts, restored when switching modes and on the next launch.

- 记住主窗口尺寸及最大化状态、面板分隔比例，以及文件／终端／任务列表的列宽。普通窗口与最大化窗口分别保存布局，切换状态及下次启动时自动恢复。

- Successful permanent termination now automatically removes the corresponding local task records from both workspaces. Successful scans also remove confirmed-missing sessions; failed queries, failed terminations and detached sessions retain their records. Cleanup is saved immediately, has no separate button, and cannot be undone by older scan results.

- 终端页或任务页成功永久结束会话后自动清理对应本地记录；扫描成功确认远端不存在时也自动清理。查询失败、结束失败或仅分离时保留记录。清理立即保存，无需单独按钮，旧扫描结果不会将已清理记录重新加回。

- Isolated tmux inventory scans from terminal mutations and foreground jobs. Scans no longer disable restore, termination, file actions, or site disconnect; failures stay in the task summary/logs. Stale results cannot overwrite later local state changes. The Tasks table supports Ctrl/Shift multi-selection, batch restore/detach, and confirmed batch termination, preserving selections during refresh.

- tmux 清单扫描独立于终端修改及前台任务，不再因扫描禁用恢复、结束会话、文件操作或站点断开；失败显示在任务摘要和日志中。旧结果不会覆盖后续本地状态变化。任务表支持 Ctrl／Shift 多选、批量恢复／分离和经确认的批量结束，刷新时保留选中项。

- Terminal disconnect actions now offer permanent tmux termination or detaching connections, with cancellation as the default. Group/checked-node actions apply one choice to all members; failed terminations retain their connections and report errors. Task-page detach and application exit still preserve remote sessions.

- 终端断开操作现可选择永久结束 tmux 会话或分离连接，默认取消。组／勾选节点统一选择，结束失败时保留对应连接并报告原因；任务页分离及退出程序仍保留远程会话。

- Added optional tmux persistent terminals (remote tmux 3.1+), an independent Tasks tab for scanning/restoring/detaching/confirmed termination, and local session metadata. tmux control mode preserves the existing xterm.js input, color, broadcast, and combined-output pipeline. Ordinary terminals remain available; missing tmux is reported without installing or falling back silently.

- 新增可选 tmux 持久终端（远程须 tmux 3.1+）及独立“任务”页，可扫描、恢复、分离并经确认结束会话，保存本地会话元数据。控制模式保留既有 xterm.js 输入、颜色、广播及联合输出通路。普通终端仍可使用，缺少 tmux 时明确报错，不安装或静默降级。

- Moved the confirmed interrupt button to the broadcast title row, leaving the command-entry row for typing and sending.

- 中断按钮移到“广播命令”标题所在行，输入行保留命令输入和发送。

- Added a confirmed “Interrupt (Ctrl+C)” broadcast button. It sends one ETX byte per selected node in the active group, without a newline or directory probe, rechecks targets after confirmation, and records submission and failures in the activity log.

- 新增需二次确认的“中断（Ctrl+C）”广播按钮：仅向当前组勾选的节点各发送一次中断字符，不附加回车、不探测目录；确认后重新检查目标，日志记录提交和失败情况。

- Clarified the site Host field as “IP or domain name”. The terminal node list prefers the server hostname returned during discovery and retains the connection address in its tooltip; terminal/group labels use that name too.

- 站点“主机”字段明确为“IP 或域名”；终端节点列表优先显示发现时取得的服务器主机名，悬停仍可查看连接地址，相关终端／组名称同步使用该名称。

- Keep memory text in the normal foreground color when selected and keep the separator close to the aligned capacity values.

- 内存栏选中时保持正常文字颜色，缩小分隔点与对齐容量之间的多余空隙。

- Right-align CPU and memory readings, align memory percentages and the amount's right edge with separate drawing regions using the normal UI font, and separate user names with a comma and space.

- CPU 和内存数值右对齐，内存保留界面原字体，通过分区绘制对齐百分号及容量右侧边缘；多个用户名改用英文逗号加空格分隔。

- Added a Users column beside terminal-node CPU/memory readings. The existing background sample also queries ordinary process owners, including users with detached jobs; hovering shows the full list. User-query failure leaves CPU/memory available.

- 终端节点 CPU／内存旁新增“用户”列，随现有后台采样查询有进程的普通用户，包含运行脱离登录会话任务的用户；悬停显示完整名单。用户查询失败不影响 CPU／内存显示。

- Site Manager now saves independent hosts-file and SSH-config discovery switches and remote paths per site. Existing sites keep the previous defaults; saved-site connections, including startup connections, use these settings.

- 站点管理器可按站点保存 hosts 文件、SSH 配置两种发现来源的开关和远程路径。旧站点沿用原默认值；从站点管理器连接及启动自动连接均使用保存的设置。

- Vertically center the common-command label with the buttons in its row.

- “常用命令”文字与同行按钮垂直居中对齐。

- Common-command buttons now wrap into multiple rows when the terminal pane narrows, including when dragging the node/terminal splitter; widening it reduces the row count again.

- 常用命令按钮随终端面板宽度自动换行；向右拖动节点／终端分隔线时会显示多行，拉宽后恢复较少行。

- CPU/memory monitoring now starts only after the first visit to the Terminal tab, reducing work at initial connection. Subsequent tab switches keep background monitoring active without starting duplicate timers.

- CPU／内存监控延后到首次进入“终端”页时启动，减少初次连接的后台工作；之后切换标签仍保持监控，不重复启动定时器。

- Mitigated a Windows startup crash in Python's native traceback dumper: automatic native fault tracing is disabled on Windows by default. Python exceptions, background-thread exceptions and Qt warnings/errors remain logged. Native failures can be inspected in the Windows Application event log.

- 针对 Windows 启动时 Python 原生栈输出器自身崩溃，默认关闭 Windows 自动原生异常栈捕获。Python 异常、后台线程异常及 Qt 警告／错误仍记录；原生故障可查看 Windows 应用程序事件日志。

- The terminal node list now shows whole-node CPU and memory usage for the direct site and reachable indirect nodes. A separate SSH monitor samples Linux `/proc` data with at most two concurrent queries, waits five seconds between rounds, and backs off failed nodes for 30 seconds. Updates preserve scrolling; monitoring stops on disconnect or exit.

- 终端节点列表新增整机 CPU 和内存用量，覆盖直连站点及可连接间接节点。独立 SSH 监控读取 Linux `/proc`，最多并发查询两个节点，每轮结束后等待 5 秒，失败节点延后 30 秒重试。更新保留滚动位置，断开或退出时停止监控。

- Site Manager can select one direct site to connect automatically after the main window opens. Encrypted passwords require master-password unlock; cancelling leaves the app disconnected. Existing SSH connection and host-key checks remain in use.

- 站点管理器可选择一个直连站点，在主窗口打开后自动连接。加密密码须先输入主密码解锁；取消后保持未连接。连接继续沿用现有 SSH 和主机密钥校验流程。

- Copies from local/direct panes to indirect nodes now ask before creating missing destination directories, including parent directories. Users can skip that node or apply their choice to subsequent missing destinations in the same batch; existing directories proceed normally.

- 本地／直连节点向间接节点复制时，目标路径缺失会先询问是否创建所需各级目录。可跳过该节点，或将选择应用于本批次后续缺失路径；已有目录的节点照常复制。

- Node reachability updates now edit existing rows in place, preserving scroll position, selection, checkboxes and collapsed terminal branches while probing continues.

- 节点可连接性状态改为原位更新，检测期间保留滚动位置、当前选择、勾选及终端分支折叠状态，避免列表跳回上方。

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
