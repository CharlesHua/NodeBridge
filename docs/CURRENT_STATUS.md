# Current status / 当前状态

This is the implementation snapshot audited on 2026-10-09. [USER_GUIDE.md](USER_GUIDE.md) explains usage, [ARCHITECTURE.md](ARCHITECTURE.md) explains the code, [DECISIONS.md](DECISIONS.md) records settled choices, and [TODO.md](TODO.md) tracks future work.

这里是截至 2026-10-09 核对过的实现快照。[USER_GUIDE.md](USER_GUIDE.md) 说明使用方法，[ARCHITECTURE.md](ARCHITECTURE.md) 说明代码结构，[DECISIONS.md](DECISIONS.md) 记录已确定的选择，[TODO.md](TODO.md) 跟踪后续工作。

## Implemented / 已实现

File copies between the local pane, direct node, and indirect nodes now use a separate background copy job and independent remote SFTP channels. Browsing remains available while one copy is active; disconnect and other file mutations wait. Batch copies retain their per-node result table and conflict prompts. A second copy waits until the first completes; persistent queueing and retry are not implemented. Dragging a remote file to Windows Explorer still has to prepare temporary file data before the drag begins, although that preparation uses the copy job and does not disable browsing controls.

本地、直连与间接节点之间的复制现使用独立后台复制任务和远程独立 SFTP 通道。一项复制进行期间仍可浏览目录；断开连接及其他文件修改须等待。批量复制仍保留逐节点结果表和同名冲突提示。第二项复制需等前一项结束；尚无持久队列或重试。远程文件拖到 Windows 资源管理器时，仍需在开始拖动前准备临时文件数据，但准备过程使用复制任务，不禁用浏览控件。

The Files view calls the primary jump site the Direct node and nodes reached through it Indirect nodes. Local, direct, and indirect file-list headers use Chinese labels. The last successfully opened local directory is restored at startup, falling back to the home directory if unavailable.

文件页将主跳板站点称为“直连节点”，经它到达的节点称为“间接节点”。本地、直连及间接节点的文件列表均使用中文表头。最近一次成功打开的本地目录在启动时恢复；目录不可用则回到用户主目录。

Discovered indirect-node names are candidates. An independent background SSH connection probes up to four candidates at once with strict host-key verification; reachable candidates use a hollow circle, while failures appear gray but remain manually connectable. Directory navigation and view switches request fresh listings. After a merged-directory refresh, a separate background job automatically computes SHA-256 for regular files that exist with the same name and size on every connected node. It uses isolated SFTP sessions, at most two concurrent hash command channels, and batches up to 32 file names per command. Fresh directory metadata is checked after hashing. Only digests and errors return locally. Known missing or different-size files need no hash. The difference column shows queued, running, equal, different, or failure states; failed nodes have detail tooltips. Changing the path or node set cancels stale checks. The manual selected-file button and JSON check reports have been removed. These read-only checks do not recursively audit directories or verify transfer results. Live cluster performance and large-file policy still need validation.

发现的间接节点名称仅是候选。独立后台 SSH 连接最多并行探测四个候选节点，严格校验主机密钥；可连接的候选节点使用空心圆，探测失败的节点显示为灰色，仍可手动重试连接。进入目录和切换视图会重新读取列表。合并目录刷新后，另一后台任务自动对所有已连接节点上同名、同大小的普通文件计算 SHA-256。它使用独立 SFTP 会话，最多同时执行两个哈希命令，每条命令最多处理 32 个文件，并在完成后重新读取目录元数据；本机只接收摘要和错误。已知缺失或大小不同的文件无需再算哈希。差异栏显示等待、校验中、相同、不同或失败，失败节点可查看提示详情。切换路径或节点集合会取消过期核对。手动核对按钮及 JSON 核对报告已移除。这些只读核对不递归检查目录，也不验收传输结果；真实集群性能和大文件策略仍待验证。

Windows-first PySide6 GUI with an optional local pane, a primary jump-site pane, and a work-node pane. The work-node pane discovers SSH aliases and numbered names from the jump site's `/etc/hosts`, holds separate SFTP sessions to multiple aliases, and shows either one node or merged directory entries. Dragging from local or jump to the work-node file list copies to the current node or all connected work nodes in parallel after a scope choice, regardless of checkbox state. Dragging selected work-node items to jump or local collects copies from the current or all connected nodes, adding a node-name suffix only to each top-level name. The work-node path row and file-list menu now create directories on the current or all connected nodes: a precheck detects existing names, then one prompt offers skip or per-node numbered names. Precheck failure stops all creation; postcheck node failures are reported individually. Delete or the context menu acts on selected work-node names with the same scope choice and permanent-delete confirmation; results appear per node. The separate batch-operation button has been removed. The underlying single-destination move remains in code but currently has no UI entry point. Existing direct, saved-site tunnel, and sequential SSH-alias connections remain; copy, rename, create-directory, and delete actions still apply to the primary remote pane. Local-pane visibility is persistent.

面向 Windows 的 PySide6 主窗口包含可选本地面板、主跳板站点面板和工作节点面板。工作节点面板从跳板 SSH 配置及 `/etc/hosts` 的编号名称发现节点，为多个别名保持独立 SFTP 会话，并支持单节点或合并目录查看。从本地或跳板拖到工作节点文件列表后，可选择复制到当前节点，或并行复制到所有已连接节点，不受复选框状态影响。从工作节点拖到跳板或本地时，可选择从当前节点或所有已连接节点汇集副本，仅给每个顶层项目名称追加节点名后缀。间接节点路径栏和文件列表菜单现可在当前节点或所有已连接节点上新建目录：先检查同名项目，冲突时统一询问跳过或按节点创建带序号的新目录。预检查失败则所有节点均不创建，创建阶段的节点失败逐项报告。工作节点的 Delete 键和右键菜单会对选中名称询问同样的操作范围，再确认永久删除；结果逐节点显示。独立的批量操作按钮已移除。底层单目标移动仍保留代码，但当前没有界面入口。原有直连、已保存站点隧道和逐级 SSH 别名中转仍保留；复制、重命名、新建目录及删除仍作用于主远程面板。本地面板显示设置可持久保存。

Work-node dragging starts without a prompt or transfer. Once the item is dropped onto the local or jump pane, NodeBridge asks for the source-node scope and copies directly over SFTP with node-name suffixes. Work-node-to-Explorer/Desktop dragging is not currently supported; its earlier temporary-copy implementation interrupted the drag and was removed. Jump-site-to-Explorer dragging still uses its existing temporary-copy path.

从工作节点开始拖动时，不弹窗也不开始传输。投放到本地或跳板面板后，NodeBridge 才询问来源节点范围，并按节点名后缀直接通过 SFTP 复制。工作节点拖到资源管理器／桌面的功能目前不支持；先前临时副本方案会中断拖动，现已移除。跳板站点拖到资源管理器仍使用原有的临时副本流程。

The Files and Terminal tabs provide switchable main views. The Terminal view shows jump sites and discovered work-node aliases even without Files-tab worker SFTP connections. It can open multiple independent SSH PTY connections per node and connect or disconnect terminals for several checked nodes at once. Each successful connection batch creates a distinct Terminal Group; a single new terminal creates its own group. The group selector and checked member list define broadcast recipients, at most one terminal per node. No broadcast-mode checkbox is required; switching groups clears the draft. Before sending, NodeBridge checks idle shells' physical directories; a mismatch or unknown directory requires confirmation, and common destructive commands require another confirmation. The send path revalidates group membership before delivery, so another group's terminal cannot be included. Each terminal accepts direct keystrokes and retains its own ANSI-colored output. Files-tab worker disconnection leaves terminal channels open; jump disconnection closes them. Closing an individual terminal tab hides its view; Disconnect terminal closes its channel. The status reports delivery, not command completion.

“文件”和“终端”标签页可切换主视图。终端页显示跳板站点和已发现的工作节点别名，不依赖文件页工作节点 SFTP 连接。每节点可打开多个独立 SSH PTY，也可批量连接或断开。每批成功连接都会创建独立 Terminal Group；单独新建终端会形成自己的组。组选择器和成员勾选列表决定广播目标，每节点最多一个终端。无需勾选广播模式；切换组会清空草稿。发送前程序检查空闲 Shell 的实际目录；目录不同或未知时需要确认，常见破坏性命令还需额外确认。发送路径会重新验证组成员关系，其他组终端不能混入。各终端可直接输入并保留独立的 ANSI 彩色输出。文件页断开工作节点不会关闭其终端；断开跳板连接才会关闭。关闭单个终端标签只隐藏其视图，“断开终端”会关闭通道。状态栏报告发送情况，不代表命令执行完成。

Groups with multiple connected terminals show their Combined tab by default; a single-terminal group shows its individual terminal instead. The former “Combined view” button is replaced by “Disconnect current group”, which asks for confirmation and closes only that group's terminals. Groups and terminals have no fixed application count limit. Work-node terminals are spread across terminal-only jump SSH connections after a conservative per-connection budget; a code-2 session-open refusal triggers a bounded retry on a fresh connection. This still needs verification against the owner's cluster.

连接了多个终端的组默认显示联合视图；只有一个终端的组直接显示该终端。“联合显示”按钮已替换为“断开当前组”，确认后仅关闭该组终端。程序没有固定的组数或终端数上限。工作节点终端超过保守的单连接额度后分配到专供终端的额外跳板连接；遇到代码 2 的会话打开拒绝时，会换新连接有限次重试。这仍需在项目所有者的集群上实测。

A drop-down button at the right end of the Terminal tab bar lists every currently open terminal and Combined tab with its full title and marks the active tab. The list is rebuilt when opened, so renamed groups and hidden tabs are reflected immediately.

终端标签栏右端现有下拉按钮，以完整名称列出全部已打开的单终端和联合视图标签，并标记当前标签。每次打开菜单时重新生成列表，因此组重命名及隐藏标签会立即反映出来。

The group name can be edited; its generated node suffix compresses contiguous numbered names. A terminal tab shows “group · node”, adding its terminal number in parentheses only when that node has multiple sessions; the broadcast panel aligns below the terminal area. For each broadcast, the read-only Combined view directly concatenates the terminals' original prompt, command echo, and output in node order, without extra headings; ANSI-derived text colors are retained. A recognizable returning Shell prompt ends that node's reply; otherwise the next broadcast bounds it. Each node's reply shows at most 20,000 characters, with the latest 100 broadcasts retained in this view; individual terminals keep their normal scrollback. The internal `pwd -P` probe echo and returning prompt are suppressed from the visible terminal in the recognized-prompt path. Identical-output deduplication and reliable exit-status detection are still planned.

组名可编辑；自动生成的节点后缀会缩写连续编号。单终端标签显示“组名 · 节点”，仅当同一节点有多个会话时才在括号内附加终端编号；广播区在终端区下方对齐。对于每次广播，只读“联合显示”按节点顺序直接拼接各终端原有的提示符、命令回显及输出，不额外添加标题，并保留 ANSI 字色。识别到 Shell 提示符返回时结束该节点反馈，否则以下一次广播为边界。每节点每次命令最多显示 20,000 个字符，联合视图保留最近 100 次广播；各独立终端保留原有滚动历史。对于可识别的提示符，检查目录所用的 `pwd -P` 探针回显及其返回提示符会从可见终端中隐藏。相同输出去重和可靠的退出状态检测仍待实现。

The broadcast cwd check recognizes ordinary Bash prompts with one or more Conda environment prefixes such as `(condmat)`. It still asks for confirmation when directories differ or a prompt cannot be recognized. The Conda case has local simulated coverage but needs confirmation on the owner's cluster.

广播前的工作目录检查现可识别带一个或多个 Conda 环境前缀（如 `(condmat)`）的普通 Bash 提示符。目录确实不同或无法识别提示符时仍会要求确认。Conda 场景已有本地模拟测试，仍需在项目所有者的集群上确认。

When the selected terminals show different Conda prefixes, broadcast asks whether to cancel, send this time, or stop showing this prefix warning for the current Terminal Group. This choice is held only in memory until that group is removed; it does not suppress the absolute-directory or destructive-command checks. Prefix comparison reads the visible prompt, not the installed environment contents.

所选终端显示的 Conda 前缀不同时，广播会询问取消、仅此次发送，或对当前终端组不再提示此前缀差异。该选择仅保存在内存中，删除终端组后失效；绝对目录检查和破坏性命令确认仍会执行。比较依据是可见提示符，不会核对环境内安装的内容。

The terminal renderer is now local xterm.js in Qt WebEngine, with one frontend per session and a separate read-only combined frontend per group. xterm.js handles the blinking cursor, ANSI/VT colors and cursor movement, alternate screen, selection, scrollback, and keyboard input; WebChannel forwards input and output, and addon-fit resizes the remote PTY. The frontend is locally tested for ANSI colors, progress-line updates, Unicode, keyboard controls, and alternate-screen switching. Full-screen programs and IME behavior still need live user validation. The combined view preserves raw ANSI sequences and node order; it still uses recognizable Shell prompts to bound each broadcast reply.

终端渲染层现为 Qt WebEngine 中的本地 xterm.js：每个会话一个前端，每组联合视图另有只读前端。闪烁光标、ANSI/VT 颜色与光标移动、备用屏幕、选择、历史和键盘输入由 xterm.js 处理；WebChannel 转发输入输出，addon-fit 将尺寸同步至远程 PTY。本地已测试 ANSI 颜色、进度行覆盖、Unicode、控制键和备用屏幕切换。全屏程序及输入法行为仍需用户在真实服务器上验证。联合视图保留原始 ANSI 序列和节点顺序；每轮反馈边界仍依靠可识别的 Shell 提示符。

Direct jump-site terminals now use a fresh terminal-only SSH login and show its authentication banner before the Shell's output. This responds to the owner's observation that PowerShell shows a cluster notice and Ubuntu MOTD while a terminal opened on the SFTP transport showed only Last login. The new path has local simulated coverage; its actual welcome text still needs confirmation on cft01.

直连跳板站点的终端现使用新的终端专用 SSH 登录，并在 Shell 输出前显示该连接的认证公告。项目所有者观察到 PowerShell 显示集群公告和 Ubuntu MOTD，而复用 SFTP 连接的终端仅显示 Last login。新路径已有本地模拟测试；cft01 上的实际欢迎内容仍需确认。

NodeBridge has an application icon used by its source-run main window and Qt application. Editable SVG, PNG, and multi-size Windows ICO assets are included. The experimental executable embeds the ICO; its Explorer and taskbar appearance still need manual inspection.

NodeBridge 现有应用图标，源码运行时用于主窗口和 Qt 应用；仓库包含可编辑 SVG、PNG 和多尺寸 Windows ICO。试验性可执行文件已嵌入图标；仍需人工检查资源管理器和任务栏中的显示效果。

Tab in an individual terminal now reaches the remote Shell instead of moving Qt focus, enabling that Shell's normal command and path completion; remote BEL still sounds locally. The broadcast input regains focus after a successful send. Eight helper buttons appear in one toolbar row with command-and-purpose labels; narrow windows use the toolbar overflow menu. They replace the draft in the last focused command entry, either the broadcast field or a live terminal, without executing it. They provide `cd`, `mkdir`, `rm -r`, `cd ..`, `ls`, `ls -a`, `ls -lh`, and `pwd`. The Combined view adds a blank line when all recipients of a broadcast have finished replying.

单终端的 Tab 现可送达远程 Shell，而不再切换 Qt 焦点，因此能使用该 Shell 自带的命令和路径补全；远端 BEL 仍在本地响铃。广播成功发送后焦点返回广播输入框。八个助手按钮在同一行的工具栏上显示“命令（功能）”；窗口较窄时使用工具栏溢出菜单。按钮会替换最近获得输入焦点的广播框或活动终端中的草稿，不会直接执行；模板包括 `cd`、`mkdir`、`rm -r`、`cd ..`、`ls`、`ls -a`、`ls -lh` 和 `pwd`。一次广播所有目标都结束反馈后，联合视图会增加一个空行。

The common-command toolbar is outside the broadcast box, directly beneath it. The box no longer has a mode checkbox; group and recipient selection govern sending.

常用命令工具栏在广播框外、紧接框下。框内不再有模式勾选项；发送范围由当前组和接收终端的选择决定。

The log below both views records file operations, SSH/SFTP and terminal connection events, and broadcast submission or cancellation. The UI retains at most 300 events per run; parallel work has one grouped summary. A structured UTF-8 JSON-lines file persists start/finish events, elapsed time, paths, errors, and per-node batch results, rotating at 5 MB with five backups. **View → Open log file** opens the current file; the show/hide choice remains persistent and the splitter adjusts log height. Source runs save under the Windows per-user local application data directory; the portable build uses its adjacent `data/logs` folder. Separate `errors.log` and `crash.log` collect warnings, errors, uncaught exceptions and best-effort native fault traces. Command text and terminal output are not intentionally saved; broadcast submission is not remote execution success. Explorer-controlled final copies remain outside NodeBridge's observable scope.

两个视图下方的日志记录文件操作、SSH/SFTP 与终端连接事件，以及广播的提交发送或取消。界面每次运行最多保留 300 条；并行操作显示一条归并摘要。结构化 UTF-8 JSON 行文件持久保存开始／结束事件、耗时、路径、错误和逐节点结果，达到 5 MB 后轮转并保留五份旧文件。“查看 → 打开日志文件”打开当前文件；日志显示状态仍可保存，分隔器可调整高度。源码运行时保存在 Windows 用户本地应用数据目录；绿色版保存在可执行文件旁的 `data/logs`。独立的 `errors.log` 和 `crash.log` 收集警告、错误、未捕获异常，以及尽可能捕获的原生崩溃栈。程序不会主动保存命令正文和终端输出；广播提交发送不代表远端执行成功。资源管理器控制的最终复制仍不在 NodeBridge 的可观测范围内。

## Verification / 验证

The repository now has a separate bilingual developer guide and a Chinese-only end-user guide. Help opens the end-user Markdown in a nonmodal application dialog; the portable spec includes the same file. A focused GUI test confirms the menu action and visible Chinese text. Packaged Help display still needs a manual acceptance check.

仓库现有独立的双语开发者指南和纯中文用户指南。“帮助”菜单在程序内以非模态窗口显示后者的 Markdown，绿色版配置也打包同一文件。定向界面测试确认菜单入口及中文正文可见；打包程序中的实际显示仍需人工验收。

Earlier check reports showed `ChannelException(2, 'Connect failed')` on some nodes while their SFTP sessions stayed connected. This was a jump-transport channel refusal, not evidence that the file was missing. Hash checks use a separate authenticated direct-node SSH connection; after batching, at most two command channels run concurrently. Per-node errors remain visible in the file table tooltip and diagnostic log. This still needs a live retry on the cluster.

早期核对报告显示：部分节点的 SFTP 会话仍在线，但哈希命令遇到 `ChannelException(2, 'Connect failed')`。这是跳板连接拒绝新通道，不能据此判断文件缺失。现在核对会另建一条经过认证的直连节点 SSH 连接；批量改进后最多并行运行两个命令通道。逐节点错误仍可在文件表提示和诊断日志中查看。此修改仍需在集群上重试验证。

The experimental onedir build was produced with Python 3.12 and PyInstaller 6.22.3. Its directory contains `python312.dll`, Qt WebEngine resources, terminal assets, and the required Conda DLLs. A local startup smoke check kept `NodeBridge.exe` running and created `data/logs/errors.log` and `crash.log`; it did not exercise SSH, transfers, or WebEngine terminals. Seven focused diagnostics, settings, activity-log and file-operation tests passed locally. Clean-machine verification is still required.

试验性单目录版本使用 Python 3.12 和 PyInstaller 6.22.3 构建；目录包含 `python312.dll`、Qt WebEngine 资源、终端资源及所需 Conda DLL。本机启动冒烟检查确认 `NodeBridge.exe` 持续运行，并创建 `data/logs/errors.log` 与 `crash.log`；尚未测试 SSH、传输或 WebEngine 终端。另有七项诊断、设置、操作日志及文件操作定向测试在本机通过。仍需在干净机器验证。

The project-local Conda environment now runs Python 3.12.15 and the official PySide6 6.9.1 wheel with Qt WebEngine. On 2026-10-04, the previous environment's full local suite ran 130 tests: 129 passed and one symlink-creation test was skipped because this Windows test environment cannot create the link. On 2026-10-07, targeted tests covered the live xterm.js WebEngine page, ANSI/progress rendering, Unicode, control keys, PTY resize, alternate screen, terminal groups and broadcast, the full MainWindow test module, and SSH/SFTP core modules. On 2026-10-08, nine targeted logging, connection, and broadcast tests passed in the project Conda environment; the log path was checked outside the repository. These are simulated or local checks, not a cluster acceptance test. The owner previously reported a successful SSH connection and tried direct terminal input and broadcast, then observed `ChannelException(2, 'Connect failed')` while opening cft15–21. A terminal-only jump transport and bounded retry were added afterward; their tests pass locally, but the owner has not yet confirmed the result on the cluster. The owner reported that `/etc/hosts` lists `cft01`–`cft50` and `ope01`–`ope09`; discovery tests cover those ranges. Multi-group capacity, batch file operations, the revised retry path, and xterm.js interactive programs still need live validation.

项目内 Conda 环境现使用 Python 3.12.15 和包含 Qt WebEngine 的官方 PySide6 6.9.1 wheel。2026-10-04 旧环境运行完整本地测试集，共 130 项：129 项通过，另有 1 项因该 Windows 测试环境无法创建符号链接而跳过。2026-10-07 的针对性测试覆盖了实际加载的 xterm.js WebEngine 页面、ANSI／进度条渲染、Unicode、控制键、PTY 尺寸、备用屏幕、终端组与广播、完整的 MainWindow 测试模块，以及 SSH/SFTP 核心模块。2026-10-08 在项目 Conda 环境中运行了 9 项日志、连接和广播定向测试，全部通过；同时确认日志位置在仓库外。这些属于模拟或本地检查，不能代替集群验收。项目所有者此前反馈 SSH 连接成功，并试用了单终端输入和广播，之后在打开 cft15–21 时观察到 `ChannelException(2, 'Connect failed')`。其后新增终端专用跳板连接和有限次重试；本地测试已通过，但所有者尚未确认集群结果。所有者报告 `/etc/hosts` 列出了 `cft01`–`cft50` 和 `ope01`–`ope09`；节点发现测试覆盖了这些范围。多组容量、批量文件操作、修改后的重试路径及 xterm.js 交互式程序仍需实测。

An additional eight targeted file-operation and work-node directory tests passed on 2026-10-08. They cover skip, per-node numbering, cancellation, failed prechecks, and existing work-node actions; live cluster validation remains outstanding.

2026-10-08 又有 8 项文件操作和间接节点新建目录的定向测试通过，覆盖跳过、逐节点编号、取消、预检查失败及现有间接节点操作；仍需真实集群验收。

This change also ran 83 existing remote/window tests and nine focused cases for SSH probing, view refresh, gray-but-selectable nodes, equal/different/missing file hashes, and JSON report creation in the project Conda environment. All passed. No live cluster probe or hash comparison has been performed yet.

本次还在项目 Conda 环境中运行了 83 项现有远程与窗口测试，以及 9 项针对 SSH 探测、视图刷新、灰色但仍可选择的节点、相同／不同／缺失文件哈希及 JSON 报告生成的定向测试，均通过。尚未对真实集群执行新探测或哈希比较。

The remote-hash change passed five focused tests in Python 3.12.15: remote SSH command construction and safe path quoting, invalid output handling, same/different/missing comparisons without SFTP content reads, and GUI report creation. Its actual `sha256sum` execution through the owner's jump host remains to be verified live.

远程哈希修改在 Python 3.12.15 中通过 5 项定向测试：远程 SSH 命令构造与路径安全引用、无效输出处理、不经 SFTP 读取内容的相同／不同／缺失比较，以及 GUI 报告生成。仍需在项目所有者的真实跳板和工作节点上验证 `sha256sum` 的实际执行。

The full `test_remote` and `test_content_check` modules plus the report UI test also ran together: 22 tests passed. This is local simulated coverage.

随后合并运行完整的 `test_remote`、`test_content_check` 模块及报告界面测试，共 22 项通过；这仍属于本地模拟验证。

The independent probe and automatic comparison changes passed 100 remote, content-check, and window tests in Python 3.12.15, followed by eight focused regression tests after the final UI updates. They use simulated SSH/SFTP; background responsiveness and server load still need live cluster verification.

独立节点探测和自动核对修改在 Python 3.12.15 中通过 100 项远程、内容核对和窗口测试；最终界面调整后另有 8 项定向回归测试通过。它们使用模拟 SSH/SFTP；后台响应性及服务器负载仍须在真实集群验证。

The hollow-circle and batched-hash change passed 32 focused remote, content-check, and window tests in the project Python 3.12.15 environment. Tests cover quoted and newline-containing names, truncated output, one-command batches, changed metadata, and removal of the manual report UI. Actual speed improvement has not been measured on the owner's cluster.

空心圆及批量哈希修改在项目 Python 3.12.15 环境中通过 32 项远程、内容核对和窗口定向测试，覆盖含空格或换行的文件名、截断输出、单命令批量核对、元数据变化以及手动报告界面的移除。实际集群上的提速幅度尚未测量。

## Not implemented or not fully controlled / 未实现或无法完全控制

Per-command exit status for broadcast and persistent terminal sessions are not implemented. The new xterm.js full-screen frontend has local tests but no live cluster acceptance yet. Multi-source move, a persistent transfer queue and task retry, whole-batch cancellation, per-node destination overrides, and independent detailed views for every node remain planned. An experimental self-contained Windows build exists and starts locally; clean-machine verification without Python and live cluster acceptance remain pending. Site Manager supports plaintext, no-save, and master-password-encrypted site passwords; packaged profile migration still needs testing. Jump-site-to-Explorer drops are completed by Windows, which controls destination conflict prompts and completion. Work-node-to-Explorer dragging awaits Windows integration that does not interrupt the drag. Cross-application drag-and-drop and non-Windows platforms still need live verification. Multi-file operations are not transactional and may leave completed items after a later error. The revised multi-group terminal connection path, batch transfers, and deletion have not yet passed a cluster acceptance check. Work-node collection has local simulated coverage but still needs live cluster verification.

广播命令的逐命令退出状态及持久终端会话尚未实现。新的 xterm.js 全屏前端已有本地测试，但尚未通过集群验收。多来源移动、持久传输队列和任务重试、整批取消、逐节点目标路径覆盖、每个节点独立的详细视图仍待实现。试验性自包含 Windows 版本已生成并在本机启动；仍需在未预装 Python 的干净机器和真实集群验收。站点管理器支持明文、不保存及主密码加密三种模式；打包版资料迁移仍需测试。从跳板站点拖入资源管理器的复制由 Windows 完成，因此目标冲突提示和完成状态由 Windows 控制。工作节点拖入资源管理器待实现不会中断拖动的 Windows 集成。跨应用拖放和非 Windows 平台仍需实测。多文件操作不具备事务性，后续项目出错时已完成的项目可能保留。修改后的多组终端连接路径、批量传输及删除尚未完成集群验收。工作节点汇集已通过本地模拟测试，仍需在实际集群验证。
