# NodeBridge — Agent Working Rules / Agent 工作规则

## Purpose / 项目目标

NodeBridge is a desktop GUI project for managing files on SSH-accessible remote nodes. Windows is the initial target; support for other desktop platforms may be needed later. SSH/SFTP browsing, copy transfers, rename, directory creation, and confirmed delete are implemented; move and concurrent multi-node transfers remain planned.

NodeBridge 是管理 SSH 可访问远程节点文件的桌面 GUI 项目。初期面向 Windows，将来可能支持其他桌面平台。SSH/SFTP 浏览、复制传输、重命名、新建目录和经确认的删除已实现；移动与多节点并发传输仍处于规划阶段。

## Confirmed technology / 已确定技术

Use the Python 3.12 Conda environment defined in `environment.yml`, PySide6 for the desktop application, Paramiko for SSH/SFTP, and Qt background threads for blocking network operations. Run verification in the project-local Conda environment when available. Build and verify Windows first. Keep platform-specific integration, such as credential storage and packaging, behind clear boundaries so other platforms remain feasible; cross-platform support is not an implemented feature or an immediate release requirement. Site profiles are persisted in the user's configuration directory.

桌面应用使用 `environment.yml` 定义的 Python 3.12 Conda 环境，GUI 采用 PySide6，SSH/SFTP 使用 Paramiko，阻塞式网络操作放在 Qt 后台线程。可用时在项目本地 Conda 环境中验证。首先面向 Windows 开发和验证。将凭据存储、打包等平台相关集成保持在清晰边界内，为未来支持其他平台保留可能；跨平台支持尚未实现，也不是近期版本的交付要求。站点资料保存在用户配置目录。

## Stage and communication / 阶段与沟通

Develop incrementally and implement only the stage the owner requests. Communicate with the owner in Chinese. Pair each logical English paragraph or list in important project documentation with its corresponding Chinese paragraph or list immediately below. Ask grouped questions about choices that materially affect architecture, security, data integrity, or interaction; decide routine, reversible details without repeated questions.

按项目所有者指定的阶段逐步开发，不擅自跨阶段实现功能。与所有者使用中文交流。重要项目文档的每个英文逻辑段落或列表后，紧接对应中文段落或列表。对明显影响架构、安全、数据完整性或交互的选择集中提问；普通且可逆的细节自行判断。

## Intended architecture / 规划架构

Keep node configuration, live SSH/SFTP connection, remote filesystem session, and file-operation task separate. Keep connection and session logic outside the GUI; translate UI actions, including drag-and-drop, into explicit operations. Treat the local filesystem as a possible endpoint in the operation model without forcing an elaborate abstraction prematurely. Never depend on one global current connection. Plan for multiple active nodes and concurrent tasks, while keeping network I/O off the GUI thread.

将节点配置、实时 SSH/SFTP 连接、远程文件系统会话和文件操作任务分开。连接与会话逻辑不放在 GUI 内；GUI 操作（包括拖放）转换为明确的操作任务。操作模型应考虑本地文件系统作为端点，但不必过早建立复杂抽象。不得依赖唯一的全局当前连接。架构需容纳多个活动节点和并发任务，网络 I/O 不得阻塞 GUI 线程。

When connecting to a target through the current SSH site, keep the jump session alive until the target disconnects; returning to the jump site must not require reconnecting. Saved-site TCP jumps verify the target host key against the local user's known hosts. SSH-alias jumps run OpenSSH on the current remote site and verify the target key against that site's known hosts; require passwordless login and strict host-key checking. A failed target connection must leave the jump session usable.

通过当前 SSH 站点连接目标时，跳板会话须保持活动，直到目标断开；返回跳板站点不应重新连接。经站点资料进行 TCP 中转时，目标主机密钥对照本机用户的已知主机记录校验；SSH 别名中转则在当前远程站点运行 OpenSSH，对照该站点的已知主机记录校验，并要求免密登录及严格的主机密钥检查。目标连接失败时，跳板会话应保持可用。

Remote filesystems may differ in permissions, symlinks, special files, metadata speed, and directory size. Design deletion, overwrite, replacement, and recursive operations with explicit user intent and clear error reporting. Do not silently overwrite or delete data.

远程文件系统可能在权限、符号链接、特殊文件、元数据速度和目录规模上存在差异。删除、覆盖、替换和递归操作必须体现明确的用户意图，并清楚报告错误。不得静默覆盖或删除数据。

## Security / 安全

Never put plaintext passwords in source, project files, tracked configuration, or ordinary logs. The owner has explicitly approved a temporary exception: saved site passwords may be stored as plaintext in the per-user configuration file outside the repository. Tell users where that file is and migrate it to OS-protected credential storage in a later stage; see the README TODO. Do not invent cryptography. Never disable host-key verification by default. Make unknown or changed host keys an explicit decision for the user. Discuss any future major credential-storage changes with the owner.

不得把明文密码写入源码、项目文件、受 Git 跟踪的配置或普通日志。项目所有者明确批准一项临时例外：已保存站点的密码可明文存于仓库之外的用户配置文件。应向用户说明文件位置，并在后续阶段迁移到操作系统保护的凭据存储；见 README 的 TODO。不自行设计加密方案。不得默认关闭主机密钥校验；未知或变更的主机密钥必须由用户明确处理。以后若要作出重大凭据存储变更，应与所有者讨论。

## Workflow / 开发流程

Before work, read this file and `README.md`, inspect the repository and, if Git is initialized, inspect `git status` and `git diff`. Confirm the requested stage, make only relevant changes, run appropriate verification, inspect the resulting diff, update documentation when architecture or status changes, and report results in Chinese. Never claim a test passed unless it ran. Do not commit unless explicitly requested.

开始工作前阅读本文件和 `README.md`，检查仓库；若已初始化 Git，还需查看 `git status` 和 `git diff`。确认本次阶段，只做相关修改，执行适当验证，检查变更；架构或进度变化时更新文档，并用中文汇报。未经实际运行不得声称测试通过。未经明确要求不得提交。

Do not silently change agreed architecture or security behavior, implement unrelated future features, or refactor working code during unrelated tasks. Distinguish planned behavior from implemented behavior in every status report and document.

不得擅自改变已商定的架构或安全行为，不实现无关的未来功能，也不在无关任务中重构正常工作的代码。所有进度汇报和文档都要区分规划与已实现内容。
