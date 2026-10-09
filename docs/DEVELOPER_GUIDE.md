# Developer guide / 开发者指南

This guide is for the project owner and coding agents. The end-user instructions are in [USER_GUIDE.md](USER_GUIDE.md) and are shown in the application's **Help → User guide** menu. Keep that guide in Chinese and focused on observable behavior. Keep the GitHub [README](../README.md) short. Read [AGENTS.md](../AGENTS.md) before changes; [ARCHITECTURE.md](ARCHITECTURE.md), [CURRENT_STATUS.md](CURRENT_STATUS.md), [DECISIONS.md](DECISIONS.md), and [TODO.md](TODO.md) hold the detailed design, implementation boundary, settled choices, and planned work.

本指南面向项目作者与编码 Agent。最终用户说明见 [USER_GUIDE.md](USER_GUIDE.md)，也可从程序“帮助 → 使用指南”打开。该指南保持纯中文，只描述用户能观察到的行为；GitHub 首页的 [README](../README.md) 保持简洁。修改前先读 [AGENTS.md](../AGENTS.md)；详细架构、实现边界、已定选择和待办分别见 [ARCHITECTURE.md](ARCHITECTURE.md)、[CURRENT_STATUS.md](CURRENT_STATUS.md)、[DECISIONS.md](DECISIONS.md) 与 [TODO.md](TODO.md)。

For a new session, [HANDOFF.md](HANDOFF.md) lists the next acceptance checks. [PORTABLE.md](PORTABLE.md) explains the Windows build and update process. Record user-visible changes in [CHANGELOG.md](CHANGELOG.md) under `Unreleased`; move them into a dated version section only when that version is released.

新会话可从 [HANDOFF.md](HANDOFF.md) 查看下一步验收事项；Windows 绿色版的构建和更新步骤见 [PORTABLE.md](PORTABLE.md)。用户可见的改动先记入 [CHANGELOG.md](CHANGELOG.md) 的“未发布”部分，实际发行时再归入带日期的版本章节。

## Local environment / 本地环境

Use the Python 3.12 Conda environment from `environment.yml`. The project package runs directly from the repository root; no editable installation is required. On Windows PowerShell:

使用 `environment.yml` 定义的 Python 3.12 Conda 环境。包可直接从仓库根目录运行，无需可编辑安装。在 Windows PowerShell 中执行：

```powershell
conda env create --prefix .\.conda-env --file environment.yml
.\.conda-env\python.exe -m nodebridge
```

Run focused tests for the changed behavior in the same environment. For GUI tests, use the existing test fixtures and offscreen Qt configuration; do not keep a real SSH connection or user data in automated tests. Inspect `git status`, the diff, and `git diff --check` before reporting. Do not commit unless the owner asks.

在同一环境中针对本次修改运行定向测试。界面测试使用现有夹具与 Qt 离屏配置；自动化测试不要连接真实 SSH 或使用用户资料。汇报前检查 `git status`、差异以及 `git diff --check`。未经项目所有者要求不要提交。

## Code map / 代码位置

`nodebridge/window.py` coordinates the GUI and background jobs; `remote.py` owns SSH/SFTP sessions and alias routing; `file_operations.py`, `transfer.py`, `batch.py`, and `content_check.py` implement file actions and verification. `worker_browser.py` renders indirect nodes. `terminal_workspace.py`, `terminal_session.py`, and `remote_terminal_widget.py` implement terminal groups, channels, and the xterm.js frontend. `sites.py` owns site profiles. `activity_log.py` records user actions, while `diagnostics.py` records warnings and failures. Keep blocking network work off the GUI thread and keep user actions explicit before destructive operations.

`nodebridge/window.py` 协调界面与后台任务；`remote.py` 管理 SSH/SFTP 会话和别名路由；`file_operations.py`、`transfer.py`、`batch.py` 与 `content_check.py` 实现文件操作和核对。`worker_browser.py` 显示间接节点。`terminal_workspace.py`、`terminal_session.py` 与 `remote_terminal_widget.py` 实现终端组、通道及 xterm.js 前端。`sites.py` 管理站点资料。`activity_log.py` 记录用户操作，`diagnostics.py` 记录警告和故障。阻塞式网络操作应离开 GUI 线程，破坏性操作前须明确取得用户意图。

## Portable Windows build / Windows 绿色版构建

Build with `scripts/build_portable.py`, which uses `nodebridge.spec` and creates a new versioned folder plus `dist/NodeBridge-portable.zip`. The spec bundles Python, Qt WebEngine, terminal assets, the app icon, the Chinese user guide, and required DLLs. Do not invoke PyInstaller directly against an in-use `dist/NodeBridge` folder: it may remove that folder's `data`. The build script uses a fresh output folder and excludes `data` from the archive. See [PORTABLE.md](PORTABLE.md) for installation, profile migration, and clean-machine acceptance.

使用 `scripts/build_portable.py` 构建；它读取 `nodebridge.spec`，生成新的带编号目录和 `dist/NodeBridge-portable.zip`。打包内容包括 Python、Qt WebEngine、终端资源、程序图标、中文使用指南及所需 DLL。不要直接对正在使用的 `dist/NodeBridge` 目录调用 PyInstaller：这可能删除其中的 `data`。构建脚本使用全新输出目录，压缩包排除 `data`。安装、资料迁移与干净机器验收见 [PORTABLE.md](PORTABLE.md)。

```powershell
.\.conda-env\python.exe scripts\build_portable.py
```

## Logs and data / 日志与数据

Source runs keep site profiles and logs outside the repository. Frozen runs use `data` beside the executable. The UI activity log and `nodebridge.log` contain structured file-operation events; `errors.log` contains Python/Qt warnings and failures; `crash.log` is a best-effort native trace. Never put passwords in source, tracked files, or ordinary logs. A user may choose plaintext site storage only in an untracked private site file. Check build archives and staged Git files for credentials before sharing or release.

源码运行将站点资料和日志放在仓库外；打包程序使用可执行文件旁的 `data`。界面日志和 `nodebridge.log` 记录结构化文件操作事件；`errors.log` 记录 Python／Qt 警告和故障；`crash.log` 尽可能记录原生崩溃栈。不得把密码写入源码、受 Git 跟踪的文件或普通日志。用户若选择明文站点模式，也只能保存在未跟踪的私有站点文件中。分享或发布前检查构建压缩包与 Git 暂存文件是否含凭据。
