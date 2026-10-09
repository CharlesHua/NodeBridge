# Handoff / 接手说明

This file helps the next contributor resume work without relying on chat history. Read [AGENTS.md](../AGENTS.md) for working rules, [DEVELOPER_GUIDE.md](DEVELOPER_GUIDE.md) for author and agent workflow, [CURRENT_STATUS.md](CURRENT_STATUS.md) for the verified state, [DECISIONS.md](DECISIONS.md) for constraints, and [ARCHITECTURE.md](ARCHITECTURE.md) for code boundaries. [README.md](../README.md) is the GitHub overview; the Chinese-only [USER_GUIDE.md](USER_GUIDE.md) is also displayed inside Help, and [TODO.md](TODO.md) tracks planned work.

本文帮助下一位开发者在不依赖聊天记录的情况下接续工作。工作规则见 [AGENTS.md](../AGENTS.md)，作者与 Agent 工作流程见 [DEVELOPER_GUIDE.md](DEVELOPER_GUIDE.md)，已验证状态见 [CURRENT_STATUS.md](CURRENT_STATUS.md)，约束与选择见 [DECISIONS.md](DECISIONS.md)，代码边界见 [ARCHITECTURE.md](ARCHITECTURE.md)。[README.md](../README.md) 是 GitHub 首页简介；纯中文 [USER_GUIDE.md](USER_GUIDE.md) 也显示在程序的“帮助”菜单中，后续工作见 [TODO.md](TODO.md)。

## Local commands / 本地命令

From the project root on Windows, use the project-local Python 3.12 environment. Set `QT_QPA_PLATFORM=offscreen` only for automated GUI tests; launch the normal app without that variable. Tests must mock the Windows clipboard when writing NodeBridge's custom MIME format under Qt offscreen mode, because the native Qt process may crash on exit otherwise.

在 Windows 项目根目录使用项目内的 Python 3.12 环境。仅在自动化界面测试时设置 `QT_QPA_PLATFORM=offscreen`；正常启动应用时不要设置。Qt 离屏测试写入 NodeBridge 自定义 MIME 格式时必须模拟 Windows 剪贴板，否则原生 Qt 进程可能在退出时崩溃。

```powershell
$env:QT_QPA_PLATFORM = 'offscreen'
.\.conda-env\python.exe -m unittest discover -s tests -v
Remove-Item Env:QT_QPA_PLATFORM
.\.conda-env\python.exe -m nodebridge
```

## Next checks / 下一步检查

The older local test build at `dist/NodeBridge/` keeps its newer active profile and logs in `data/`. A previously interrupted rebuild left an older settings-only backup at `dist/NodeBridge/data-backup-before-rebuild/`; it was retained separately to avoid replacing newer settings. The current build script instead writes each new release to `dist/portable-builds/<build-id>/NodeBridge/` and updates `dist/NodeBridge-portable.zip` without touching old build data.

较早的本机测试版 `dist/NodeBridge/` 将较新的站点资料和日志保存在 `data/`。此前一次被中断的重建留下了较旧、仅含设置的备份 `dist/NodeBridge/data-backup-before-rebuild/`；为避免覆盖新设置，已单独保留。当前构建脚本改为把每次新版本写入 `dist/portable-builds/<构建编号>/NodeBridge/`，并更新 `dist/NodeBridge-portable.zip`，不触碰旧版数据。

Earlier cluster reports recorded `ChannelException(2, 'Connect failed')` for hash commands despite connected SFTP sessions. The hash path uses a separate direct-node SSH transport and now runs at most two batched command channels. Retry a merged directory across many nodes on the owner's cluster; confirm whether those per-node errors disappear. If not, inspect the difference-column tooltips, diagnostic log, and direct-node SSH session limits before changing file-read logic.

早期集群核对中，尽管 SFTP 会话已连接，哈希命令仍出现 `ChannelException(2, 'Connect failed')`。核对现另建直连节点 SSH 连接，最多同时开启两个批量命令通道。请在项目所有者的集群上对同一目录和多个节点重试，确认这些逐节点错误是否消失；若仍存在，先检查差异栏提示、诊断日志和直连节点 SSH 会话限制，再考虑修改文件读取逻辑。

After node discovery, a separate background SSH connection probes candidates with passwordless login; gray failures remain selectable. Check on the owner's cluster that host-key, authentication, and powered-off failures give useful reasons and that the jump host tolerates concurrent probes. Reentering a directory and switching views rereads listings. A merged-directory refresh automatically checks SHA-256 for same-name, same-size regular files on all connected nodes through isolated SFTP and batched command connections. Verify that browsing and file actions remain usable, old-path results do not reappear, and large files do not overload the cluster. Test filenames with spaces, missing `sha256sum -z`, changing files, and many nodes. Directory recursion and post-transfer verification remain planned. Task Workspace remains a design discussion only.

发现节点后，独立后台 SSH 连接对候选做免密探测；灰色的失败项仍可重试。请在项目所有者集群上检查主机密钥、认证与关机节点的错误说明，以及跳板能否承受并发探测。重新进入目录和切换视图会重新读取列表。合并目录刷新后，程序通过独立的 SFTP 和批量命令连接，自动核对所有已连接节点上同名、同大小普通文件的 SHA-256。请验证浏览和文件操作仍可用、旧路径结果不会回写，以及大文件不会让集群负载过高。还须实测带空格文件名、缺少 `sha256sum -z`、变化中的文件和大量节点。目录递归与传输后校验仍待实现；任务工作区目前只讨论设计。

An experimental self-contained Windows one-folder build and archive are produced by `scripts/build_portable.py`; the archive excludes `data`. The executable started locally with the bundled Python and created separate diagnostic logs. Before release, test it on a clean machine without Python and on the owner's cluster, including Qt WebEngine terminal use, transfer behavior, profile persistence, and updates. See [PORTABLE.md](PORTABLE.md) and [TODO.md](TODO.md).

`scripts/build_portable.py` 现可生成试验性的自包含 Windows 单目录版本和压缩包，压缩包排除 `data`。程序已在本机使用打包的 Python 启动，并创建独立诊断日志。正式发行前，仍需在无 Python 的干净机器和项目所有者的集群上验证，包括 Qt WebEngine 终端、传输、站点资料持久化与更新。见 [PORTABLE.md](PORTABLE.md) 和 [TODO.md](TODO.md)。

The Terminal tab lists jump sites and discovered work-node aliases even without Files-tab worker SFTP connections. It can open several SSH PTY channels per node. Each successful connection batch creates a Terminal Group with a run-local stable ID; single-terminal creation makes its own group. `terminal_workspace.py` displays the active group and member checkboxes, `terminal_session.py` owns channels, and `remote_terminal_widget.py` hosts a separate local xterm.js frontend for each session. A broadcast request carries the group ID, command, and selected sessions; `MainWindow` validates membership before its `pwd -P` probes and again before sending. No mode checkbox is needed; switching groups clears the draft. Common destructive commands trigger another confirmation. Closing an individual terminal tab hides its view; Disconnect terminal closes the channel. Disconnecting worker SFTP leaves terminals open; disconnecting the jump closes dependent terminals. Group isolation and full-screen applications still need verification on the owner's cluster. Broadcast reports delivery, not per-command exit status.

“终端”标签页显示跳板站点与已发现的工作节点别名，不依赖文件页工作节点 SFTP 连接。每节点可打开多个 SSH PTY。每批成功连接都创建一个具有本次运行内稳定 ID 的 Terminal Group；单独新建终端则形成自己的组。`terminal_workspace.py` 显示当前组和成员勾选列表，`terminal_session.py` 持有通道，`remote_terminal_widget.py` 为每个会话承载独立的本地 xterm.js 前端。广播请求带组 ID、命令和已选会话；`MainWindow` 在 `pwd -P` 探针前及发送前都验证成员关系。无需勾选模式；切换组会清空草稿。常见破坏性命令另需确认。关闭单个终端标签只隐藏视图，“断开终端”关闭通道。断开工作节点 SFTP 不影响终端；断开跳板会关闭依赖的终端。分组隔离及全屏程序仍需在项目所有者集群上实测。广播只报告发送状态，不跟踪逐命令退出码。

Each completed multi-terminal connection batch selects its group's Combined tab automatically; a single-terminal group shows its individual terminal. Selecting another group follows the same rule. A multi-terminal group's Combined tab cannot be hidden while the group exists. The group-row action is “Disconnect current group” with confirmation and leaves other groups intact. Work-node PTYs use a conservative budget of four channels per jump connection. Extra authenticated jump connections are terminal-only and consume no SFTP session slot; a code-2 session-open refusal triggers a bounded retry on a fresh connection. They are released after their last terminal closes. The owner observed `ChannelException(2, 'Connect failed')` on cft15–21; the retry path has local mock coverage, but multi-group capacity still needs live verification.

每批多终端连接完成后会自动切到该组的联合视图；只有一个终端的组显示其独立终端。切换到其他组也遵循此规则。多终端组存在期间不能隐藏联合标签。组栏操作改为“断开当前组”，经确认后保留其他组。工作节点 PTY 按每条跳板连接最多四条通道的保守额度分配。额外的已认证跳板连接专供终端使用，不占 SFTP 会话槽位；遇到代码 2 的会话打开拒绝时，会换新连接有限次重试。最后一个终端关闭后释放对应连接。项目所有者在 cft15–21 上观察到 `ChannelException(2, 'Connect failed')`；本地模拟测试已覆盖重试路径，多组容量仍需集群实测。

The broadcast controls sit below the terminal area. Group names can be renamed while their abbreviated node suffix stays automatic; terminal tabs begin with the group name. The read-only Combined xterm.js view starts a round only when a broadcast is approved for sending. It collects each recipient's raw PTY reply from its current prompt, concatenating replies by node without adding headers. A recognizable returning prompt freezes a node's reply. It does not deduplicate identical results or provide reliable exit codes. A recognized Shell prompt lets the cwd probe hide its echoed `printf` and returning prompt. Verify this against the owner's server with two harmless `ls` broadcasts; local tests cover split probe output, round order, and ANSI preservation.

广播控件位于终端区下方。组名可修改，缩写的节点后缀自动生成；终端标签以组名开头。只读的 xterm.js“联合显示”仅在广播获准发送后新建一轮，从各接收终端当前提示符处收集原始 PTY 反馈，按节点顺序直接拼接，不添加标题。识别到 Shell 提示符返回时固定该节点反馈。它尚不对相同结果去重，也不提供可靠退出码。识别到 Shell 提示符时，目录探针会隐藏回显的 `printf` 和返回提示符。请在项目所有者的服务器上用两次无害的 `ls` 广播实测；本地测试已覆盖探针分段输出、轮次顺序和 ANSI 保留。

xterm.js sends Tab and other terminal keys to the remote Shell through WebChannel; BEL from the Shell sounds locally. Broadcasting restores focus to the command field after successful delivery. The eight helper buttons replace the last focused broadcast or single-terminal draft. In a terminal they send Ctrl+E, Ctrl+U, then the template, without Enter; validate the plain Shell prompt before doing so. The Combined view leaves a blank line after a complete broadcast round. Confirm Tab completion, helper behavior, and an ambiguous-match bell on the owner's Shell during live testing.

xterm.js 经 WebChannel 把 Tab 等终端按键发给远程 Shell；Shell 的 BEL 可在本地响铃。广播成功发送后焦点返回命令框。八个助手按钮替换最近获得焦点的广播草稿或单终端草稿。在单终端中先发送 Ctrl+E、Ctrl+U，再发送模板，不附带回车；发送前需识别普通 Shell 提示符。联合视图在完整一轮广播后留一个空行。请在项目所有者的 Shell 上实测 Tab 补全、助手行为及多匹配提示音。

xterm.js renders the blinking cursor and rings the system bell on remote BEL. It owns scrollback (10,000 lines), selection, and terminal keyboard sequences. Ctrl+Plus/Minus changes its font size and sends a PTY resize; Ctrl+0 resets the font. The earlier Python-side local left-boundary bell was removed with the old renderer; test whether the remote Shell supplies BEL at that boundary.

xterm.js 绘制闪烁光标，并在收到远端 BEL 时播放系统提示音。终端历史（最多一万行）、选择和键盘序列由它管理。Ctrl+加号／减号会调整字号并向 PTY 发送尺寸变化；Ctrl+0 恢复默认字号。旧渲染器中的 Python 本地左边界响铃已移除；需实测远端 Shell 在此处是否发送 BEL。

First verify the work-node browser and file operations against the owner's jump host: inspect discovered aliases, connect a few nodes, browse a common absolute path, then use disposable directories to test drag-copy from local and jump to one or several work nodes, suffix-based collection from work nodes to jump or the local pane, same-name conflict handling, and confirmed Delete on one or several work nodes. Confirm that the source-scope prompt appears only after dropping onto the destination. Check both empty-area and folder-row drops. Work-node-to-Explorer dragging remains a TODO. Try enough nodes to exercise extra jump connections. An alias from SSH config or `/etc/hosts` is only a candidate; verify actual connection and host-key behavior. Exposing move in the drag workflow remains a TODO. Multi-node results are per node, with no rollback after partial completion. Never use valuable files for destructive checks.

首先在项目所有者的跳板节点上实测工作节点浏览器和文件操作：检查发现的别名、连接少量节点、浏览共同绝对路径，再用可丢弃目录测试从本地和跳板拖到单个或多个工作节点、按节点名后缀从工作节点拖回跳板或本地面板、同名冲突，以及对单个或多个工作节点经确认的 Delete 删除。确认来源范围弹窗只在投放到目标后出现。分别测试投放到空白处和文件夹行。工作节点拖到资源管理器仍属待办。还需连接足够多的节点以验证额外跳板连接。SSH 配置或 `/etc/hosts` 中的别名只是候选，仍须核验实际连接与主机密钥行为。在拖动流程中提供移动仍属待办。多节点结果按节点报告，部分完成后不会回滚。破坏性检查不得使用重要文件。

## GitHub state / GitHub 状态

The owner created and pushed the Git repository and chose `GPL-3.0-only`; `LICENSE` is tracked in commit `359c548`. The owner reports that the repository is public. `.gitignore` excludes the local Conda environment, Python caches, `sites.json`, and common credential-file formats. Before each future commit, inspect the staged file list and diff for credentials and private host details. Do not create a commit or push on behalf of the owner without their request.

项目所有者已创建并推送 Git 仓库，选择了 `GPL-3.0-only`；`LICENSE` 已由提交 `359c548` 跟踪。据项目所有者说明，仓库已公开。`.gitignore` 排除了本地 Conda 环境、Python 缓存、`sites.json` 和常见凭据文件格式。以后每次提交前都应检查暂存文件清单与差异，确认没有凭据或内部主机细节。未经所有者要求，不代为创建提交或推送。
