# User guide / 使用指南

## Run locally / 本地运行

Use the Python 3.12 environment defined in `environment.yml`. Create the project-local Conda environment if needed. From the NodeBridge directory in Windows PowerShell, open the window with:

使用 `environment.yml` 定义的 Python 3.12 环境。如果项目内尚无 `.conda-env` 环境，请先创建。在 Windows PowerShell 中进入 NodeBridge 目录，然后运行以下命令打开窗口：

```powershell
conda env create --prefix .\.conda-env --file environment.yml
.\.conda-env\python.exe -m nodebridge
```

After connecting to the jump site, NodeBridge reads explicit `Host` aliases from that site's user and system SSH config and numbered hostnames from `/etc/hosts`, regardless of prefix. It excludes the jump host itself. If `/etc/hosts` contains no numbered hosts, it probes nearby names with the jump site's `getent hosts` (up to 20 seconds). These are candidates, not a reachability check; wildcard patterns and cluster schedulers do not provide a universal node inventory. Select aliases and click **Connect selected**, or use **Add alias…**. Click one work-node checkbox, then hold Shift and click another to check or uncheck the full range. Connected nodes can be browsed in merged or single-node mode. Checkboxes choose nodes for connection and disconnection and clear after either button; drag-copy and Delete can act on the current node or all connected work nodes. If many nodes are selected for connection, NodeBridge opens extra jump-site SSH connections as needed; the jump-site password stays in memory only until disconnection.

连接跳板站点后，NodeBridge 会读取该站点用户及系统 SSH 配置中明确写出的 `Host` 别名，以及 `/etc/hosts` 中所有带编号的主机名，不限前缀，并排除跳板节点本身。仅当 `/etc/hosts` 中没有带编号的主机名时，才用跳板站点的 `getent hosts` 查询相近编号的名称（最长 20 秒）。查到的是候选节点，不代表已验证可达；通配规则和集群调度器也不提供统一的节点清单。勾选别名并点击“连接勾选”，或用“添加别名…”输入名称。先点击一个工作节点的复选框，再按住 Shift 点击另一个，可勾选或取消勾选两者之间的全部节点。已连接节点可合并或单节点查看。复选框用于选择要连接或断开的节点；点击任一按钮后会清空勾选。拖动复制和 Delete 可以作用于当前节点或所有已连接的工作节点。选择大量节点进行连接时，NodeBridge 会按需建立额外跳板 SSH 连接；跳板密码仅保留在内存中直至断开连接。

## Batch work-node operations / 工作节点批量操作

Drag files or folders from the local or jump-site file list, or from Windows Explorer/Desktop, into the work-node file list. Drop on a folder row to copy into that directory, or on empty space to copy into the shown work-node directory. When two or more work nodes are connected, choose whether to copy only to the current node or in parallel to all connected nodes. Select names in the work-node file list and press **Delete** or use its right-click menu for the same scope choice, followed by a separate permanent-delete confirmation. Checkbox state does not affect this operation scope; the result table shows each node separately. Dragging copies and keeps the source.

从本地或跳板节点的文件列表，或 Windows 资源管理器／桌面，将文件或文件夹拖到工作节点文件列表。投放到文件夹行时复制进该文件夹，投放到空白处时复制进当前显示的工作节点目录。连接了两个或更多工作节点后，可选择只复制到当前节点，或并行复制到所有已连接节点。在工作节点文件列表中选中项目后按 **Delete** 或通过右键菜单删除，也会先选择单节点或多节点范围，再单独确认永久删除。此操作范围与复选框是否勾选无关；下方结果表逐节点显示结果。拖动操作只复制，保留来源。

Drag selected files or folders from the work-node list onto the jump or local file list. Release the mouse over the destination first; then choose the current node or all connected nodes. NodeBridge copies directly over SFTP. Each copied top-level folder gains `_<node>` (for example `results_cft02`); a file gains the suffix before its extension (for example `output_cft02.txt`). Contents inside folders retain their original names. This is copy only: sources remain on work nodes. Existing destination names use the Overwrite, Skip, or Cancel dialog. Dragging work-node items directly to Windows Explorer or the desktop is not currently supported. The underlying single-destination move engine exists, but the drag-and-delete interface does not expose move. Remote batch delete is permanent. Completed copies or deletions are not rolled back if a later node fails; review the result table before retrying.

从工作节点文件列表选中文件或文件夹，拖到跳板或本地文件列表。先在目标位置松开鼠标，再选择当前节点或所有已连接节点；NodeBridge 随后直接通过 SFTP 复制。复制的顶层文件夹名追加 `_<节点名>`（如 `results_cft02`）；文件在扩展名前追加后缀（如 `output_cft02.txt`）。文件夹内部名称保持不变。汇集只复制，工作节点上的来源保留。目标同名时使用软件的“覆盖／跳过／取消”对话框。目前不支持把工作节点项目直接拖到 Windows 桌面或资源管理器。底层已有单目标移动执行逻辑，但拖放和删除界面尚未提供移动入口。远程批量删除是永久删除。若后续节点失败，已完成的复制或删除不会回滚；重试前请查看结果表。

If **Discover nodes** finds none, sign in to the jump site in a terminal and inspect how `ssh cft02` is resolved. The following commands inspect configuration and name resolution. `Host` entries reveal explicit candidates; `ssh -G` shows effective settings for a known name, but does not list all possible names. `getent` checks name resolution, and `sinfo` is relevant only if the site uses Slurm.

如果“发现节点”没有找到候选项，可先在终端登录跳板站点，检查 `ssh cft02` 如何解析。以下命令只检查配置与名称解析。`Host` 条目给出明确列出的候选名；`ssh -G` 只能显示某个已知名称的生效配置，不能列出所有可能的名称。`getent` 检查名称解析；仅在站点使用 Slurm 时才需要看 `sinfo`。

```sh
type ssh
grep -HnEi '^[[:space:]]*(Host|Include)([[:space:]]|=)' ~/.ssh/config ~/.ssh/config.d/* /etc/ssh/ssh_config /etc/ssh/ssh_config.d/* 2>/dev/null
ssh -G cft02 2>/dev/null | awk '$1=="hostname" || $1=="user" || $1=="port" || $1=="proxyjump" {print}'
getent hosts cft02
command -v sinfo && sinfo -N -h -o '%N' | head
```

To recreate the environment on another Windows machine, run the following from the project root. The `nodebridge` package lives in the project root, so no editable installation is needed. Activation is not required because the commands use the environment's Python directly.

在另一台 Windows 机器上重建环境时，从项目根目录运行以下命令。`nodebridge` 包位于项目根目录，无需可编辑安装。命令直接使用环境中的 Python，也无需激活环境。

```powershell
conda env create --prefix .\.conda-env --file environment.yml
.\.conda-env\python.exe -m unittest discover -s tests -v
.\.conda-env\python.exe -m nodebridge
```

Before connecting, independently verify the server's SSH host-key fingerprint and add it to your normal OpenSSH `known_hosts` (for example, through an explicitly confirmed `ssh user@host` connection). Enter connection details in the quick-connect row, or open Site Manager to create a named profile and connect from it. A password saved in Site Manager is reused on later connections. Both sides support address entry, tree navigation, double-clicking folders in the file list, Up, and Refresh. Dragging copies files but does not move or delete the originals.

连接前请独立核对服务器 SSH 主机密钥指纹，并将其加入常用 OpenSSH `known_hosts`（例如通过明确确认过的 `ssh user@host` 连接）。可以在快速连接栏填写资料，也可以打开站点管理器创建具名站点并连接。在站点管理器保存密码后，下次连接会复用该密码。两侧都支持输入地址、通过树导航、双击文件列表中的目录，以及使用“上一级”“刷新”。拖动操作只复制，不移动或删除源文件。

When running from source, saved sites remain outside the repository at `%LOCALAPPDATA%\NodeBridge\sites.json` on this Windows setup. Site Manager offers three password modes: save as plaintext, encrypt with a master password, or do not save. Choose the mode in Site Manager and save; an older version-1 file is upgraded on the next save. In master-password mode, enter the master password once per application run when opening Site Manager. If it is forgotten, the saved passwords cannot be recovered; cancelling the unlock offers a confirmed reset that retains site metadata but removes all saved passwords. The planned portable executable will store `data/sites.json` beside the executable; packaging and migration from the source-run location have not yet been verified. Do not share or commit site files; old backups may still contain plaintext passwords.

从源码运行时，在这套 Windows 环境中，站点资料仍存于仓库之外的 `%LOCALAPPDATA%\NodeBridge\sites.json`。站点管理器现提供明文保存、使用主密码加密保存和不保存密码三种模式。选择模式并保存后，旧版 v1 文件会升级；主密码模式下，每次运行首次打开站点管理器时需输入主密码。遗忘后无法恢复已保存密码；取消解锁时可经确认清除全部密码，同时保留站点资料。计划中的绿色版程序会把 `data/sites.json` 放在可执行文件旁，但打包及从源码运行位置迁移尚未验证。请勿分享或提交站点文件；旧备份仍可能包含明文密码。

## Drag-to-copy / 拖动复制

Drag files or folders from the local list/tree or Windows Explorer/Desktop into the remote file list. Drop on a folder row to copy into that folder, or on an empty area to copy into the current remote directory. Drag remote file-list items to the local list/tree or into Windows Explorer/Desktop. Dropping on a local folder copies there; otherwise it copies into the currently shown local directory. The Files view labels the primary jump site **Direct node** and nodes reached through it **Indirect nodes**. The **View → Show local site** check box hides or restores the left pane. Its value and the last opened local directory are saved in per-user application settings (Windows: `HKEY_CURRENT_USER\Software\NodeBridge\NodeBridge`). If that directory is unavailable at startup, the home directory opens instead.

从本地文件列表／目录树或 Windows 资源管理器／桌面，把文件或文件夹拖入远程文件列表。投放到文件夹行会复制到该文件夹；投放到空白处会复制到当前远程目录。从远程文件列表可拖到本地列表／目录树，也可拖到 Windows 资源管理器／桌面。投放到本地文件夹会复制到该文件夹，否则复制到当前显示的本地目录。文件页把主跳板站点称为“直连节点”，经它到达的节点称为“间接节点”。“查看 → 查看本地站点”可隐藏或恢复左栏；显示状态和最近打开的本地目录保存在用户应用设置中（Windows：`HKEY_CURRENT_USER\Software\NodeBridge\NodeBridge`）。若下次启动时该目录不可用，则打开用户主目录。

**View → Show log** hides or restores the operation log below the Files/Terminal tabs. Drag the horizontal divider above the log to adjust its height. The visibility choice is saved in the same per-user application settings.

**“查看 → 查看日志”** 可隐藏或恢复“文件／终端”标签页下方的操作日志。拖动日志上方的横向分隔条即可调整高度。显示状态同样保存在用户应用设置中。

Transfers run in the background and show the current source path. On a same-name file in either NodeBridge destination pane, a dialog shows both paths, sizes, and modification times, then offers **Overwrite**, **Skip**, or **Cancel**; **Always use this action** applies Overwrite or Skip to later conflicts in the current transfer only. Existing directories merge, with file conflicts still prompted. Overwrite stages the new file before replacing the old one; remote overwrite requires the server's OpenSSH `posix-rename` SFTP extension and leaves the original intact if that replacement fails. Files and folders are copied recursively, while symbolic links and special files are rejected. If a transfer stops partway through, completed files and created directories may remain at the destination. Dragging from remote into Windows Explorer first stages a temporary local copy; Windows Explorer handles any conflict in its destination because Qt's drag source does not receive the Explorer drop directory. To have NodeBridge show the conflict dialog for Desktop or another Explorer directory, select the remote item and use **Download to…** to choose that directory. For larger dragged items, keep the mouse button down until preparation finishes; if the drag ends before that, drag the prepared item again. Temporary staging files are removed when NodeBridge exits. Live cross-application drag-and-drop still needs verification on this Windows machine.

复制任务在后台执行，并显示当前源路径。拖到 NodeBridge 任一目标栏时，遇到同名文件会弹窗显示双方路径、大小和修改时间，可选择“覆盖”“跳过”或“取消”；勾选“总是使用该操作”后，覆盖或跳过会应用到本次传输后续的同名文件。已有文件夹会合并，其中同名文件仍按所选规则处理。覆盖前会先写入临时文件再替换旧文件；远程覆盖需要服务器支持 OpenSSH 的 SFTP `posix-rename` 扩展，替换失败时保留原文件。文件和文件夹支持递归复制，符号链接和特殊文件暂不复制。如果中途停止，已完成的文件和已创建的目录可能留在目标位置。从远程拖到 Windows 资源管理器时，会先准备临时本地副本；Qt 拖动源拿不到资源管理器的投放目录，因此该目录中的同名文件仍由 Windows 提示。要让 NodeBridge 对桌面或其他资源管理器目录弹出同名提示，请选中远程项目并通过“下载到…”选择该目录。较大文件需要按住鼠标直到准备完成；若拖动提前结束，再拖一次已准备好的项目即可。临时副本会在 NodeBridge 退出时删除。跨应用拖拽仍需在这台 Windows 机器上进行实际操作验证。

## File actions / 文件操作

Right-click an item in the local or primary remote file list for **Copy**, **Rename**, or **Delete**. Use **Ctrl+C**, **Ctrl+V**, **F2**, and **Delete** for the same actions. To rename without a dialog, select an item and click its name again, or use F2 or the Rename menu action; edit the name in the list, then press Enter to apply or Escape to cancel. Double-clicking a folder still opens it. Right-click empty space for **Create directory** or **Create directory and enter**. Copy/paste works within NodeBridge; local files copied to the Windows clipboard can also be pasted into Explorer. Remote clipboard data is specific to NodeBridge and the current session. Remote deletion recursively removes files and directories after confirmation and cannot be undone; local deletion moves items to the Windows Recycle Bin after confirmation. Rename refuses to replace an existing item. The work-node file list supports drag-in copying and confirmed Delete/right-click deletion, but does not offer inline rename.

在本地或主远程文件列表中右键项目，可选“复制”“重命名”“删除”，也可使用 **Ctrl+C**、**Ctrl+V**、**F2** 和 **Delete**。重命名无需弹窗：先选中项目，再单击一次文件名，或者按 F2／选择右键“重命名”，即可在列表中编辑；按 Enter 提交，按 Esc 取消。双击文件夹仍会进入目录。右键空白处可选“创建目录”或“创建目录并进入”。复制／粘贴可在 NodeBridge 内使用；复制的本地文件也能通过 Windows 剪贴板粘贴到资源管理器。远程剪贴板数据仅用于 NodeBridge 当前会话。远程删除经确认后递归删除文件和目录且无法撤销；本地删除经确认后移入 Windows 回收站。重命名不会覆盖已有项目。工作节点文件列表支持拖入复制及经确认的 Delete／右键删除，但尚无行内重命名。

After a NodeBridge-managed copy, delete, rename, or directory creation finishes, a brief message appears at the lower-right of the application window for five seconds and remains in the status line. The operation log below the Files/Terminal tabs records the start time, a plain-language action and paths, then updates that entry with success, failure and the available error, or cancellation/partial completion. A parallel operation produces one summary log entry that groups nodes with identical error text; the Files-tab result table retains individual node details. The log shows no Python API calls or Shell commands. Actual file operations continue through SFTP or local filesystem APIs. The log stays in memory only and clears when the application closes. Single-operation errors still show a dialog. Direct remote-to-Explorer drops are completed by Windows, so NodeBridge can log preparation of the drag cache but cannot report the final Explorer copy result.

NodeBridge 管理的复制、删除、重命名或新建目录完成后，应用窗口右下角会显示约五秒的提示，状态栏也保留结果。“文件／终端”标签页下方的操作日志会记录开始时间、自然语言操作和路径，然后把同一条记录更新为成功、失败及可获得的报错，或取消／部分完成。并行操作只生成一条汇总日志，把报错文字相同的节点合并显示；文件页下方的结果表仍保留逐节点详情。日志不显示 Python API 或 Shell 命令。实际文件操作继续使用 SFTP 或本地文件系统 API。日志只保存在内存中，关闭程序即清空。单项操作失败时仍弹出错误对话框。从远程直接拖入资源管理器的最终复制由 Windows 完成，NodeBridge 只能记录拖动缓存的准备结果，无法报告资源管理器最终的复制结果。

For local-to-work-node copies, NodeBridge reads the source directly from the local computer and writes to that worker's SFTP session through the jump connection. It never chooses a same-named file from the jump node's filesystem. The jump node relays SSH traffic only; a different file at the same path on the jump node is unaffected.

从本地复制到工作节点时，NodeBridge 直接读取本机源文件，经跳板连接写入该工作节点的 SFTP 会话，不会从跳板节点文件系统中挑选同名文件。跳板节点只转发 SSH 流量；其同路径下内容不同的文件不参与传输，也不会因此被修改。

## SSH jump connection / SSH 中转连接

First connect to the jump site. Click **Add indirect node…** and enter only its SSH alias on that site, such as `cft02` (enter `cft02`, not `ssh cft02`), or choose an alias from the discovered list. NodeBridge runs the jump site's OpenSSH client and opens the target's SFTP subsystem while keeping the jump pane visible. Passwordless login and a working SFTP subsystem are required. The target host key is checked by OpenSSH against the jump site's `known_hosts`; an unknown or changed key is rejected. Select an indirect node to inspect it individually, or use merged view for entries at the shared path. Check nodes and click **Disconnect selected** when finished.

先连接跳板站点，再点击“添加间接节点…”并仅输入该站点上的 SSH 别名，如 `cft02`（不要输入 `ssh cft02`），或在已发现的列表中选择别名。NodeBridge 调用跳板站点上的 OpenSSH 打开目标的 SFTP 子系统，同时保留跳板面板。目标须能免密登录并提供 SFTP 子系统。目标主机密钥由跳板站点的 OpenSSH 对照其 `known_hosts` 校验；未知或变化的密钥会被拒绝。选中间接节点可单独查看，合并视图则显示共同路径下的条目。完成后勾选间接节点并点击“断开勾选”。

For a first live check, sign in to the jump site in a terminal and verify `ssh cft02` works without a password. Independently verify and trust the target host key there before trying the alias in NodeBridge. If that login relies on an SSH agent forwarded from your own computer, note that NodeBridge does not forward your local agent into the jump session.

首次实测时，先在终端登录跳板站点，确认 `ssh cft02` 能免密登录；通过可信渠道核对目标主机密钥，并在跳板站点上建立信任后，再在 NodeBridge 输入别名。如果终端登录依赖从本机转发的 SSH agent，需注意 NodeBridge 目前不会把本机 agent 转发到跳板会话。

Alternatively, add the destination as a saved site and connect through the current SSH site from Site Manager. This mode uses a TCP tunnel, the saved target credentials, and the local machine's `known_hosts`. The jump SSH connection stays open while the target is active; the jump server must permit SSH TCP forwarding.

也可以把目标保存为站点，再从站点管理器选择经当前站点连接。此模式使用 TCP 隧道、已保存的目标凭据以及本机的 `known_hosts`。浏览目标期间跳板 SSH 连接保持活动；跳板服务器须允许 SSH TCP 转发。

For saved-site TCP tunnels, the target host key must be trusted in the local machine's OpenSSH `known_hosts`, even if the target is only reachable through the jump site. One way to establish that trust is to run `ssh -J jump_user@jump_host target_user@target_host` locally, verify the target fingerprint independently, and explicitly accept it. Do not accept an unexpected changed host key.

对于经站点资料建立的 TCP 隧道，即使目标只能经跳板访问，它的主机密钥也必须被本机 OpenSSH 的 `known_hosts` 信任。可以先在本机运行 `ssh -J jump_user@jump_host target_user@target_host`，通过可信渠道核对目标指纹后明确接受。遇到意外变化的主机密钥时不要直接接受。

## SSH terminals / SSH 终端

The main window has **Files** and **Terminal** tabs. The Terminal tab lists jump sites and discovered work-node aliases even without a Files-tab SFTP connection. Check nodes in **Batch selection** (Shift selects a range), then choose **Connect checked nodes**. All terminals opened successfully in that batch belong to one new Terminal Group; a later batch or **New terminal** creates another group. **Disconnect terminal** closes one channel; closing an individual terminal tab only hides its view. Worker SFTP disconnection leaves its terminals open, while jump-site disconnection closes dependent terminals. Click a black terminal area to type directly; Enter submits, Ctrl+C interrupts, Ctrl+D sends EOF, Ctrl+Shift+C copies selected output, and Ctrl+V pastes.

主窗口有“文件”和“终端”两个标签页。终端页会列出跳板站点及已发现的工作节点别名，即使工作节点尚未在文件页建立 SFTP 连接。在“批量选择”列勾选节点（Shift 可连续选择），再点击“连接勾选节点”。本批成功打开的终端归属一个新 Terminal Group；下一批或“新建终端”会形成另一个组。“断开终端”关闭一个通道；关闭单个终端的标签只隐藏其视图。文件页断开工作节点 SFTP 不会关闭其终端，断开跳板则会关闭依赖它的终端。点击黑色终端区域可直接输入；Enter 执行，Ctrl+C 中断，Ctrl+D 发送 EOF，Ctrl+Shift+C 复制选中输出，Ctrl+V 粘贴。

To broadcast, choose the Terminal Group in the left selector, check the desired sessions in **This group's broadcast targets**, then enter and send the command. No broadcast-mode checkbox is needed. At most one terminal per node receives it, and no terminal in another group can receive it. Switching groups clears the draft. NodeBridge checks idle shells' physical directories with `pwd -P`; differing or unverified directories require confirmation. Common destructive commands require a further confirmation. The status reports delivery, not execution success; each terminal keeps its own output.

要广播命令，先在左侧选择 Terminal Group，再在“本组广播目标”中勾选要接收的终端，然后直接输入命令并发送，不需要勾选广播模式。每个节点最多一个终端接收，其他组的终端不会接收。切换组会清空尚未发送的命令。程序用 `pwd -P` 检查空闲 Shell 的实际工作目录；目录不同或无法确认时需要手动确认。常见破坏性命令还需额外确认。状态栏只报告发送情况，不代表执行成功；各终端分别保留输出。

The common-command toolbar sits below and outside the broadcast box. It can still prepare a command for either the broadcast field or an individual terminal, according to the last input focus.

常用命令工具栏位于广播框下方、框外。它仍按最近的输入焦点，为广播框或单个终端准备命令。

The broadcast controls sit directly below the terminal area. Group names are compact, for example `终端组1(cft02..04)`; use **Rename** to change the name before the parentheses, which always show the member nodes. Each terminal tab starts with its group name. A group with multiple terminals opens its read-only **Combined** tab by default; a single-terminal group opens the individual terminal instead. Selecting a group follows the same rule. **Disconnect current group** asks for confirmation and closes only that group's terminals. For each broadcast the Combined tab directly concatenates the terminals' actual prompt, command echo, and output in node order, regardless of SSH arrival order. It adds no node or command headings and preserves terminal text colors. A recognizable returning Shell prompt closes that node's reply; when no prompt can be recognized, the next broadcast bounds it. Up to 20,000 characters are shown per node per command, and the latest 100 commands are retained in this view. Original terminals keep their own output. Identical-result deduplication remains planned.

广播区位于终端区域正下方。组名会缩写为如 `终端组1(cft02..04)`，使用“重命名”可修改括号前的名称；括号中的成员节点始终显示。每个终端标签以所属组名开头。多终端组默认打开只读的“联合”标签；单终端组直接显示该终端。切换组时也按此规则显示。“断开当前组”经确认后只关闭本组终端。对于每次广播，联合视图按节点顺序直接拼接各终端真实的提示符、命令回显及输出，不受 SSH 数据到达顺序影响，不额外添加节点或命令标题，并保留终端字色。可识别的 Shell 提示符返回时结束该节点本轮反馈；若无法识别提示符，则以下一次广播为边界。每节点每次命令最多显示 20,000 个字符，联合视图保留最近 100 次命令；独立终端仍保留自身输出。相同结果去重仍待实现。

The small down-arrow at the right end of the terminal tab bar lists every open individual and Combined tab by its full title. The current tab is checked; select another entry to switch directly to it. Hidden individual tabs are absent until reopened from the node tree.

终端标签栏最右侧的小下拉箭头会按完整名称列出当前打开的单终端和联合视图标签。当前标签带勾选标记；选择其他条目即可直接切换。已隐藏的单终端标签不在列表中，从节点树重新打开后会再次出现。

In an individual terminal, Tab goes to the remote Shell for its normal command, name, and path completion. Its BEL signal sounds through NodeBridge when a completion is ambiguous. After all recipients of a broadcast have replied, the Combined view leaves one blank line before the next broadcast. Successful sending returns keyboard focus to the broadcast input. The one-row helper toolbar below that input works on whichever command entry last had the caret: the broadcast field or an individual terminal. Each button shows its command and purpose, for example `cd（进入目录）`; narrow windows place later buttons in the toolbar's overflow menu. A button replaces the current draft with `cd `, `mkdir `, `rm -r `, `cd ..`, `ls`, `ls -a`, `ls -lh`, or `pwd` without executing it. In a terminal this uses the remote Shell's line-editing keys. Recursive removal still receives the broadcast's destructive-command confirmation.

在单个终端中，Tab 会送往远程 Shell，使用其命令、名称和路径补全；匹配不唯一时远程 Shell 发出的 BEL 会由 NodeBridge 播放提示音。一次广播的所有节点反馈完毕后，联合视图在下一次广播前留一个空行。发送成功后，键盘焦点回到广播输入框。其下方的常用命令工具栏只有一行，按钮显示“命令（功能）”，例如 `cd（进入目录）`；窗口较窄时，后面的按钮会收进工具栏的溢出菜单。按钮作用于最近放置输入光标的位置：广播框或单终端。点击后会清空原草稿，并填入 `cd `、`mkdir `、`rm -r `、`cd ..`、`ls`、`ls -a`、`ls -lh` 或 `pwd`，不会立即执行。在单终端中，清空使用远程 Shell 的行编辑按键。递归删除仍会触发广播的破坏性命令确认。

Next, extend the connected SSH shells with full-screen PTY rendering; local PowerShell, CMD, and WSL terminals are outside this scope. Direct input supports `cd`, environment activation, long-running commands, keyboard interrupts, and terminal resize. Full-screen interactive programs such as `vim` and `top` still require cursor-positioning terminal emulation.

下一步在已连接的 SSH Shell 上完善 PTY 全屏显示；本地 PowerShell、CMD 和 WSL 终端不在此范围。目前可直接输入 `cd`、环境激活及长时间运行的命令，也支持键盘中断和终端尺寸变化；`vim`、`top` 等依赖光标定位的全屏交互程序仍需完整终端仿真。

The terminal shows ANSI colors sent by the server. For a plain Bash prompt with no color codes, it colors the user and host green and the path blue without changing the server's output. It also treats the doubled carriage returns produced by some SSH jumps as one line break.

终端优先显示服务器发送的 ANSI 颜色。若 Bash 提示符未带颜色控制码，NodeBridge 会把用户名和主机名显示为绿色、路径显示为蓝色，不修改服务器的原始输出。某些 SSH 跳转产生的双重回车也会合并成一次换行。

The active terminal has a blinking caret. A BEL sent by the remote shell plays the system notification sound; at the left edge of a recognizable plain Bash prompt, pressing Left also rings once if the remote shell does not. Scrolling up preserves the viewed position while new output arrives, with up to 10,000 lines retained. Shift+PageUp/PageDown scrolls locally; Ctrl+Plus/Minus changes the font size and updates the remote PTY size; Ctrl+0 restores the default font size.

活动终端显示闪烁光标。远端 Shell 发出 BEL 时会播放系统提示音；对于能识别的普通 Bash 提示符，在最左输入位置继续按左方向键也会响一次，避免远端不发 BEL 时没有反馈。向上查看历史时，新输出不会把视图强行拉到底部，最多保留一万行。Shift+PageUp/PageDown 在本地翻页；Ctrl+加号／减号调整字号并同步更新远端 PTY 尺寸；Ctrl+0 恢复默认字号。
