# Roadmap and TODO / 路线图与待办

## TODO / 待办

As the final release stage, provide a self-contained **portable Windows edition first**: extract and run it without an installer or Python preinstalled on the target machine. Test it on a clean Windows machine or equivalent environment and document extraction, startup, and updates. An installer is optional and comes only after the portable edition.

在最后的发行阶段，**优先提供免安装绿色 Windows 版**：解压即可运行，目标机器无需安装器或预装 Python。在干净的 Windows 机器或等效环境中验证，并说明解压、启动与更新方法。安装版可选，且排在绿色版之后。

Drag-copy collection from connected work nodes to the jump or local pane now uses a node-name suffix on each selected top-level item. Multi-source move and safe source deletion remain planned; do not delete any source after collection until that behavior is explicitly designed and confirmed.

现已支持从已连接工作节点拖动复制到跳板或本地，并在每个选中顶层项目名称后加节点名后缀。多来源移动及安全删除来源仍待实现；在明确设计和确认相关行为前，汇集后不删除任何来源。

Support dragging work-node files to Windows Explorer/Desktop without prompting or staging a complete copy before the drop. Keep source-node selection after the destination is known where the Windows integration permits it, and preserve portable packaging.

支持将工作节点文件拖到 Windows 资源管理器／桌面，且拖动前不弹范围框、不预先缓存完整副本。在 Windows 集成方式允许取得目标后再选择来源节点，并保持绿色版可用。

Expose single-destination move and the remaining work-node transfer directions through drag-and-drop or context actions, with explicit source-deletion confirmation. Avoid restoring a separate batch-operation button.

通过拖放或右键操作提供单目标移动及工作节点的其他传输方向，并明确确认来源删除；不要恢复独立的批量操作按钮。

Site Manager now offers plaintext, no-save, and master-password-encrypted modes. Converting an existing `sites.json` to the master-password mode removes plaintext from the current file. Before portable release, verify data-directory behavior in a built executable, migration from a source installation, and how historical file backups are handled.

站点管理器现提供明文、不保存及主密码加密三种模式。把已有 `sites.json` 切换到主密码模式后，当前文件不再包含明文密码。绿色版发行前仍需在打包程序中验证 `data` 目录行为、从源码版本迁移资料的方法，以及历史文件备份的处理方式。

### Terminal follow-up / 终端后续工作

Verify multi-group connection capacity, group isolation, terminal-only jump reconnection, Tab completion, and harmless `ls` broadcast on the owner's cluster. The owner observed `ChannelException(2, 'Connect failed')` while opening cft15–21; the new bounded retry path has only local simulated coverage so far. Record the exact server response if it still fails.

在项目所有者的集群上验证多组连接容量、组间隔离、终端专用跳板连接重试、Tab 补全和无害的 `ls` 广播。所有者曾在打开 cft15–21 时看到 `ChannelException(2, 'Connect failed')`；新增的有限次重试目前只有本地模拟测试。若仍失败，应记录服务器返回的准确错误。

Multiple nodes can already hold simultaneous Shell channels, and one node can have several terminals. Closing an individual terminal tab hides its widget while the SSH channel stays open and can be shown again from the node tree. **Still planned:** true remote-session persistence across SSH disconnection and application restart. Evaluate remote `tmux`, GNU screen, another session manager, or an application-managed equivalent; a hidden SSH channel alone is not persistent. Distinguish hiding a view, detaching a remote session, disconnecting SSH, and terminating the remote process.

目前已能同时保留多个节点的 Shell 通道，同一节点也能打开多个终端。关闭单个终端标签只隐藏控件，SSH 通道仍保持活动，并可从节点树重新显示。**仍待实现：**SSH 断开和应用重启后的真正远程会话持久化。需评估远端 `tmux`、GNU screen、其他会话管理器或由程序管理的等价机制；隐藏 SSH 通道不等于持久化。应区分隐藏视图、分离远程会话、断开 SSH 与终止远端进程。

The multi-node command broadcast action sends one command to selected terminals within one active Terminal Group, at most one per node. Each node keeps its own output stream and session state. Common destructive commands trigger an extra confirmation. Future work includes reliable per-command exit status and broader safeguards. Terminal logic stays separate from GUI presentation.

多节点命令广播操作可向一个当前 Terminal Group 内选中的终端发送同一命令，每节点最多一个终端。各节点的输出流和会话状态分别保留。常见破坏性命令会触发额外确认。未来仍需可靠的逐命令退出状态和更广泛的风险防护。终端逻辑与 GUI 展示层分开。

### Completed core — Terminal Groups and group-scoped broadcast / 已完成基础功能：终端分组与组内广播

Each successfully opened batch of selected nodes now creates a `Terminal Group`. A later batch creates another group even when node names overlap. Groups have run-local stable IDs, names, member sessions, and selected recipients; switching groups clears the broadcast draft. Group state is not persisted across application restarts.

每次成功为一批选中节点打开终端时，都会创建一个 `Terminal Group`；之后批次即使包含相同节点，也会形成新组。组具有本次运行内稳定的 ID、名称、成员会话和已选目标；切换组会清空广播草稿。组状态不会在应用重启后保留。

Broadcast now targets **exactly one active group** and rejects sessions outside it. The Terminal tab shows the group and its selectable member sessions; the broadcast summary shows the active group and receiving nodes. No mode checkbox is needed; switching groups clears the draft. Common destructive commands require a separate confirmation. There is no implicit global broadcast; any future cross-group feature would need a separate, explicit design. Command-pattern detection is only a safeguard, not a complete Shell parser.

广播现已**严格限定在一个当前组内**，会拒绝该组之外的会话。终端页显示组及可勾选的成员会话；广播摘要显示当前组和接收节点。无需勾选模式；切换组会清空草稿。常见破坏性命令另需确认。不存在隐式全局广播；未来若考虑跨组功能，必须单独明确设计。命令模式检测只是辅助防护，并非完整 Shell 解析器。

### Priority B — group identical broadcast results / 优先级 B：合并相同的广播结果

The current read-only Combined view concatenates each broadcast's original terminal replies by node, with prompts and text colors preserved and no added headings. Identical-result comparison and deduplication remain in this priority. Reliable command completion and exit-status detection beyond recognizable Shell prompts also remain planned.

当前只读“联合显示”已按节点顺序直接拼接每次广播的原始终端反馈，保留提示符和字色，不额外添加标题。相同结果的比较与去重仍属本优先级。超出可识别 Shell 提示符范围的可靠命令结束及退出状态检测也仍待实现。

After the core group workflow is stable, consider showing effectively identical results from one bounded broadcast command once, labeled with all matching nodes, while different results remain separate. Keep every terminal's raw output available. Comparison may normalize presentation-only differences such as ANSI and cursor controls, whitespace and line endings, width, prompts, and node-specific hostnames; do not assume byte-for-byte equality is enough. Do not merge unbounded interactive output from programs such as `top`, `vim`, `watch`, or `tail -f` into a single live terminal state.

组内基础流程稳定后，再考虑将一次有明确边界的广播命令产生的实质相同结果只显示一份，并标出全部对应节点；不同结果单独显示，同时保留各终端原始输出。比较时可能需要归一化 ANSI 与光标控制、空白和换行、终端宽度、提示符及节点特有主机名等展示差异，不能只依赖逐字节相等。不得把 `top`、`vim`、`watch`、`tail -f` 等持续交互输出盲目合并为一个实时终端状态。

## Provisional roadmap / 暂定路线

1. Phase 0: architecture, documentation, and stack decision — complete.
2. Phase 1: minimal GUI and one-node SSH connection — implemented; owner reported a successful live connection with the previous layout.
3. Phase 2: remote directory browsing — implemented; local and fake-backend GUI checks passed.
4. Phase 3: basic file operations and safety behavior — copy, rename, create directory, and confirmed delete implemented; a one-destination move engine exists but has no current UI entry point.
5. Phase 4: drag-and-drop and transfer progress or queue — copy drag-and-drop implemented; progress bar and queue remain planned.
6. Phase 5: multiple configured nodes and switching — site profiles, sequential SSH jumps, and focused or merged work-node browsing implemented.
7. Phase 6: concurrent multi-node copy distribution, suffix-based drag collection, and confirmed delete — implemented with up to four node tasks at once, without a four-node total limit; a transfer queue, transfer-task retry, and multi-source move remain planned.
8. Phase 7: robustness and UX refinement — ongoing.
9. Phase 8: interactive remote SSH terminal with PTY — implemented for ordinary Shell interaction; full-screen terminal emulation remains planned.
10. Phase 9: simultaneous node terminals — implemented; detached and persistent remote sessions remain planned.
11. Phase 10: basic explicit multi-node command broadcast implemented; per-command exit status and further orchestration remain planned.
12. Phase 11: Terminal Groups and strictly group-scoped broadcast implemented; optional identical-result grouping (Priority B) remains planned.
13. Final release stage: self-contained portable Windows edition first, requiring no installer or preinstalled Python; an installer may follow later.

1. 阶段 0：架构、文档和技术栈决策——已完成。
2. 阶段 1：最小 GUI 与单节点 SSH 连接——已实现；项目所有者反馈旧布局下真实连接成功。
3. 阶段 2：远程目录浏览——已实现；本地与虚拟远程后端的 GUI 检查通过。
4. 阶段 3：基本文件操作与安全行为——已实现复制、重命名、新建目录及经确认的删除；单目标移动底层已有实现，但当前没有界面入口。
5. 阶段 4：拖放与传输进度或队列——已实现拖动复制；进度条与队列仍待实现。
6. 阶段 5：多个节点的配置与切换——已支持站点资料、逐级 SSH 中转及单节点聚焦或合并浏览。
7. 阶段 6：多节点并行复制分发、按后缀拖动汇集及经确认的删除——一次最多并行处理四个节点任务，节点总数不限制为四个；传输队列、任务重试和多来源移动仍待实现。
8. 阶段 7：健壮性与交互完善——进行中。
9. 阶段 8：带 PTY 的交互式 SSH 远程终端——普通 Shell 交互已实现；全屏终端仿真仍待实现。
10. 阶段 9：多个节点的终端同时运行——已实现；可分离且持久的远程会话仍待实现。
11. 阶段 10：明确标示的多节点命令广播基本功能已实现；逐命令退出状态及更多节点协同功能仍待实现。
12. 阶段 11：Terminal Group 与严格组内广播已实现；相同结果合并显示（优先级 B）仍待实现。
13. 最后的发行阶段：优先提供自包含的免安装 Windows 绿色版，无需安装器或预装 Python；安装版可在以后考虑。

This roadmap is provisional. The owner will review each meaningful stage before implementation continues.

此路线图为暂定规划。每个有意义的阶段结束后，由项目所有者审阅并决定后续工作。
