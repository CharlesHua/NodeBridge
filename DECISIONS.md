# Decisions / 设计决定

This file records decisions that affect future work. It does not turn planned features into implemented features; see [CURRENT_STATUS.md](CURRENT_STATUS.md) for the current boundary.

本文记录会影响后续开发的决定，不把规划功能视为已经实现；当前功能边界见 [CURRENT_STATUS.md](CURRENT_STATUS.md)。

## License / 许可证

The owner chose GNU GPL version 3 only (`GPL-3.0-only`) for NodeBridge, with Copyright (C) 2026 Charles Hua. The project is public according to the owner. The full license text is in [LICENSE](LICENSE); third-party dependencies keep their own licenses.

项目所有者为 NodeBridge 选择 GNU GPL 第 3 版且仅限该版本（`GPL-3.0-only`），版权行为 Copyright (C) 2026 Charles Hua。据项目所有者说明，仓库已公开。完整许可证见 [LICENSE](LICENSE)；第三方依赖保留各自的许可证。

## Platform and stack / 平台与技术栈

Use Python 3.12 from `environment.yml`, PySide6 for the desktop UI, and Paramiko for SSH/SFTP. Verify Windows first while keeping platform-specific code replaceable where practical. Blocking network operations run outside the GUI thread.

使用 `environment.yml` 中的 Python 3.12、PySide6 桌面界面和 Paramiko SSH/SFTP。首先验证 Windows，适用时让平台相关代码可替换。阻塞式网络操作放在界面线程之外。

The Windows release must be self-contained and run without Python preinstalled on the target machine. The packaging method is still to be chosen and verified; see [TODO.md](TODO.md).

Windows 发行包必须自包含，在目标机器未预装 Python 时也能运行。具体打包方式仍需选择和验证；见 [TODO.md](TODO.md)。

## SSH identity / SSH 身份校验

Require known SSH host keys. Direct and saved-site tunnel connections use the local user's OpenSSH `known_hosts`; SSH-alias jumps use the jump site's OpenSSH configuration and `known_hosts`, require passwordless login, and enable strict host-key checking. Do not silently trust an unknown or changed key.

要求 SSH 主机密钥已获信任。直连和经已保存站点建立的隧道使用本机用户的 OpenSSH `known_hosts`；SSH 别名中转使用跳板站点的 OpenSSH 配置和 `known_hosts`，要求免密登录并启用严格主机密钥检查。不得静默信任未知或变化的密钥。

## Credentials / 凭据

The owner chose FileZilla-like site password modes in place of Windows Credential Manager: plaintext, no-save, and master-password-encrypted storage. The plaintext option remains explicit; source runs keep the file outside the repository. A future frozen portable executable uses a `data` directory beside itself. The encryption implementation uses the established cryptography library (Scrypt and Fernet); changing to master mode rewrites the current site file without plaintext passwords. Historical backups are outside this migration.

项目所有者选择类似 FileZilla 的站点密码模式，代替 Windows 凭据管理器：明文、不保存和主密码加密保存。明文模式须由用户明确选择；源码运行时文件位于仓库外。未来冻结打包的绿色版程序使用其旁边的 `data` 目录。加密实现使用成熟的 cryptography 库（Scrypt 和 Fernet）；切换主密码模式会重写当前站点文件并移除明文密码，但不处理历史备份。

## Terminal broadcast scope / 终端广播范围

Each successful terminal connection batch creates an independent Terminal Group. Broadcast is permitted only to selected live sessions in the active group, at most one per node; no separate mode checkbox is required. Switching groups clears the draft. The current group and recipients must be visible before sending. The per-command exit status and identical-output grouping are separate future work.

每批成功建立的终端连接都形成独立 Terminal Group。广播仅可发送给当前组内选中的活动会话，每节点最多一个；无需额外勾选模式。切换组会清空草稿。发送前必须清楚显示当前组及接收目标。逐命令退出状态和相同输出合并显示属于后续工作。

## File operations / 文件操作

Dragging copies rather than moves. Remote deletion requires confirmation and is permanent; local deletion requires confirmation and uses the Recycle Bin. Existing regular files are never silently overwritten. Remote overwrite depends on the server's OpenSSH SFTP `posix-rename` extension. Direct remote-to-Windows-Explorer drops are finished by Windows, so Explorer handles destination conflicts and final completion; NodeBridge's **Download to…** action is the path for its own conflict dialog.

拖动执行复制而非移动。远程删除需要确认且永久生效；本地删除需要确认并移入回收站。已有普通文件不得被静默覆盖。远程覆盖依赖服务器的 OpenSSH SFTP `posix-rename` 扩展。从远程直接拖入 Windows 资源管理器的复制由 Windows 完成，因此目标冲突和最终完成状态由资源管理器处理；若需 NodeBridge 自己的冲突对话框，使用“下载到…”。
