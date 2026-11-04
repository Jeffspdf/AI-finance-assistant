# 警告 A/B/C：可执行方案与判定表

日期：2026-11-04。产品/候选基线仍为 `75c9ce5`，实验源码固定为旧提交 `18336e5`。本包只新增独立诊断工具和工具测试，不改 `desktop.py`、`finance/`、安装构建或候选产物。

**当前状态：工具预检就绪，A/B/C未执行。** 用户要求后台工作期间没有启动可见GUI、UAC或Procmon采集；预检使用合成控制台程序，不能当作目标警告的复现结果。main、v0.9.0标签和Release继续冻结。

## 工具与边界

- [`warning_experiment.py`](../scripts/warning_experiment.py)：prepare只导出/核验旧源码和生成计划；run明确要求`--allow-visible-gui`；analyze只读已有证据。无额外Python依赖。
- [`capture_warning_procmon.ps1`](../scripts/capture_warning_procmon.ps1)：由用户在管理员终端单独运行，使用事先审阅并保存的PMC过滤配置。拒绝接管已存在的Procmon，不自动提权、不改变防护或过滤设置；记录配置/工具/PML哈希、时限和停止方式。**本轮只做了PowerShell语法检查，没有执行管理员采集，也未验证其实际启停流程。**
- 原始账本全部新建合成目录，清除子进程继承的模型Key环境变量，不读取已安装客户端的数据。工具记录和PML保留在`.test_*`目录，禁止直接上传原始PML、CSV、输入法日志或整个实验目录。

### A：优先运行原入口

默认10次，可选10–20次，每次全新中文路径合成目录。内层PowerShell保留：

```powershell
& $s.python $s.source --self-test *>> $s.log
```

A不通过runpy执行，也不注入IME调用或修改Tk。实际source是旧提交导出副本，工作目录保持原项目目录；与历史的源码文件绝对路径/宿主环境仍有差异，不能称为所有条件完全相同。外部采样器只读本轮进程的映像/模块身份。

**先看A是否复现**。A没有复现时，即使B也没出现警告，仍不能归因IME或宣布已修。若某次退出非零、超时、缺新标志或源码核验失败，该次不计作有效阴性，工具停止该阶段，保留现场，重新实验需新session。

### B：匹配包装器的IME对照

B每轮两次，交替使用正常顺序和反向顺序：

| 组 | 入口 | IME行为 |
|---|---|---|
| A original | 原desktop.py直接入口 | 不干预 |
| B0 ime_normal | 包装器+runpy | 不干预，排除包装器本身的影响 |
| B1 ime_disabled | 同一包装器+runpy | 在运行desktop.py、创建Tk根窗之前调用`ImmDisableIME(0)` |

默认A共10次；B0/B1各10次，共20次，B不是单独拿“原入口”和“不同包装器”直接比较。记录调用返回值、线程ID、时间和实际Python PID。返回False则该B1无效，不继续假装成功。

这只干预诊断子进程当前线程，不切换用户全局输入法，不改客户端中文录入逻辑。[微软ImmDisableIME说明](https://learn.microsoft.com/en-us/windows/desktop/api/Imm/nf-imm-immdisableime)

**调用成功不等于搜狗已被排除**。模块采样记录见过的Sogou/PicFace路径和对应文件哈希；未采样到不能证明从未加载。读取权限不足、短命子进程或TSF行为不符合预期时，B只能给出有限关联，必须结合PML。

### PID、退出、时序与清理

不能把PowerShell/venv启动器PID直接当成实际GUI解释器PID。每次使用等待门和独立Windows Job：先建立本轮进程约束，再释放门启动Python，采样本轮成员的PID、映像、首次/末次观察和相关模块。B另有进程内PID记录可交叉核对。

Job没有CPU/内存/窗口限制，允许组件显式请求breakaway；但它仍是额外的宿主条件，未复现不能排除此差异。主动脱离Job或共享的外部输入法助手不保证被PID采样覆盖。若证据指向这些进程，需要在PML中明确关联后另行限定采集范围，不把无关进程自动算进来。

超时或中断只结束本轮Job和自己创建的等待包装器，不使用全机进程名杀进程。正常命令结束也记录尚存的成员，然后清理本轮Job。**清理前冻结日志快照**；清理后才产生的警告单列`warning_anytime`，不计作A/B有效复现。外层shell出现、但未进入追加文件的警告也单列，不能与原现象混算。

`warning_observed_window`是两次轮询之间的发现区间，不是原生写stderr的精确时刻。日志共享锁导致某次读取失败时不会推进“已知无警告”的时间边界；最终仍核对完整日志。`started/ended`、marker时间或模块存在都不能替代调用栈和实际发出时间。

## 执行步骤

以下从项目根目录、Windows PowerShell执行。**现在只准备，运行GUI必须等用户允许前台操作。**

```powershell
$py = '..\finance_assistant\.venv\Scripts\python.exe'
$session = Join-Path (Get-Location) '.test_warning_experiment_abc_v1'
& $py scripts/warning_experiment.py prepare --out $session --repetitions 10
```

session必须不存在，避免覆盖旧日志。prepare会固定原提交SHA、源码/资产/解释器/PowerShell可执行文件/驱动/包装器哈希。任何基线或脚本变化后重新准备，run会拒绝不一致的导出。默认固定prepare时PATH里找到的PowerShell；如需与历史宿主一致，传`--powershell`指定原命令中的pwsh.exe。run不会在另一个终端重新挑选不同的PowerShell，结果中另记实际PS版本。

本机本轮已经准备同名session，且显式固定为原历史命令的pwsh.exe；无需再次prepare。新环境或重做实验时使用新目录名。

### C：同步采集

先在Procmon审阅并保存过滤配置为`.pmc`，启用Drop Filtered Events。采集时保留目标Python进程的文件系统和进程/模块上下文；若捕获时只剩三种失败Operation，会丢失PID父子或模块加载线索。**分析/导出时**再筛CreateFile/WriteFile/SetEndOfFile及ACCESS DENIED/SHARING VIOLATION/PATH NOT FOUND，按本轮记录的实际PID与时间区间限定。存在其他Python任务时，原始PML可能包含它们；本轮摘录必须再次按PID/时刻复核，不上传原件。

管理员终端执行（事先保存并关闭旧Procmon窗口；脚本不会接管它）：

```powershell
$session = Join-Path (Get-Location) '.test_warning_experiment_abc_v1'
.\scripts\capture_warning_procmon.ps1 -Session $session -Profile '你的过滤配置.pmc' -ConfirmFilteredProfile -DurationSeconds 300
```

`-ConfirmFilteredProfile`只是操作者确认，脚本不会解析或证明PMC过滤正确。观察到backing file仅表示文件已创建，不能证明已收到事件；后续须核对PML有覆盖完整A/B时段的有效事件。默认采集时限300秒，另外有启动等待和停止等待；必要时可设15–600秒。另一个普通权限终端运行A，避免把应用也提权成与原条件不同：

```powershell
& $py scripts/warning_experiment.py run --session $session --phase A --allow-visible-gui
& $py scripts/warning_experiment.py analyze --session $session
```

先检查A及采集有效性，再决定是否执行B；不因A零命中就称B阴性有意义：

```powershell
& $py scripts/warning_experiment.py run --session $session --phase B --allow-visible-gui
New-Item -ItemType File -Path (Join-Path $session 'capture.stop')
```

采集脚本有界停止，并记录是否正常终止；被迫结束时PML可能不完整，不能据此验收C。采集期间不要把该实例用于其他排查。

Procmon中核对本轮实际PID、警告发现区间附近的失败路径、原生Stack及模块身份，然后保存限定的CSV摘录。CSV没有完整原生调用栈，分析器只将匹配PID/Operation/Result的行列为候选；时间文本不自动补日期或当作精确对应，PID复用仍需人工复核：

```powershell
& $py scripts/warning_experiment.py analyze --session $session --procmon-csv '筛选导出的.csv' --pml (Join-Path $session 'capture.pml')
```

PML只记录是否提供、大小/哈希，保持`provided but not stack-reviewed`；不伪称自动解读了调用栈。参见[微软Procmon采集说明](https://learn.microsoft.com/en-us/troubleshoot/windows-client/shell-experience/troubleshoot-apps-start-failure-use-process-monitor)。

## 判定表

| 结果 | 可以写的结论 | 不能写的结论 |
|---|---|---|
| A有效10–20次均无警告 | 当前指定组合未复现，保留宿主差异 | 已修复/IME无关/允许发布 |
| A多次出现同文警告 | 该组合可复现；保留日志/PID和时段 | 已证明搜狗或已知失败路径 |
| A出现，B0不出现 | 包装器/时序可能影响，B0/B1比较条件未对齐 | B1阴性证明IME原因 |
| B0稳定出现，B1稳定减少/消失，调用有效且模块证据一致 | 支持进程内IME相关的高可信关联 | 单靠差异就知道具体文件/原生函数 |
| C有失败I/O，但PID、时段、模块或栈不对应 | 独立候选事件，继续定位 | 警告由该路径失败引起 |
| 至少A+B或A+C等两路一致，并能说明具体模块/路径/失败机制 | 形成可供人工复核的运行时归因证据包 | 脚本自动宣布“根因已定位” |
| PML缺失/不完整、只交CSV、采样不到实际PID、trial无效 | 明确记录缺证或无效，不算有效阴性 | 用合成控制台预检补齐A/B/C |

**至少两路一致是必要条件，不是投票规则。** 若仍缺失败路径和原生栈，只能报告关联。所有JSON始终保留`root_cause_closed=false`、`formal_release_approved=false`，由评审另行判断解释是否充分；工具不合并main、不打标签、不发Release。即使归因为输入法，仍需决定是可说明的环境噪声还是用户可见问题，其他VM/真实数据等关口继续独立记录。

## 本次预检

25项独立工具测试通过：计划及GUI闸门、旧证据不覆盖、源码篡改拒跑、归档路径、两种输出编码、PID限定、无效/未执行场景、日志变化检测，以及真实无GUI PowerShell追加、实际子PID和原生Job超时清理。PowerShell采集脚本仅通过语法解析。A/B/C无实测数据，不做新的501项完整回归或EXE认证声明。

[预检机器记录](evidence/m2-warning-experiment/preflight.json) · [工具测试](../tests/test_warning_experiment.py)
