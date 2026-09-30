# NodeBridge

NodeBridge is a desktop GUI file manager for SSH-accessible remote machines. Windows is the initial target, with possible support for other desktop platforms later. Its workflow follows familiar graphical file clients: browse local and remote trees and file lists, navigate between folders and configured nodes, and manage transfers and file operations.

NodeBridge 是面向 SSH 可访问远程机器的桌面 GUI 文件管理器。初期面向 Windows，以后可能支持其他桌面平台。其工作流沿用常见图形化文件客户端的方式：浏览本地和远程的目录树与文件列表、在目录和已配置节点间切换，以及管理传输和文件操作。

## Project documents / 项目文档

This README is the user guide. [ARCHITECTURE.md](ARCHITECTURE.md) explains the current code structure; [CURRENT_STATUS.md](CURRENT_STATUS.md) separates implemented work from remaining work; [DECISIONS.md](DECISIONS.md) records settled technical and safety choices; [HANDOFF.md](HANDOFF.md) gives the next contributor a starting point. [AGENTS.md](AGENTS.md) contains the working rules for coding agents.

本 README 是用户指南。[ARCHITECTURE.md](ARCHITECTURE.md) 说明当前代码结构；[CURRENT_STATUS.md](CURRENT_STATUS.md) 区分已实现与待完成内容；[DECISIONS.md](DECISIONS.md) 记录已确定的技术与数据安全选择；[HANDOFF.md](HANDOFF.md) 为下一位开发者提供接手入口。[AGENTS.md](AGENTS.md) 是编程代理的工作规则。

## Current status / 当前状态

**Implemented:** a two-column browser with local files on the left and remote files on the right. Each side has a path bar above a directory tree on the left and current-directory file list on the right, with an adjustable divider. File lists use compact rows without cell grid lines and right-align file sizes; common extensions such as `.txt` and `.py` have distinct icons. The File, View, and Help menus include a persistent local-site visibility setting. Files and directories can be copied by dragging between local and remote panes, from Windows Explorer into the remote list, or from the remote list into Windows Explorer. NodeBridge destination panes show an Overwrite, Skip, or Cancel dialog for same-name files; **Download to…** can target any local directory with the same dialog. File-list context menus and shortcuts provide copy/paste, rename, confirmed delete, and directory creation; local delete moves items to the Recycle Bin, while remote delete is permanent. The remote side supports SSH/SFTP navigation and refresh through background threads. A target can be reached by entering only its SSH alias from the current remote site, or by selecting a saved site, then disconnected to return to the jump site. The site manager saves named connection profiles, including optional passwords, in a per-user configuration file. The owner previously reported a successful direct connection; live jump, transfer, and new file-management operations have not yet been tested against servers. Move and transfer queue operations do not exist.

**已实现：**左侧本地、右侧远程的双栏浏览器。每栏上方是地址栏，下方是左侧目录树和右侧当前目录文件列表，中间的分隔条可拖动。文件列表使用紧凑行高、无单元格网格线，文件大小右对齐；`.txt`、`.py` 等常见类型有不同图标。“文件”“查看”“帮助”菜单中提供可持久保存的本地站点显示设置。可在本地与远程栏之间、从 Windows 资源管理器到远程列表、以及从远程列表到资源管理器拖动复制文件和文件夹。拖到 NodeBridge 目标栏时，同名文件会弹出“覆盖／跳过／取消”对话框；“下载到…”也可选择任意本地目录并使用同一提示。文件列表右键菜单与快捷键支持复制／粘贴、重命名、经确认的删除和创建目录；本地删除移入回收站，远程删除则是永久删除。远程侧在后台线程执行 SSH/SFTP 导航与刷新。可以只输入当前远程站点上的 SSH 别名，或选择已保存站点，连接目标并在断开后返回跳板站点。站点管理器可将具名站点的连接资料及可选密码保存到用户配置文件。项目所有者此前反馈直连成功；跳板、传输和新增文件管理操作尚未用真实服务器测试。移动和传输队列尚未实现。

## Requirements and direction / 需求与方向

**Planned early stages:** Windows desktop GUI; SSH-based access using standard SFTP where suitable; remote directory tree, file list, navigation and refresh; multiple configured nodes and switching; copy, move, rename, create directory, and confirmed delete; practical drag-and-drop; progress and recoverable error display. The GUI should remain responsive during network work.

**早期规划：**Windows 桌面 GUI；使用 SSH 访问，适合时采用标准 SFTP；远程目录树、文件列表、导航与刷新；配置多个节点并切换；复制、移动、重命名、新建目录和经确认的删除；适用场景下的拖放；进度与可恢复的错误显示。网络操作期间 GUI 应保持响应。

**Long-term direction:** concurrent independent jobs across multiple active nodes, such as local-to-remote, remote-to-local, and remote-to-remote transfers, with queuing, cancellation, retry, and concurrency limits. These capabilities are not implemented or committed to the initial release.

**长期方向：**在多个活动节点上并发执行独立任务，包括本地到远程、远程到本地、远程到远程的传输，并逐步支持排队、取消、重试和并发限制。这些能力尚未实现，也不属于首个版本的既定交付。

## Intended architecture / 规划架构

Model node configuration, live SSH/SFTP connection, remote filesystem session, and file-operation task as distinct concepts. Keep network and file-operation work outside GUI event handlers. Express actions as explicit tasks with source and destination endpoints, while allowing the endpoint abstraction to grow only as needed. Never assume a single global connection. Protect credentials and verify SSH host keys; require explicit intent for destructive operations.

把节点配置、实时 SSH/SFTP 连接、远程文件系统会话和文件操作任务建模为不同概念。网络和文件操作不直接在 GUI 事件处理器中执行。操作以具有来源和目标端点的明确任务表示，同时按实际需要扩展端点抽象。不假定只有一个全局连接。保护凭据并校验 SSH 主机密钥；破坏性操作需要明确意图。

## Technology decision / 技术选型

**Confirmed:** Python + PySide6, with Paramiko for SSH/SFTP and Qt background threads for blocking network calls. The initial application targets Windows; other desktop platforms remain possible but untested. The owner approved temporarily saving site passwords as plaintext in the per-user configuration file, outside this repository. On Windows, the file is `%LOCALAPPDATA%\NodeBridge\sites.json`. Quick-connect passwords are not saved automatically. SSH agent/default keys and an optional key file are supported. Host keys must already be trusted in OpenSSH `known_hosts`; unknown and changed keys are rejected. Packaging remains a future decision.

**已确定：**采用 Python + PySide6，使用 Paramiko 处理 SSH/SFTP，以 Qt 后台线程运行阻塞式网络调用。应用初期面向 Windows；其他桌面平台仍有可能，但尚未测试。项目所有者批准暂时把站点密码明文保存在仓库之外的用户配置文件；Windows 上的文件是 `%LOCALAPPDATA%\NodeBridge\sites.json`。快速连接栏的密码不会自动保存。支持 SSH agent／默认密钥及可选私钥文件。主机密钥必须预先存在于 OpenSSH 的 `known_hosts` 中；未知或变化的密钥会被拒绝。打包留待后续决定。

## Run locally / 本地运行

Use the Python 3.12 environment defined in `environment.yml`. A project-local Conda environment is already prepared at `.conda-env` on this machine. From the NodeBridge directory in Windows PowerShell, open the window with:

使用 `environment.yml` 定义的 Python 3.12 环境。这台机器已在项目内准备好 `.conda-env` 环境。在 Windows PowerShell 中进入 NodeBridge 目录，然后运行以下命令打开窗口：

```powershell
.\.conda-env\python.exe -m nodebridge
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

## Drag-to-copy / 拖动复制

Drag files or folders from the local list/tree or Windows Explorer/Desktop into the remote file list. Drop on a folder row to copy into that folder, or on an empty area to copy into the current remote directory. Drag remote file-list items to the local list/tree or into Windows Explorer/Desktop. Dropping on a local folder copies there; otherwise it copies into the currently shown local directory. The **View → Show local site** check box hides or restores the left pane, and its value is saved in per-user application settings (Windows: `HKEY_CURRENT_USER\Software\NodeBridge\NodeBridge`).

从本地文件列表／目录树或 Windows 资源管理器／桌面，把文件或文件夹拖入远程文件列表。投放到文件夹行会复制到该文件夹；投放到空白处会复制到当前远程目录。从远程文件列表可拖到本地列表／目录树，也可拖到 Windows 资源管理器／桌面。投放到本地文件夹会复制到该文件夹，否则复制到当前显示的本地目录。“查看 → 查看本地站点”可隐藏或恢复左栏，设置保存在用户应用设置中（Windows：`HKEY_CURRENT_USER\Software\NodeBridge\NodeBridge`）。

Transfers run in the background and show the current source path. On a same-name file in either NodeBridge destination pane, a dialog shows both paths, sizes, and modification times, then offers **Overwrite**, **Skip**, or **Cancel**; **Always use this action** applies Overwrite or Skip to later conflicts in the current transfer only. Existing directories merge, with file conflicts still prompted. Overwrite stages the new file before replacing the old one; remote overwrite requires the server's OpenSSH `posix-rename` SFTP extension and leaves the original intact if that replacement fails. Files and folders are copied recursively, while symbolic links and special files are rejected. If a transfer stops partway through, completed files and created directories may remain at the destination. Dragging from remote into Windows Explorer first stages a temporary local copy; Windows Explorer handles any conflict in its destination because Qt's drag source does not receive the Explorer drop directory. To have NodeBridge show the conflict dialog for Desktop or another Explorer directory, select the remote item and use **Download to…** to choose that directory. For larger dragged items, keep the mouse button down until preparation finishes; if the drag ends before that, drag the prepared item again. Temporary staging files are removed when NodeBridge exits. Live cross-application drag-and-drop still needs verification on this Windows machine.

复制任务在后台执行，并显示当前源路径。拖到 NodeBridge 任一目标栏时，遇到同名文件会弹窗显示双方路径、大小和修改时间，可选择“覆盖”“跳过”或“取消”；勾选“总是使用该操作”后，覆盖或跳过会应用到本次传输后续的同名文件。已有文件夹会合并，其中同名文件仍按所选规则处理。覆盖前会先写入临时文件再替换旧文件；远程覆盖需要服务器支持 OpenSSH 的 SFTP `posix-rename` 扩展，替换失败时保留原文件。文件和文件夹支持递归复制，符号链接和特殊文件暂不复制。如果中途停止，已完成的文件和已创建的目录可能留在目标位置。从远程拖到 Windows 资源管理器时，会先准备临时本地副本；Qt 拖动源拿不到资源管理器的投放目录，因此该目录中的同名文件仍由 Windows 提示。要让 NodeBridge 对桌面或其他资源管理器目录弹出同名提示，请选中远程项目并通过“下载到…”选择该目录。较大文件需要按住鼠标直到准备完成；若拖动提前结束，再拖一次已准备好的项目即可。临时副本会在 NodeBridge 退出时删除。跨应用拖拽仍需在这台 Windows 机器上进行实际操作验证。

## File actions / 文件操作

Right-click an item in either file list for **Copy**, **Rename**, or **Delete**. Use **Ctrl+C**, **Ctrl+V**, **F2**, and **Delete** for the same actions. To rename without a dialog, select an item and click its name again, or use F2 or the Rename menu action; edit the name in the list, then press Enter to apply or Escape to cancel. Double-clicking a folder still opens it. Right-click empty space for **Create directory** or **Create directory and enter**. Copy/paste works within NodeBridge; local files copied to the Windows clipboard can also be pasted into Explorer. Remote clipboard data is specific to NodeBridge and the current session. Remote deletion recursively removes files and directories after confirmation and cannot be undone; local deletion moves items to the Windows Recycle Bin after confirmation. Rename refuses to replace an existing item.

在任一文件列表中右键项目，可选“复制”“重命名”“删除”，也可使用 **Ctrl+C**、**Ctrl+V**、**F2** 和 **Delete**。重命名无需弹窗：先选中项目，再单击一次文件名，或者按 F2／选择右键“重命名”，即可在列表中编辑；按 Enter 提交，按 Esc 取消。双击文件夹仍会进入目录。右键空白处可选“创建目录”或“创建目录并进入”。复制／粘贴可在 NodeBridge 内使用；复制的本地文件也能通过 Windows 剪贴板粘贴到资源管理器。远程剪贴板数据仅用于 NodeBridge 当前会话。远程删除经确认后递归删除文件和目录且无法撤销；本地删除经确认后移入 Windows 回收站。重命名不会覆盖已有项目。

After a NodeBridge-managed copy, delete, rename, or directory creation finishes, a brief message appears at the lower-right of the application window for five seconds and remains in the status line. Transfer messages include copied and skipped counts. Errors still show a dialog. Direct remote-to-Explorer drops are completed by Windows, so NodeBridge cannot report their final result. Move is not implemented yet.

NodeBridge 管理的复制、删除、重命名或新建目录完成后，应用窗口右下角会显示约五秒的提示，状态栏也保留结果。传输提示包含已复制和已跳过的数量。失败时仍弹出错误对话框。从远程直接拖入资源管理器的最终复制由 Windows 完成，NodeBridge 无法报告其完成结果。移动功能尚未实现。

## SSH jump connection / SSH 中转连接

First connect to the jump site. Click **SSH alias jump…** and enter only the alias used on that site, such as `cft02` (enter `cft02`, not `ssh cft02`). NodeBridge runs the jump site's OpenSSH client and opens the target's SFTP subsystem. The alias is resolved from the jump site's SSH configuration. Passwordless login and a working SFTP subsystem are required. The target host key is checked by OpenSSH against the jump site's `known_hosts`; an unknown or changed key is rejected. Click **Return to previous site** to restore the previous connection and directory. You can continue through another alias defined on the current target.

先连接跳板站点，点击“SSH 别名中转…”，只输入该站点上使用的别名，如 `cft02`（输入 `cft02`，不要输入 `ssh cft02`）。NodeBridge 会调用跳板站点上的 OpenSSH 并打开目标的 SFTP 子系统。别名由跳板站点的 SSH 配置解析。目标须能免密登录并提供 SFTP 子系统。目标主机密钥由跳板站点上的 OpenSSH 对照其 `known_hosts` 校验；未知或变化的密钥会被拒绝。点击“返回上一站点”可恢复原连接和目录。若当前目标上还定义了其他别名，也可以继续中转。

For a first live check, sign in to the jump site in a terminal and verify `ssh cft02` works without a password. Independently verify and trust the target host key there before trying the alias in NodeBridge. If that login relies on an SSH agent forwarded from your own computer, note that NodeBridge does not forward your local agent into the jump session.

首次实测时，先在终端登录跳板站点，确认 `ssh cft02` 能免密登录；通过可信渠道核对目标主机密钥，并在跳板站点上建立信任后，再在 NodeBridge 输入别名。如果终端登录依赖从本机转发的 SSH agent，需注意 NodeBridge 目前不会把本机 agent 转发到跳板会话。

Alternatively, add the destination as a saved site and connect through the current SSH site from Site Manager. This mode uses a TCP tunnel, the saved target credentials, and the local machine's `known_hosts`. The jump SSH connection stays open while the target is active; the jump server must permit SSH TCP forwarding.

也可以把目标保存为站点，再从站点管理器选择经当前站点连接。此模式使用 TCP 隧道、已保存的目标凭据以及本机的 `known_hosts`。浏览目标期间跳板 SSH 连接保持活动；跳板服务器须允许 SSH TCP 转发。

For saved-site TCP tunnels, the target host key must be trusted in the local machine's OpenSSH `known_hosts`, even if the target is only reachable through the jump site. One way to establish that trust is to run `ssh -J jump_user@jump_host target_user@target_host` locally, verify the target fingerprint independently, and explicitly accept it. Do not accept an unexpected changed host key.

对于经站点资料建立的 TCP 隧道，即使目标只能经跳板访问，它的主机密钥也必须被本机 OpenSSH 的 `known_hosts` 信任。可以先在本机运行 `ssh -J jump_user@jump_host target_user@target_host`，通过可信渠道核对目标指纹后明确接受。遇到意外变化的主机密钥时不要直接接受。

## TODO / 待办

Replace plaintext site passwords with the operating system's protected credential store. Migrate existing entries from `sites.json`, then remove plaintext secrets from the old file. Keep the storage interface portable for possible future desktop platforms.

把明文站点密码迁移到操作系统保护的凭据库。从 `sites.json` 迁移已有记录后，删除旧文件中的明文机密信息。存储接口需为将来可能支持的其他桌面平台保留可替换性。

## Provisional roadmap / 暂定路线

1. Phase 0: architecture, documentation, and stack decision — complete.
2. Phase 1: minimal GUI and one-node SSH connection — implemented; owner reported a successful live connection with the previous layout.
3. Phase 2: remote directory browsing — implemented; local and fake-backend GUI checks passed.
4. Phase 3: basic file operations and safety behavior — copy, rename, create directory, and confirmed delete implemented; move remains planned.
5. Phase 4: drag-and-drop and transfer progress or queue — copy drag-and-drop implemented; progress bar and queue remain planned.
6. Phase 5: multiple configured nodes and switching — site profiles and sequential SSH jumps implemented; independent multi-node views remain future work.
7. Phase 6: concurrent multi-node operations.
8. Phase 7: robustness, packaging, and UX refinement.

1. 阶段 0：架构、文档和技术栈决策——已完成。
2. 阶段 1：最小 GUI 与单节点 SSH 连接——已实现；项目所有者反馈旧布局下真实连接成功。
3. 阶段 2：远程目录浏览——已实现；本地与虚拟远程后端的 GUI 检查通过。
4. 阶段 3：基本文件操作与安全行为——已实现复制、重命名、新建目录及经确认的删除；移动仍待实现。
5. 阶段 4：拖放与传输进度或队列——已实现拖动复制；进度条与队列仍待实现。
6. 阶段 5：多个节点的配置与切换——已支持站点资料和逐级 SSH 中转；独立的多节点视图仍属后续工作。
7. 阶段 6：多节点并发操作。
8. 阶段 7：健壮性、打包与交互完善。

This roadmap is provisional. The owner will review each meaningful stage before implementation continues.

此路线图为暂定规划。每个有意义的阶段结束后，由项目所有者审阅并决定后续工作。
