# Windows portable build / Windows 绿色版

Windows native crash tracing is now disabled by default because the CPython traceback dumper itself crashed during Windows exception handling. `errors.log` still records Python/Qt failures; `crash.log` records this configuration. Inspect Windows Application events for native failures. Diagnostic opt-in `NODEBRIDGE_NATIVE_TRACE=1` enables current-thread native tracing and may reintroduce the crash. References below to automatic native traces apply only when tracing is enabled.

由于 CPython 栈输出器在处理 Windows 异常时自身崩溃，Windows 现默认关闭自动原生崩溃栈捕获。`errors.log` 仍记录 Python／Qt 错误，`crash.log` 记录此配置；原生故障请查看 Windows 应用程序事件日志。诊断时可用 `NODEBRIDGE_NATIVE_TRACE=1` 启用当前线程原生栈捕获，但可能再次触发崩溃。下文关于自动原生栈记录的说明仅适用于启用捕获时。

`dist` is short for **distribution** and contains build outputs rather than source code. `dist/NodeBridge-portable.zip` is the latest portable archive. Each `dist/portable-builds/<build-id>/NodeBridge/` folder contains a build's `NodeBridge.exe`, its required `_internal` runtime components, and `LICENSE`. The older `dist/NodeBridge/` folder is a previous test build, not the latest output of the current build script. Git ignores these generated files.

`dist` 是 **distribution**（发行文件）的缩写，存放构建产物，而不是源代码。`dist/NodeBridge-portable.zip` 是最新的绿色版压缩包。每个 `dist/portable-builds/<构建编号>/NodeBridge/` 文件夹包含该次构建的 `NodeBridge.exe`、运行所需的 `_internal` 组件和 `LICENSE`。较早的 `dist/NodeBridge/` 是旧测试版本，不是当前构建脚本的最新输出。Git 会忽略这些生成文件。

The experimental portable edition is a folder, not a single executable. Build it on Windows with the project-local Python 3.12 Conda environment:

试验性绿色版采用完整文件夹，而不是单个可执行文件。在 Windows 上使用项目本地 Python 3.12 Conda 环境构建：

```powershell
.\.conda-env\python.exe scripts\build_portable.py
```

The script writes PyInstaller output directly to UTF-8 `dist/build_portable_latest.log`. Open it with a UTF-8 editor for build diagnostics. Do not use PowerShell `*>` to create this log; PowerShell's native-command redirection can change the encoding or replace characters before writing them.

脚本会直接将 PyInstaller 输出写入 UTF-8 编码的 `dist/build_portable_latest.log`，可用支持 UTF-8 的编辑器查看构建诊断。不要用 PowerShell 的 `*>` 生成这个日志；其原生命令重定向可能改变编码，或在写入前就把字符替换掉。

Run that command again from the project root after changing source code. It creates a new numbered build folder and updates the ZIP; it does not update a previously extracted copy automatically. To update a copy you use, close NodeBridge, back up its `data` folder, replace `NodeBridge.exe` and `_internal` with the new build, and retain `data`. The local Conda environment is needed to build, but is not part of the archive.

以后修改源代码后，在项目根目录再次运行上述命令即可重建。脚本会新建带编号的构建目录并更新 ZIP，不会自动更新已解压使用的旧程序。更新正在使用的版本时，先关闭 NodeBridge、备份其 `data`，再用新构建的 `NodeBridge.exe` 和 `_internal` 替换旧文件，同时保留 `data`。本地 Conda 环境用于构建，不包含在压缩包中。

Each build creates a new `dist/portable-builds/<build-id>/NodeBridge/` folder and replaces `dist/NodeBridge-portable.zip`. Older build folders and their `data` remain untouched, even if an older executable is running. The archive excludes `data`. Extract the whole `NodeBridge` folder to a writable location, then open `NodeBridge.exe`. The target should not need Python or Conda installed. Do not run only the executable without its `_internal` folder. This has been smoke-tested on the build machine; testing on a clean Windows machine is still required before release.

每次构建都会新建一个 `dist/portable-builds/<构建编号>/NodeBridge/` 文件夹，并替换 `dist/NodeBridge-portable.zip`。旧构建目录及其 `data` 不会被改动，即使旧程序仍在运行。压缩包排除 `data`。将压缩包内完整的 `NodeBridge` 文件夹解压到可写位置，再打开 `NodeBridge.exe`。目标机器按设计无需安装 Python 或 Conda。不要脱离 `_internal` 文件夹单独运行可执行文件。目前只在构建机器做过启动冒烟测试，正式发布前仍需在干净的 Windows 机器验证。

Portable settings, site profiles, operation logs, and diagnostics live under `data/` beside `NodeBridge.exe`. The archive deliberately excludes `data`, including passwords and local logs. A first run creates it. Source-run profiles are not migrated automatically. To move an existing profile, copy your source-run `sites.json` into `data/sites.json` yourself while the app is closed; inspect its password mode before sharing or backing up the folder. When updating, replace the executable and `_internal` folder and keep your own `data` folder. Keep a private backup of `data` before updates.

绿色版的设置、站点资料、操作日志和诊断信息位于 `NodeBridge.exe` 旁的 `data/`。压缩包会刻意排除 `data`，包括其中的密码和本机日志。首次运行会创建该目录。源码版的站点资料不会自动迁移；若需要迁移，请关闭程序后自行将源码版 `sites.json` 复制到 `data/sites.json`，分享或备份前先检查密码保存模式。更新时替换可执行文件和 `_internal` 文件夹，保留自己的 `data` 文件夹；建议更新前自行备份 `data`。

For a file-operation history, open **View → Open log file** (`data/logs/nodebridge.log`). For runtime failures or warnings, inspect `data/logs/errors.log`. `data/logs/crash.log` is a best-effort native fault trace; it may remain empty. The latter two files may contain exception details or local paths, so review them before sharing. Diagnostics cannot guarantee a record for failures before initialization or forced termination. The activity and error logs rotate at about 5 MB and retain five backups; `crash.log` does not rotate.

文件操作记录可通过“查看 → 打开日志文件”打开（`data/logs/nodebridge.log`）。运行时错误或警告见 `data/logs/errors.log`；`data/logs/crash.log` 尽可能记录原生崩溃信息，也可能一直为空。后两者可能包含异常细节或本机路径，分享前请检查。诊断初始化之前的故障或强制终止不保证留下记录。操作日志和错误日志约达 5 MB 后轮转，并保留五份旧文件；`crash.log` 不轮转。
