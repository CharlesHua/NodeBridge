# Persistent terminals / 持久终端

Persistent mode is optional and requires tmux 3.1 or newer on each target node. It does not install software, change SSH verification, or replace xterm.js. New ordinary terminals keep the existing SSH PTY path. A terminal already opened in ordinary mode cannot be converted retrospectively into a persistent session.

持久模式可选，每个目标节点须已安装 tmux 3.1 或更新版本。它不会安装软件、改变 SSH 校验或替换 xterm.js；普通新终端继续使用既有 SSH PTY。已经打开的普通终端不能事后转换为持久会话。

## Remote ownership and isolation / 远程持有与隔离

`tmux_session.py` manages a separate per-user tmux server with `tmux -L nodebridge -f /dev/null`. The socket label is `nodebridge`; sessions are named `nodebridge-<UUID>`. Existing default-socket sessions and `~/.tmux.conf` are not modified. New sessions are created detached, with `destroy-unattached off` set only for that session. Group identifiers and initial group names are stored as session options. Queries and attachment follow the same strict passwordless OpenSSH alias routes as the rest of the application.

`tmux_session.py` 用 `tmux -L nodebridge -f /dev/null` 管理独立的当前用户 tmux 服务。socket 标签为 `nodebridge`，会话名为 `nodebridge-<UUID>`。不修改默认 socket 上的既有会话及 `~/.tmux.conf`。新会话先以分离方式创建，仅对该会话设置 `destroy-unattached off`。组标识及初始组名保存在会话选项中；查询和连接沿用程序已有的严格免密 OpenSSH 别名中转路径。

Closing an SSH control client detaches it; it never sends `exit`, Ctrl+C, or `kill-session`. Shell state, activated Conda environments, and jobs remain owned by the remote tmux server while that server survives. Host reboot, explicit session termination, shell exit, or administrator cleanup can still end them. No command is automatically rerun on recovery. A missing session causes an error instead of being recreated.

关闭 SSH 控制客户端仅分离，不发送 `exit`、Ctrl+C 或 `kill-session`。只要远程 tmux 服务仍在，Shell 状态、已激活的 Conda 环境及任务由它持有。主机重启、明确结束会话、Shell 退出或管理员清理仍可能终止它们。恢复不会自动重新运行命令；会话不存在时报告错误，不重新创建。

Terminal-page disconnect actions (single terminal, group, or checked nodes) ask whether to permanently terminate sessions or detach connections; Cancel is the default. Permanent termination uses background `kill-session` requests and retains failed members for retry. The tab close button only hides the view and preserves the channel and local renderer/history. Detaching from either workspace releases the channel, and restoring creates a new local terminal with a bounded remote screen/history snapshot. Site disconnect and application exit continue to detach only.

终端页断开单终端、当前组或勾选节点时，询问永久结束会话或分离连接，默认取消。永久结束在后台执行 `kill-session`，失败成员保留供重试。标签上的关闭按钮仍只隐藏视图，保留通道、本地渲染器及历史。终端页或任务页的分离均释放本地通道，恢复时新建本地终端并读取有限远程屏幕／历史快照。断开站点及退出软件仍仅分离。

## Terminal data path / 终端数据通路

The backend attaches with tmux control mode (`-C`), not a rendered tmux terminal. `TmuxChannel` implements the channel methods used by `TerminalManager`. It buffers fragmented protocol lines, consumes command-response blocks, and decodes `%output` octal escapes into the original pane bytes. xterm.js continues to handle ANSI colors, cursor movement, progress updates, and application output. Keyboard and paste bytes become hexadecimal `send-keys -H` arguments, preventing user text from being interpreted as tmux command syntax. Resize requests use `refresh-client -C`. See the [official control-mode documentation](https://github.com/tmux/tmux/wiki/Control-Mode).

后端使用 tmux 控制模式（`-C`），不接收 tmux 二次绘制的终端画面。`TmuxChannel` 实现 `TerminalManager` 使用的通道方法，缓存分段协议行、消费命令响应块，并将 `%output` 八进制转义解码为原始窗格字节。ANSI 颜色、光标移动、进度刷新及应用输出仍由 xterm.js 处理。键盘和粘贴字节转为十六进制 `send-keys -H` 参数，避免用户文本被当成 tmux 命令语法。尺寸请求使用 `refresh-client -C`。原理见 [tmux 官方控制模式文档](https://github.com/tmux/tmux/wiki/Control-Mode)。

Attachment restores the current pane and up to 2,000 available history lines with colors and cursor position. Initial screen restoration is separated from the current-prompt text used by broadcast checks; subsequent live bytes use the normal terminal pipeline. The existing `pwd -P` OSC marker can therefore pass through without enabling tmux passthrough. Ordinary group checks, Conda-prefix checks, combined output, and confirmed Ctrl+C broadcast remain in NodeBridge. One NodeBridge persistent terminal targets one pane; tmux split/window management is not exposed in the GUI. Full-screen applications resumed mid-session still need broader live validation of terminal modes and resizing.

连接会恢复当前窗格及最多 2,000 行可用历史，包括颜色和光标位置。初始画面恢复与广播检查使用的当前提示符文本分开，后续实时字节仍走普通终端通路，因此既有 `pwd -P` 的 OSC 标记不需要启用 tmux passthrough 即可到达。组范围、Conda 前缀检查、联合输出及经确认的 Ctrl+C 广播仍由 NodeBridge 管理。一个持久终端对应一个窗格，GUI 不提供 tmux 分屏／多窗口管理。运行中全屏应用的恢复仍需进一步实测终端模式和尺寸行为。

## Local inventory and recovery / 本地记录与恢复

Successful permanent termination from either workspace automatically removes the corresponding persisted local records. A successful inventory query also removes records for absent remote sessions and closes any stale local attachment. Failed terminations/queries and detachment retain records. There is no separate removal button. Cleanup is saved immediately, and an older scan cannot reinsert the removed history.

在任一工作区成功永久结束会话后，自动删除对应持久保存的本地记录。清单查询成功确认远端不存在时，也删除该记录并关闭残留本地连接。结束／查询失败及分离连接时保留记录；不提供单独移除按钮。清理立即保存，旧扫描不能重新插入已移除历史。

The table supports Ctrl/Shift multi-selection. Restore processes selected sessions in sequence while retaining saved group membership; detach closes each selected local channel; termination uses one confirmation and reports per-session failures. Table refresh retains the selected keys and vertical scroll position.

任务表支持 Ctrl／Shift 多选。恢复依次处理所选会话并保留原组关联；分离关闭所选本地通道；永久结束统一确认并报告各会话失败。表格刷新保留选中会话及纵向滚动位置。

`tasks_workspace.py` displays the Tasks tab; MainWindow coordinates operations outside the widgets. Nonsecret records are saved in `QSettings` under `terminal/tmux_tasks` (portable builds: `data/settings.ini`). Identity includes direct address, port, SSH user, alias route, and tmux name; it is independent of run-local terminal IDs. Records never contain passwords. Reopened records start unverified, rather than claiming that remote jobs still run. Restoring members of the same saved group relinks them within the current run; ordinary groups remain run-local. Renames persist locally; losing local metadata may recover the initial group label from the remote session instead.

`tasks_workspace.py` 展示“任务”页，MainWindow 在控件之外协调操作。非秘密记录保存到 `QSettings` 的 `terminal/tmux_tasks`（绿色版为 `data/settings.ini`）；身份包括直连地址、端口、SSH 用户、别名路径及 tmux 名称，不依赖本次运行的终端 ID。记录不含密码。重启后先显示“待检查”，不声称远程任务仍在运行。同一保存组的成员恢复时会在当前运行中重新关联；普通组仍只在本次运行内存在。重命名保存在本地，本地元数据丢失后可能从远程恢复初始组名。

Entering Tasks or pressing Scan queries the direct node, known reachable/connected indirect nodes, and routes from saved records. A dedicated background slot and two concurrent queries use a separate SSH helper. Cancellation closes queries and shutdown waits for the worker before destroying its Qt objects. The scan can discover sessions after local IDs are lost. Failure to query a node is distinct from a successful scan finding no session. The task list reports session availability and the current program name, not reliable job completion or exit status. It is not yet the proposed computation-task workspace with inputs, outputs, and file orchestration.

进入“任务”页或点击扫描时，查询直连节点、已知可连接／已连接的间接节点，以及保存记录中的路径。查询使用独立后台任务位、额外 SSH 辅助连接及两个并发请求。取消时关闭查询；退出会等待线程结束再释放 Qt 对象。本地 ID 丢失后仍能扫描发现会话。“查询失败”与成功扫描后发现“远端不存在”分开。“任务”页报告会话可用性和当前程序名，不提供可靠的计算完成／退出状态；它尚不是包含输入、结果及文件编排的完整计算任务工作区。

Inventory scans run only when entering Tasks, using “Recover persistent sessions…”, or pressing Scan; there is no periodic scan or automatic startup scan. `_tmux_scan_job` owns a separate SSH helper and at most two queries, independently of `_tmux_job` mutations and foreground jobs. Only a duplicate scan is disabled; existing terminal input, broadcasts, browsing, transfers and session actions retain their ordinary availability. Scan failures are nonmodal, recorded in logs and the Tasks summary. Disconnect cancels the scan without waiting for it; shutdown cancels and waits asynchronously for Qt worker cleanup. Results from an old site/cancelled scan are discarded, and record snapshots protect changes made during scanning. Restoring a session also validates that specific remote session before attachment.

清单扫描仅在进入任务页、点击“恢复持久会话…”或扫描按钮时触发，没有定时扫描或启动自动扫描。`_tmux_scan_job` 持有独立 SSH 辅助连接，最多两个查询，与 `_tmux_job` 修改操作及前台任务独立。扫描仅禁用重复扫描，已有终端输入、广播、浏览、传输和会话操作保持原有可用性。失败不弹模态框，写入日志及任务摘要。断开站点取消扫描而不等待；退出异步等待 Qt 工作线程清理。取消／旧站点结果被丢弃，记录快照保护扫描期间发生的状态变化。恢复时还会在连接前单独确认对应远程会话仍存在。

## Manual administration / 手工管理

Run these commands as the same SSH user on the actual execution node. Termination is destructive and should be used only after identifying the session; it may end processes in that terminal.

在实际执行任务的节点上，以相同 SSH 用户执行。结束会话具有破坏性，应先确认名称；它可能结束该终端内的进程。

```bash
tmux -L nodebridge list-sessions
tmux -L nodebridge attach-session -t '=nodebridge-REPLACE_WITH_NAME'
tmux -L nodebridge kill-session -t '=nodebridge-REPLACE_WITH_NAME'
```

## Verification / 验证

Focused Windows tests cover group selection, persistence metadata, recovery without recreation, missing-tmux errors, and confirmed termination. Opt-in Linux integration runs on a unique test socket and removes only that test server. With WSL Ubuntu tmux 3.2a, it verified detach while a command runs, continued execution while disconnected, reattachment to the same Shell/directory, ANSI colors, OSC directory markers, Ctrl+C, and alternate-screen byte delivery. This does not replace real multi-node SSH and packaged Windows acceptance.

Windows 定向测试覆盖组选择、元数据保存、不重新创建的恢复、缺少 tmux 的错误及经确认的结束。Linux 实测须显式启用，使用唯一测试 socket，仅清理该测试服务。已在 WSL Ubuntu tmux 3.2a 验证命令运行中分离、断开期间继续执行、恢复原 Shell／目录、ANSI 颜色、OSC 目录标记、Ctrl+C 和备用屏幕字节传递；这不能代替真实多节点 SSH 及 Windows 打包验收。

```bash
NODEBRIDGE_TMUX_LIVE=1 python3 -m unittest tests.test_tmux_session.LiveTmuxTests
```
