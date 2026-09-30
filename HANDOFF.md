# Handoff / 接手说明

This file helps the next contributor resume work without relying on chat history. Read [AGENTS.md](AGENTS.md) for working rules, [CURRENT_STATUS.md](CURRENT_STATUS.md) for the verified state, [DECISIONS.md](DECISIONS.md) for constraints, and [ARCHITECTURE.md](ARCHITECTURE.md) for code boundaries. [README.md](README.md) is the user guide.

本文帮助下一位开发者在不依赖聊天记录的情况下接续工作。工作规则见 [AGENTS.md](AGENTS.md)，已验证状态见 [CURRENT_STATUS.md](CURRENT_STATUS.md)，约束与选择见 [DECISIONS.md](DECISIONS.md)，代码边界见 [ARCHITECTURE.md](ARCHITECTURE.md)。[README.md](README.md) 是用户指南。

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

Before changing transfer or deletion behavior, exercise a disposable remote directory and review conflict, cancellation, symlink, and partial-completion behavior. The most useful live checks are SSH-alias jumping, saved-site tunneling, both transfer directions with a same-name file, local Recycle Bin deletion, remote confirmed deletion, and cross-application drag-and-drop. Never use valuable files for destructive checks.

变更传输或删除行为前，应使用可丢弃的远程目录，检查同名冲突、取消、符号链接和部分完成的行为。最有价值的实测包括 SSH 别名中转、经已保存站点建立隧道、双向同名文件传输、本地回收站删除、经确认的远程删除，以及跨应用拖放。破坏性检查不得使用重要文件。

## GitHub state / GitHub 状态

The owner created and pushed the initial Git commit; local `main` and `origin/main` matched during this audit. The owner reports that the repository is public. Git is installed locally; GitHub CLI (`gh`) was not found on `PATH`. `.gitignore` excludes the local Conda environment, Python caches, `sites.json`, and common credential-file formats. Before each future commit, inspect the staged file list and diff for credentials and private host details. The owner chose `GPL-3.0-only`; `LICENSE` and documentation changes are pending a commit and push. Do not create a commit or push on behalf of the owner without their request.

项目所有者已创建并推送初始 Git 提交；此次检查时本地 `main` 与 `origin/main` 一致。据项目所有者说明，仓库已公开。本机已安装 Git；`PATH` 中没有 GitHub CLI（`gh`）。`.gitignore` 排除了本地 Conda 环境、Python 缓存、`sites.json` 和常见凭据文件格式。以后每次提交前都应检查暂存文件清单与差异，确认没有凭据或内部主机细节。所有者选择了 `GPL-3.0-only`；`LICENSE` 和相关文档改动尚待提交和推送。未经所有者要求，不代为创建提交或推送。
