# Handoff / 接手说明

This file helps the next contributor resume work without relying on chat history. Read [AGENTS.md](AGENTS.md) for working rules, [CURRENT_STATUS.md](CURRENT_STATUS.md) for the verified state, [DECISIONS.md](DECISIONS.md) for constraints, and [ARCHITECTURE.md](ARCHITECTURE.md) for code boundaries. [README.md](README.md) is the GitHub overview; [USER_GUIDE.md](USER_GUIDE.md) has usage details and [TODO.md](TODO.md) tracks planned work.

本文帮助下一位开发者在不依赖聊天记录的情况下接续工作。工作规则见 [AGENTS.md](AGENTS.md)，已验证状态见 [CURRENT_STATUS.md](CURRENT_STATUS.md)，约束与选择见 [DECISIONS.md](DECISIONS.md)，代码边界见 [ARCHITECTURE.md](ARCHITECTURE.md)。[README.md](README.md) 是 GitHub 首页简介；使用细节见 [USER_GUIDE.md](USER_GUIDE.md)，后续工作见 [TODO.md](TODO.md)。

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

Windows packaging is a release requirement: the distributable must be self-contained and run on a target machine without Python preinstalled. No package or clean-machine verification exists yet; see [TODO.md](TODO.md).

Windows 打包是发行要求：发行包必须自包含，目标机器无需预装 Python。当前尚无打包产物或干净机器验证；见 [TODO.md](TODO.md)。

The Terminal tab lists jump sites and discovered work-node aliases even without Files-tab worker SFTP connections. It can open several SSH PTY channels per node. Each successful connection batch creates a Terminal Group with a run-local stable ID; single-terminal creation makes its own group. `terminal_workspace.py` displays the active group and member checkboxes, while `terminal_session.py` owns channels. A broadcast request carries the group ID, command, and selected sessions; `MainWindow` validates membership before its `pwd -P` probes and again before sending. No mode checkbox is needed; switching groups clears the draft. Common destructive commands trigger another confirmation. Closing an individual terminal tab hides its view; Disconnect terminal closes the channel. Disconnecting worker SFTP leaves terminals open; disconnecting the jump closes dependent terminals. Next, verify group creation and isolation against the owner's cluster, then implement full-screen terminal rendering. Broadcast reports delivery, not per-command exit status.

“终端”标签页显示跳板站点与已发现的工作节点别名，不依赖文件页工作节点 SFTP 连接。每节点可打开多个 SSH PTY。每批成功连接都创建一个具有本次运行内稳定 ID 的 Terminal Group；单独新建终端则形成自己的组。`terminal_workspace.py` 显示当前组和成员勾选列表，`terminal_session.py` 持有通道。广播请求带组 ID、命令和已选会话；`MainWindow` 在 `pwd -P` 探针前及发送前都验证成员关系。无需勾选模式；切换组会清空草稿。常见破坏性命令另需确认。关闭单个终端标签只隐藏视图，“断开终端”关闭通道。断开工作节点 SFTP 不影响终端；断开跳板会关闭依赖的终端。下一步在项目所有者集群上实测分组建立和隔离，再实现全屏终端渲染。广播只报告发送状态，不跟踪逐命令退出码。

Each completed terminal connection batch now selects its group's Combined tab automatically. Selecting another group shows its Combined tab; the tab cannot be hidden while the group exists. The group-row action is “Disconnect current group” with confirmation and leaves other groups intact. Work-node PTYs use a conservative budget of four channels per jump connection. Extra authenticated jump connections are terminal-only and consume no SFTP session slot; a code-2 session-open refusal triggers a bounded retry on a fresh connection. They are released after their last terminal closes. The owner observed `ChannelException(2, 'Connect failed')` on cft15–21; the retry path has local mock coverage, but multi-group capacity still needs live verification.

每批终端连接完成后会自动切到该组的联合视图；选择其他组也会显示其联合视图。组存在期间不能隐藏该标签。组栏操作改为“断开当前组”，经确认后保留其他组。工作节点 PTY 按每条跳板连接最多四条通道的保守额度分配。额外的已认证跳板连接专供终端使用，不占 SFTP 会话槽位；遇到代码 2 的会话打开拒绝时，会换新连接有限次重试。最后一个终端关闭后释放对应连接。项目所有者在 cft15–21 上观察到 `ChannelException(2, 'Connect failed')`；本地模拟测试已覆盖重试路径，多组容量仍需集群实测。

The broadcast controls sit below the terminal area. Group names can be renamed while their abbreviated node suffix stays automatic; terminal tabs begin with the group name. The read-only Combined view starts a round only when a broadcast is approved for sending. It copies each recipient's formatted terminal text from its current prompt, concatenating replies by node without adding headers. A recognizable returning prompt freezes a node's reply before that next prompt. It does not deduplicate identical results or provide reliable exit codes. A recognized Shell prompt lets the cwd probe hide its echoed `printf` and returning prompt. Verify this against the owner's server with two harmless `ls` broadcasts; local tests cover split probe output, round order, and retained text colors.

广播控件位于终端区下方。组名可修改，缩写的节点后缀自动生成；终端标签以组名开头。只读“联合显示”仅在广播获准发送后新建一轮，从各接收终端当前提示符处复制带格式的文本，按节点顺序直接拼接，不添加标题。识别到 Shell 提示符返回时，在下一提示符之前固定该节点反馈。它尚不对相同结果去重，也不提供可靠退出码。识别到 Shell 提示符时，目录探针会隐藏回显的 `printf` 和返回提示符。请在项目所有者的服务器上用两次无害的 `ls` 广播实测；本地测试已覆盖探针分段输出、轮次顺序和字色保留。

`TerminalOutput` must intercept Qt Tab focus traversal so the remote Shell receives Tab completion; BEL from the Shell is already rendered as a local sound. Broadcasting restores focus to the command field after successful delivery. The eight helper buttons replace the last focused broadcast or single-terminal draft. In a terminal they send Ctrl+E, Ctrl+U, then the template, without Enter; validate the plain Shell prompt before doing so. The Combined view leaves a blank line after a complete broadcast round. Confirm Tab completion, helper behavior, and an ambiguous-match bell on the owner's Shell during live testing.

`TerminalOutput` 必须拦截 Qt 的 Tab 焦点切换，让远程 Shell 收到 Tab 补全；Shell 的 BEL 已可在本地响铃。广播成功发送后焦点返回命令框。八个助手按钮替换最近获得焦点的广播草稿或单终端草稿。在单终端中先发送 Ctrl+E、Ctrl+U，再发送模板，不附带回车；发送前需识别普通 Shell 提示符。联合视图在完整一轮广播后留一个空行。请在项目所有者的 Shell 上实测 Tab 补全、助手行为及多匹配提示音。

The terminal output widget overlays a blinking caret at its tracked PTY position and rings the system bell on BEL. A Left key at the start of a recognized plain Bash prompt rings locally if needed; a following remote BEL is suppressed to avoid a double sound. Scrollback preserves a manual scroll position, keeps at most 10,000 lines, and supports Shift+PageUp/PageDown. Ctrl+Plus/Minus zooms and sends a PTY resize; Ctrl+0 resets the font.

终端输出控件会在跟踪的 PTY 位置叠加闪烁光标，并在收到 BEL 时播放系统提示音。对能识别的普通 Bash 提示符，在输入左边界按左方向键会先在本地响铃；随后若收到远端 BEL，则避免重复响铃。手动查看历史输出时保留滚动位置，最多保留一万行，并支持 Shift+PageUp/PageDown。Ctrl+加号／减号会调整字号并向 PTY 发送尺寸变化；Ctrl+0 恢复默认字号。

First verify the work-node browser and file operations against the owner's jump host: inspect discovered aliases, connect a few nodes, browse a common absolute path, then use disposable directories to test drag-copy from local and jump to one or several work nodes, suffix-based collection from work nodes to jump or the local pane, same-name conflict handling, and confirmed Delete on one or several work nodes. Confirm that the source-scope prompt appears only after dropping onto the destination. Check both empty-area and folder-row drops. Work-node-to-Explorer dragging remains a TODO. Try enough nodes to exercise extra jump connections. An alias from SSH config or `/etc/hosts` is only a candidate; verify actual connection and host-key behavior. Exposing move in the drag workflow remains a TODO. Multi-node results are per node, with no rollback after partial completion. Never use valuable files for destructive checks.

首先在项目所有者的跳板节点上实测工作节点浏览器和文件操作：检查发现的别名、连接少量节点、浏览共同绝对路径，再用可丢弃目录测试从本地和跳板拖到单个或多个工作节点、按节点名后缀从工作节点拖回跳板或本地面板、同名冲突，以及对单个或多个工作节点经确认的 Delete 删除。确认来源范围弹窗只在投放到目标后出现。分别测试投放到空白处和文件夹行。工作节点拖到资源管理器仍属待办。还需连接足够多的节点以验证额外跳板连接。SSH 配置或 `/etc/hosts` 中的别名只是候选，仍须核验实际连接与主机密钥行为。在拖动流程中提供移动仍属待办。多节点结果按节点报告，部分完成后不会回滚。破坏性检查不得使用重要文件。

## GitHub state / GitHub 状态

The owner created and pushed the Git repository and chose `GPL-3.0-only`; `LICENSE` is tracked in commit `359c548`. The owner reports that the repository is public. `.gitignore` excludes the local Conda environment, Python caches, `sites.json`, and common credential-file formats. Before each future commit, inspect the staged file list and diff for credentials and private host details. Do not create a commit or push on behalf of the owner without their request.

项目所有者已创建并推送 Git 仓库，选择了 `GPL-3.0-only`；`LICENSE` 已由提交 `359c548` 跟踪。据项目所有者说明，仓库已公开。`.gitignore` 排除了本地 Conda 环境、Python 缓存、`sites.json` 和常见凭据文件格式。以后每次提交前都应检查暂存文件清单与差异，确认没有凭据或内部主机细节。未经所有者要求，不代为创建提交或推送。
