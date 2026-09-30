# Current status / 当前状态

This is the implementation snapshot audited on 2026-09-30. [README.md](README.md) explains usage, [ARCHITECTURE.md](ARCHITECTURE.md) explains the code, and [DECISIONS.md](DECISIONS.md) records settled choices.

这里是截至 2026-09-30 核对过的实现快照。[README.md](README.md) 说明使用方法，[ARCHITECTURE.md](ARCHITECTURE.md) 说明代码结构，[DECISIONS.md](DECISIONS.md) 记录已确定的选择。

## Implemented / 已实现

Windows-first PySide6 GUI with local and remote tree/list panes; saved SFTP site profiles; direct, saved-site tunnel, and passwordless SSH-alias connections; sequential return to prior sites; directory browsing; distinct file icons; compact file lists; persistent local-pane visibility; drag-and-drop and clipboard copy between supported endpoints; same-name file decisions; inline rename; directory creation; confirmed local and remote deletion; and in-window completion messages.

面向 Windows 的 PySide6 界面，包含本地与远程的目录树和文件列表；已保存的 SFTP 站点资料；直连、经已保存站点隧道连接和免密 SSH 别名中转；逐级返回前级站点；目录浏览；文件类型图标；紧凑文件列表；本地面板显示设置持久化；受支持端点之间的拖放与剪贴板复制；同名文件处理选择；行内重命名；新建目录；经确认的本地及远程删除；以及窗口内的完成提示。

## Verification / 验证

The project-local Conda environment runs Python 3.12.11. The latest local automated run completed 51 tests: 50 passed and one symlink-creation test was skipped because this Windows test environment cannot create the link. The owner previously reported a successful direct SSH connection. Live jump, transfer, rename, and deletion behavior has not yet been verified against the owner's servers. The directory has not been initialized as a Git repository or connected to GitHub.

项目内 Conda 环境使用 Python 3.12.11。最近一次本地自动化运行共 51 项测试：50 项通过，另有 1 项因该 Windows 测试环境无法创建符号链接而跳过。项目所有者此前反馈 SSH 直连成功。中转、传输、重命名和删除尚未在所有者的服务器上实测。当前目录尚未初始化为 Git 仓库，也未连接 GitHub。

## Not implemented or not fully controlled / 未实现或无法完全控制

Move, transfer queue, concurrent jobs, retry, independent simultaneous remote views, packaged installer, and OS-protected password storage remain planned. Direct remote-to-Explorer drops are completed by Windows, which controls destination conflict prompts and completion. Cross-application drag-and-drop and non-Windows platforms still need live verification. Multi-file operations are not transactional and may leave completed items after a later error.

移动、传输队列、并发任务、重试、独立的并列远程视图、安装包和系统凭据库存储仍待实现。从远程直接拖入资源管理器的复制由 Windows 完成，因此目标冲突提示和完成状态由 Windows 控制。跨应用拖放和非 Windows 平台仍需实测。多文件操作不具备事务性，后续项目出错时已完成的项目可能保留。
