# Connection recovery / 连接恢复

When a site or terminal is still connected, closing the window offers Exit, Minimize to background, or Cancel. Minimize keeps the window in the Windows taskbar and preserves connections and background work. Detached, recoverable tmux records alone do not trigger this prompt. Exit follows the existing asynchronous shutdown and waits for active file work; it detaches tmux rather than destroying remote sessions.

仍有站点或终端连接时，关闭窗口会询问“关闭软件”“最小化到后台”或“取消”。最小化后窗口保留在 Windows 任务栏，连接与后台工作继续运行。只有已分离、可恢复的 tmux 记录时不提示。真正退出沿用现有异步关闭流程，等待文件任务完成；tmux 仅断开本地连接，不销毁远程会话。

On startup, a saved active-connection snapshot takes precedence over site auto-connect. Accepting restores the root site, file-worker connections, nested site connections and terminals, using existing background connection routines. Ordinary terminals create new shells and retain their group association; attached tmux terminals use existing task IDs to restore the original remote sessions. No commands are replayed, and ordinary terminal output, working directory and running processes are not recovered. Declining skips auto-connect for that launch.

启动时，上次活动连接记录优先于站点自动连接。确认后，通过现有后台连接流程依次恢复直连站点、文件页间接节点、嵌套站点和终端。普通终端新建 Shell 并保留分组关系；当时仍连接的 tmux 终端通过已有任务标识接回原会话。不重放命令，也不恢复普通终端的输出、工作目录和运行进程。选择不恢复时，本次启动不再执行站点自动连接。

`nodebridge/recovery.py` stores a versioned JSON snapshot at `recovery/connections_v1` in the existing application settings. It contains connection configuration, worker aliases, group labels and attached tmux record keys, never passwords, shell input or output. Existing site password storage is reused; a missing password or locked store prompts the user. Credentials and host-key checks use the existing connection path.

`nodebridge/recovery.py` 在现有应用设置的 `recovery/connections_v1` 中保存带版本号的 JSON 快照，包含连接配置、节点别名、组名和当时连接的 tmux 记录标识；不包含密码及 Shell 输入输出。恢复时复用站点密码存储，密码缺失或存储加锁时询问用户；认证与主机密钥校验沿用原有连接流程。

Connection control updates and terminal closures checkpoint immediately; a one-second timer also captures changes such as renaming groups. Unchanged snapshots are not rewritten. Shutdown freezes the snapshot before disconnecting, so cleanup cannot erase the recovery list. Forced termination can recover the last successfully saved snapshot, but a change interrupted before persistence can be lost. This is connection recovery, not recovery of interrupted transfers or preservation of ordinary SSH processes. Real Windows force-exit/relaunch and live SSH acceptance remain necessary.

连接控制状态更新及终端关闭时立即保存，另有一秒定时检查捕捉组名等变化；内容未变化时不重复写入。退出时在断开前冻结快照，避免清理过程清空恢复列表。强制结束后可恢复最后成功保存的记录，但写入前被打断的变化可能丢失。这是连接恢复，不恢复中断的文件传输，也不保证普通 SSH 进程继续运行。仍需真实 Windows 强制退出／重启及远程 SSH 验收。
