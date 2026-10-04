# Current status / 当前状态

This is the implementation snapshot audited on 2026-10-04. [USER_GUIDE.md](USER_GUIDE.md) explains usage, [ARCHITECTURE.md](ARCHITECTURE.md) explains the code, [DECISIONS.md](DECISIONS.md) records settled choices, and [TODO.md](TODO.md) tracks future work.

这里是截至 2026-10-04 核对过的实现快照。[USER_GUIDE.md](USER_GUIDE.md) 说明使用方法，[ARCHITECTURE.md](ARCHITECTURE.md) 说明代码结构，[DECISIONS.md](DECISIONS.md) 记录已确定的选择，[TODO.md](TODO.md) 跟踪后续工作。

## Implemented / 已实现

Windows-first PySide6 GUI with an optional local pane, a primary jump-site pane, and a work-node pane. The work-node pane discovers SSH aliases and numbered names from the jump site's `/etc/hosts`, holds separate SFTP sessions to multiple aliases, and shows either one node or merged directory entries. Dragging from local or jump to the work-node file list copies to the current node or all connected work nodes in parallel after a scope choice, regardless of checkbox state. Delete or the context menu acts on selected work-node names with the same scope choice and permanent-delete confirmation; results appear per node. The separate batch-operation button has been removed. The underlying single-destination move and other transfer routes remain in code but currently have no UI entry point. Existing direct, saved-site tunnel, and sequential SSH-alias connections remain; copy, rename, create-directory, and delete actions still apply to the primary remote pane. Local-pane visibility is persistent.

面向 Windows 的 PySide6 主窗口包含可选本地面板、主跳板站点面板和工作节点面板。工作节点面板从跳板 SSH 配置及 `/etc/hosts` 的编号名称发现节点，为多个别名保持独立 SFTP 会话，并支持单节点或合并目录查看。从本地或跳板拖到工作节点文件列表后，可选择复制到当前节点，或并行复制到所有已连接节点，不受复选框状态影响。工作节点的 Delete 键和右键菜单会对选中名称询问同样的操作范围，再确认永久删除；结果逐节点显示。独立的批量操作按钮已移除。底层单目标移动及其他传输方向仍保留代码，但当前没有界面入口。原有直连、已保存站点隧道和逐级 SSH 别名中转仍保留；复制、重命名、新建目录及删除仍作用于主远程面板。本地面板显示设置可持久保存。

The Files and Terminal tabs provide switchable main views. The Terminal view shows jump sites and discovered work-node aliases even without Files-tab worker SFTP connections. It can open multiple independent SSH PTY connections per node and connect or disconnect terminals for several checked nodes at once. Each successful connection batch creates a distinct Terminal Group; a single new terminal creates its own group. The group selector and checked member list define broadcast recipients, at most one terminal per node. No broadcast-mode checkbox is required; switching groups clears the draft. Before sending, NodeBridge checks idle shells' physical directories; a mismatch or unknown directory requires confirmation, and common destructive commands require another confirmation. The send path revalidates group membership before delivery, so another group's terminal cannot be included. Each terminal accepts direct keystrokes and retains its own ANSI-colored output. Files-tab worker disconnection leaves terminal channels open; jump disconnection closes them. Closing an individual terminal tab hides its view; Disconnect terminal closes its channel. The status reports delivery, not command completion.

“文件”和“终端”标签页可切换主视图。终端页显示跳板站点和已发现的工作节点别名，不依赖文件页工作节点 SFTP 连接。每节点可打开多个独立 SSH PTY，也可批量连接或断开。每批成功连接都会创建独立 Terminal Group；单独新建终端会形成自己的组。组选择器和成员勾选列表决定广播目标，每节点最多一个终端。无需勾选广播模式；切换组会清空草稿。发送前程序检查空闲 Shell 的实际目录；目录不同或未知时需要确认，常见破坏性命令还需额外确认。发送路径会重新验证组成员关系，其他组终端不能混入。各终端可直接输入并保留独立的 ANSI 彩色输出。文件页断开工作节点不会关闭其终端；断开跳板连接才会关闭。关闭单个终端标签只隐藏其视图，“断开终端”会关闭通道。状态栏报告发送情况，不代表命令执行完成。

New groups show their Combined tab automatically. The former “Combined view” button is replaced by “Disconnect current group”, which asks for confirmation and closes only that group's terminals. Groups and terminals have no fixed application count limit. Work-node terminals are spread across terminal-only jump SSH connections after a conservative per-connection budget; a code-2 session-open refusal triggers a bounded retry on a fresh connection. This still needs verification against the owner's cluster.

新建终端组后自动显示该组的联合视图。“联合显示”按钮已替换为“断开当前组”，确认后仅关闭该组终端。程序没有固定的组数或终端数上限。工作节点终端超过保守的单连接额度后分配到专供终端的额外跳板连接；遇到代码 2 的会话打开拒绝时，会换新连接有限次重试。这仍需在项目所有者的集群上实测。

A drop-down button at the right end of the Terminal tab bar lists every currently open terminal and Combined tab with its full title and marks the active tab. The list is rebuilt when opened, so renamed groups and hidden tabs are reflected immediately.

终端标签栏右端现有下拉按钮，以完整名称列出全部已打开的单终端和联合视图标签，并标记当前标签。每次打开菜单时重新生成列表，因此组重命名及隐藏标签会立即反映出来。

The group name can be edited; its generated node suffix compresses contiguous numbered names. Each terminal tab begins with its group name, and the broadcast panel aligns below the terminal area. For each broadcast, the read-only Combined view directly concatenates the terminals' original prompt, command echo, and output in node order, without extra headings; ANSI-derived text colors are retained. A recognizable returning Shell prompt ends that node's reply; otherwise the next broadcast bounds it. Each node's reply shows at most 20,000 characters, with the latest 100 broadcasts retained in this view; individual terminals keep their normal scrollback. The internal `pwd -P` probe echo and returning prompt are suppressed from the visible terminal in the recognized-prompt path. Identical-output deduplication and reliable exit-status detection are still planned.

组名可编辑；自动生成的节点后缀会缩写连续编号。各终端标签以所属组名开头，广播区在终端区下方对齐。对于每次广播，只读“联合显示”按节点顺序直接拼接各终端原有的提示符、命令回显及输出，不额外添加标题，并保留 ANSI 字色。识别到 Shell 提示符返回时结束该节点反馈，否则以下一次广播为边界。每节点每次命令最多显示 20,000 个字符，联合视图保留最近 100 次广播；各独立终端保留原有滚动历史。对于可识别的提示符，检查目录所用的 `pwd -P` 探针回显及其返回提示符会从可见终端中隐藏。相同输出去重和可靠的退出状态检测仍待实现。

Terminal assistance now includes a blinking caret, system bell on remote BEL or a left-boundary key at a recognizable plain Bash prompt, scrollback that stays in place while output arrives, Shift+PageUp/PageDown, and font zoom with PTY resize. The scrollback retains up to 10,000 lines.

终端辅助功能现包括闪烁光标、远端 BEL 或普通 Bash 提示符输入左边界触发的系统提示音、查看历史时保持滚动位置、Shift+PageUp/PageDown 翻页，以及同步改变 PTY 尺寸的字号缩放。历史输出最多保留一万行。

Tab in an individual terminal now reaches the remote Shell instead of moving Qt focus, enabling that Shell's normal command and path completion; remote BEL still sounds locally. The broadcast input regains focus after a successful send. Eight helper buttons appear in one toolbar row with command-and-purpose labels; narrow windows use the toolbar overflow menu. They replace the draft in the last focused command entry, either the broadcast field or a live terminal, without executing it. They provide `cd`, `mkdir`, `rm -r`, `cd ..`, `ls`, `ls -a`, `ls -lh`, and `pwd`. The Combined view adds a blank line when all recipients of a broadcast have finished replying.

单终端的 Tab 现可送达远程 Shell，而不再切换 Qt 焦点，因此能使用该 Shell 自带的命令和路径补全；远端 BEL 仍在本地响铃。广播成功发送后焦点返回广播输入框。八个助手按钮在同一行的工具栏上显示“命令（功能）”；窗口较窄时使用工具栏溢出菜单。按钮会替换最近获得输入焦点的广播框或活动终端中的草稿，不会直接执行；模板包括 `cd`、`mkdir`、`rm -r`、`cd ..`、`ls`、`ls -a`、`ls -lh` 和 `pwd`。一次广播所有目标都结束反馈后，联合视图会增加一个空行。

The common-command toolbar is outside the broadcast box, directly beneath it. The box no longer has a mode checkbox; group and recipient selection govern sending.

常用命令工具栏在广播框外、紧接框下。框内不再有模式勾选项；发送范围由当前组和接收终端的选择决定。

An in-memory log below both views records file-operation start times, plain-language actions, paths, final outcomes, and available errors. Parallel work has one grouped summary log entry, while individual node outcomes remain in the Files-tab result table. A View menu checkbox persists log visibility, and a vertical splitter adjusts its height. The log shows no API calls or Shell commands. SFTP/local API behavior is unchanged, and local-to-worker transfers do not read jump-host files. The log is cleared on exit; Explorer-controlled final copies are outside its observable scope.

两个视图下方的内存日志记录文件操作的开始时间、自然语言操作、路径、最终结果及可获得的报错。并行操作在日志中只有一条归并后的汇总，逐节点结果仍在文件页结果表中。“查看”菜单中的勾选项会保存日志显示状态；纵向分隔器可调整日志高度。日志不显示 API 调用或 Shell 命令。SFTP／本地 API 行为不变；本地传到工作节点时不会读取跳板文件。退出后日志清空；由资源管理器完成的最终复制不在其可观测范围内。

## Verification / 验证

The project-local Conda environment runs Python 3.12.11. On 2026-10-04, the full local suite ran 130 tests: 129 passed and one symlink-creation test was skipped because this Windows test environment cannot create the link. These are simulated or local checks, not a cluster acceptance test. The owner previously reported a successful SSH connection and tried direct terminal input and broadcast, then observed `ChannelException(2, 'Connect failed')` while opening cft15–21. A terminal-only jump transport and bounded retry were added afterward; their tests pass locally, but the owner has not yet confirmed the result on the cluster. The owner reported that `/etc/hosts` lists `cft01`–`cft50` and `ope01`–`ope09`; discovery tests cover those ranges. Multi-group capacity, batch file operations, and the revised retry path still need live validation.

项目内 Conda 环境使用 Python 3.12.11。2026-10-04 运行完整本地测试集，共 130 项：129 项通过，另有 1 项因该 Windows 测试环境无法创建符号链接而跳过。这些属于模拟或本地检查，不能代替集群验收。项目所有者此前反馈 SSH 连接成功，并试用了单终端输入和广播，之后在打开 cft15–21 时观察到 `ChannelException(2, 'Connect failed')`。其后新增终端专用跳板连接和有限次重试；本地测试已通过，但所有者尚未确认集群结果。所有者报告 `/etc/hosts` 列出了 `cft01`–`cft50` 和 `ope01`–`ope09`；节点发现测试覆盖了这些范围。多组容量、批量文件操作及修改后的重试路径仍需实测。

## Not implemented or not fully controlled / 未实现或无法完全控制

Per-command exit status for broadcast, full-screen PTY rendering, and persistent terminal sessions are not implemented. Collection from multiple work nodes, a persistent transfer queue and task retry, whole-batch cancellation, per-node destination overrides, independent detailed views for every node, self-contained Windows packaging, and OS-protected password storage remain planned. The Windows release must run without Python preinstalled on the target machine. Direct remote-to-Explorer drops are completed by Windows, which controls destination conflict prompts and completion. Cross-application drag-and-drop and non-Windows platforms still need live verification. Multi-file operations are not transactional and may leave completed items after a later error. The revised multi-group terminal connection path, batch transfers, and deletion have not yet passed a cluster acceptance check.

广播命令的逐命令退出状态、全屏 PTY 显示和持久终端会话尚未实现。从多个工作节点汇集、持久传输队列和任务重试、整批取消、逐节点目标路径覆盖、每个节点独立的详细视图、自包含 Windows 打包和系统凭据库存储仍待实现。Windows 发行包必须能在目标机器未预装 Python 时运行。从远程直接拖入资源管理器的复制由 Windows 完成，因此目标冲突提示和完成状态由 Windows 控制。跨应用拖放和非 Windows 平台仍需实测。多文件操作不具备事务性，后续项目出错时已完成的项目可能保留。修改后的多组终端连接路径、批量传输及删除尚未完成集群验收。
