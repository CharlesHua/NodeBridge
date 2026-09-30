# Architecture / 架构

This document describes the current implementation. The longer-term direction is in [README.md](README.md); implemented and planned behavior are separated in [CURRENT_STATUS.md](CURRENT_STATUS.md).

本文描述当前实现。长期方向见 [README.md](README.md)；已实现与计划中的功能在 [CURRENT_STATUS.md](CURRENT_STATUS.md) 中分开列出。

## Components / 组件

`nodebridge/__main__.py` starts `app.py`, which creates the Qt application and `MainWindow` in `window.py`. The main window owns the active remote session, a stack of previous jump sessions, the two file panes, and the current background job. `local_browser.py` provides the local directory tree and file list; `drag_drop.py` translates Qt drag data into local paths or a NodeBridge remote-session token and paths.

`nodebridge/__main__.py` 启动 `app.py`，由它创建 Qt 应用及 `window.py` 中的 `MainWindow`。主窗口持有当前远程会话、前级跳板会话栈、两个文件面板及当前后台任务。`local_browser.py` 提供本地目录树和文件列表；`drag_drop.py` 将 Qt 拖动数据转换为本地路径，或 NodeBridge 的远程会话标识和路径。

`remote.py` owns Paramiko SSH/SFTP connections and directory listings. `sites.py` stores named site profiles, and `site_dialog.py` edits them. `transfer.py` implements local-to-remote, remote-to-local, local-to-local, and within-session remote-to-remote copy operations. `file_operations.py` implements rename, directory creation, and deletion. `conflict_dialog.py` asks the user how to handle an existing file; `file_icons.py` provides file-type icons.

`remote.py` 管理 Paramiko SSH/SFTP 连接和目录列表。`sites.py` 保存具名站点资料，`site_dialog.py` 用于编辑资料。`transfer.py` 实现本地到远程、远程到本地、本地到本地，以及同一远程会话内的复制。`file_operations.py` 实现重命名、新建目录和删除。`conflict_dialog.py` 询问同名文件的处理方式；`file_icons.py` 提供文件类型图标。

## Connections and jobs / 连接与任务

Direct connections use Paramiko. A saved-site jump opens an SSH `direct-tcpip` channel through the current site and then creates another Paramiko connection. An SSH-alias jump runs the current site's OpenSSH client and opens its target SFTP subsystem. The previous session remains on a stack so disconnecting the target returns to it. The current GUI shows one active remote site at a time; it does not yet provide independent simultaneous site views.

直连使用 Paramiko。经已保存站点中转时，程序通过当前站点打开 SSH `direct-tcpip` 通道，再建立另一个 Paramiko 连接。SSH 别名中转则运行当前站点上的 OpenSSH 客户端，并打开目标的 SFTP 子系统。前级会话留在栈中，断开目标后可返回。当前界面一次只显示一个活动远程站点，尚无独立的多站点并列视图。

`MainWindow._run` starts one `QThread` job for blocking SSH/SFTP and file work, and disables conflicting controls until it finishes. Progress and results return to the GUI through Qt signals. On a file conflict, the worker requests a GUI dialog and waits for the user's Overwrite, Skip, or Cancel choice. The current window has one job slot, with no queue, retry, or concurrent transfers.

`MainWindow._run` 用一个 `QThread` 任务执行阻塞式 SSH/SFTP 和文件操作，并在完成前禁用可能冲突的控件。进度与结果通过 Qt 信号返回界面。遇到同名文件时，工作线程请求界面对话框，并等待用户选择覆盖、跳过或取消。当前窗口只有一个任务位，没有队列、重试或并发传输。

## Storage and safety / 存储与数据安全

Site profiles live outside the repository in the user's `NodeBridge/sites.json` under Qt's generic configuration location (Windows on this machine: `%LOCALAPPDATA%\NodeBridge\sites.json`). Saved passwords are temporarily plaintext by explicit owner decision; moving them to the OS credential store is a TODO. The local-pane visibility setting uses `QSettings`. Remote drag export uses temporary local files that are removed when NodeBridge exits.

站点资料位于仓库之外、Qt 通用配置目录下的用户 `NodeBridge/sites.json` 中（本机 Windows 路径为 `%LOCALAPPDATA%\NodeBridge\sites.json`）。经项目所有者明确决定，保存的密码暂时是明文；迁移到系统凭据库仍是待办事项。本地面板显示设置使用 `QSettings`。远程拖出文件会使用临时本地副本，NodeBridge 退出时会清理。

Unknown or changed SSH host keys are rejected. Deletes require confirmation: remote deletion is recursive and permanent, while local deletion moves items to the Recycle Bin. Same-name regular files require a choice before replacement; replacement is staged before the original is replaced. Copying symbolic links and special files is currently rejected. If a multi-file operation fails partway through, already completed items may remain.

未知或变化的 SSH 主机密钥会被拒绝。删除需要确认：远程删除递归且永久生效，本地删除移入回收站。同名普通文件替换前需要用户选择，且先暂存新文件再替换原文件。目前不复制符号链接和特殊文件。多文件操作中途失败时，已完成的项目可能留在目标位置。
