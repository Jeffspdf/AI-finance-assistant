# 历史写文件警告：第二次定位记录

日期：2026-11-04。产品/候选基线 `75c9ce5`，原始复跑提交 `18336e5`。本次只增加诊断记录，未修改产品源码或重建候选 EXE。**根因仍未定位，正式发布关口不通过。** M2-5 工程候选已获评审通过；继续不合并 main、不打 v0.9.0 标签、不发 Release。

## 已取得的证据

| 项目 | 实际结果 | 可以推导什么 |
|---|---|---|
| 历史 M2-2 / M2-3 日志 | 两份各 31 字节，均为 `can't open file for write !` 加两次换行；无 PID、阶段或失败路径 | 警告确实存在，不能靠退出 0 或后续未复现关闭 |
| 原 M2-3 + 中文源码/工作/数据路径 | 直接子进程双管道、PowerShell `*>`、PowerShell 分开 stdout/stderr、隐藏 Start-Process 分流，各运行 6 组，共 24 次，均退出 0、生成新的验收标志、无目标警告 | 这些条件下本轮未复现；不能断定中文路径或某种重定向已排除 |
| 历史合成目录副本 | 通用自检可运行；旧 M1a 重用已跑过的数据时，`len(groups)==2` 断言失败；另外四组直接运行退出 0 | 这是已消费候选初态导致的工程自检非幂等，与目标警告不同；该组失败运行不算通过 |
| 实际创建 Tk 后的原生模块 | 检查当前进程 139 个已加载模块，未找到完整警告文本 | 不能排除拼接文本、后加载模块或其他进程，不构成归因 |
| ASCII 路径对照 | 原计划 ASCII 可写根实际拒绝写入；临时目录回退到中文工作区，受限令牌的 ASCII 临时目录也无法完成源码展开 | **未完成，不能列作通过或据此比较编码影响** |
| Defender 只读诊断 | 当前配置查询返回 `0x800106ba`；2026-11-04 14:00–15:00 的 1123/1124 查询无匹配事件 | 没有取得对应阻止事件；不能断言防护关闭、从未阻止，或与警告无关 |
| Process Monitor | 官方下载、签名 Valid / Microsoft Corporation，版本 4.12；未启动驱动或进行采集 | 工具已准备；当前令牌非管理员，后续需在管理员环境完成过滤采集 |

中文对照始终使用只读导出的旧提交、同一 Python 环境和新建合成目录；原历史数据先后哈希一致，未改写。源码/工作目录是导出副本，**不是把当前工作树原地切回旧版本**，也未声称完全重建原先执行环境。验收标志在运行前清除，避免副本里的旧 JSON 被当作新成功。

最初的历史副本尝试中有旧验收标志，该尝试不用于统计通过次数；24 次成功来自随后独立的新目录实验。ASCII 实验中的权限错误同样不等于产品产生了目标警告。

[机器汇总](evidence/m2-warning-followup/results.json) · [24 次中文路径对照](evidence/m2-warning-followup/chinese-controls.json)

## 工具准备

本机工具：`tools/procmon/Procmon64.exe`（工具目录受 gitignore 排除，不随源码或 Release 分发）。下载源为[微软 Sysinternals Process Monitor](https://learn.microsoft.com/en-us/sysinternals/downloads/procmon)，签名已在本机验证。核对值见机器汇总。

受控文件夹访问未被关闭或增加放行项。本次没有进行全机无过滤采集，没有获取个人账本或 Key。原始 PML/CSV 以后也应只在本机保留，公开证据只摘录与目标进程有关的失败事件并脱敏。

## 下一步的管理员采集

1. 在管理员环境打开已验证的 Procmon，先暂停捕获并清空旧事件；设置 Process Name 为 `python.exe` / `AIFinanceHelper.exe`，Operation 为 CreateFile / WriteFile / SetEndOfFile，Result 为 ACCESS DENIED / SHARING VIOLATION / PATH NOT FOUND。启用 Drop Filtered Events，确认过滤后再开始。若有其他 Python 工作，事后必须按下方生成的 PID 再筛选，只保留本轮进程。
2. 从下面命令运行**新建合成目录**的旧 M2-3 自检，保留每阶段 PID、时间、退出码、stdout/stderr。另在管理员可写的 ASCII 目录做同样一轮；不要复用 M1a 已跑过的库。
3. 自检结束立即暂停并保存 PML；按记录的 PID 定位失败路径、同一时刻的操作和调用栈。日志出现目标警告时，必须对应同一进程/阶段/时序；单独看到 ACCESS DENIED 不足以认定根因。必要时再采集未限制 Result 的同一目标进程，以免漏掉相关上下文。

微软给出了命令行采集、保存和终止方式，但本次尚未执行，不能把准备文档当作已有 trace。[微软采集说明](https://learn.microsoft.com/en-us/troubleshoot/windows-client/shell-experience/troubleshoot-apps-start-failure-use-process-monitor)

下面命令从项目根目录运行，旧提交导出副本已经存在；它不会自动启动/停止 Procmon，也不会改变防护设置。需要在 Procmon 开始记录后执行。非零退出或缺新标志会停止该轮，进程错误留在独立日志。

```powershell
$project = (Get-Location).Path
$python = Join-Path $project '..\finance_assistant\.venv\Scripts\python.exe'
$oldCode = Join-Path $project '.test_warning_probe\中文路径\code-18336e5'
$case = Join-Path $project ('.test_warning_capture\' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $case -Force | Out-Null
$oldData = $env:FINANCE_DESKTOP_DATA_DIR
$env:FINANCE_DESKTOP_DATA_DIR = Join-Path $case 'data'
$checks = [ordered]@{
  '--self-test'='self-test.ok'; '--self-test-m1a'='m1a-self-test.json'
  '--self-test-round5'='round5-self-test.json'; '--self-test-round6'='round6-self-test.json'
  '--self-test-round7'='round7-self-test.json'; '--self-test-round8'='round8-self-test.json'
}
$runs = @()
try {
  foreach ($flag in $checks.Keys) {
    $stem = $flag.TrimStart('-')
    $started = [DateTimeOffset]::Now.ToString('o')
    $arguments = '"' + (Join-Path $oldCode 'desktop.py') + '" ' + $flag
    $child = Start-Process -FilePath $python -ArgumentList $arguments -WorkingDirectory $oldCode `
      -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $case "$stem.stdout.log") `
      -RedirectStandardError (Join-Path $case "$stem.stderr.log")
    if (-not $child.WaitForExit(90000)) { $child.Kill(); throw "Timeout: $flag" }
    $child.Refresh()
    $marker = Test-Path -LiteralPath (Join-Path $env:FINANCE_DESKTOP_DATA_DIR $checks[$flag])
    $runs += [pscustomobject]@{Flag=$flag;PID=$child.Id;Started=$started;Ended=[DateTimeOffset]::Now.ToString('o');Exit=$child.ExitCode;Marker=$marker}
    $runs | ConvertTo-Json -Depth 3 | Set-Content -LiteralPath (Join-Path $case 'processes.json') -Encoding utf8
    if ($child.ExitCode -ne 0 -or -not $marker) { throw "Self-test failed: $flag" }
  }
} finally {
  $env:FINANCE_DESKTOP_DATA_DIR = $oldData
}
```

## 关口保持

### 后台续查（用户正在看视频期间）

本轮没有发送鼠标键盘输入、激活窗口、启动可见测试界面或请求 UAC，也没有启停 Procmon 采集。产品源码和候选包哈希未变；以下是新增的诊断证据，不是重新执行了501项回归。

**找回了原命令中的一个覆盖缺口**：M2-2/M2-3逐进程使用的是 PowerShell `*>>` **追加**重定向，而前轮合并对照是 `*>` **覆盖**。原命令输出、退出码及验收标志见[历史执行记录摘录](evidence/m2-warning-background/original-invocations.json)，没有把工具日志中的既有结论当作新运行结果。

随后比较 append/overwrite/split 三种方式，每种分别跑标准输出/错误正控制、无窗口 Tcl、desktop仅导入、M1a服务自检、第八轮服务自检：**15次均退出0，无目标警告**。[无窗口对照](evidence/m2-warning-background/no-window-controls.json)

原先的“完整GUI自检 + `*>>`”组合本轮尚未覆盖。隔离桌面权限探测返回 WinError 5，因此没有退回可见GUI测试。Procmon进程仍存在，但本轮未核实采集状态，也未声称获得PML/调用栈。

**新的最强来源候选是本机搜狗组件**，理由如下：

- 本机五个搜狗二进制中存在完整相同的 `can't open file for write !` 字符串：SGTool.exe、userNetSchedule.exe、PicFace.dll、PicFace64.dll、SGPicFaceTool.exe。相邻常量包括配置头/校验等写入错误，但没有据此猜测具体失败路径。
- 已对SGTool.exe、userNetSchedule.exe、PicFace64.dll三个文件核验有效的搜狗数字签名、版本和SHA256；只读检查，未加载或手动运行这些组件。[文件身份、偏移及脱敏记录](evidence/m2-warning-background/input-method-candidate.json)
- 项目既有debug.log在两次原运行附近出现含sogou的`kernel_parameter_setter.cc`、`t_skinfilemap.cpp`记录；原始输入法日志未上传。
- 两份警告日志最后写入时间分别在GUI成功标志之后约347.589ms、358.415ms，而M1a成功标志分别再晚约1083.669ms、1065.222ms。另测无输出子进程对已有文件执行`*>>`不会改变其内容或修改时间。[时序及控制](evidence/m2-warning-background/historical-timing.json)

**这些仍不构成运行时归因**：常量存在不等于该DLL当时输出了它；日志修改时间也不是进程起止时间，无法单独区分GUI收尾与下一进程早期。当前表述是“强候选”，不是“已证明来自搜狗”，更不是“已经排除产品”。[后台续查汇总](evidence/m2-warning-background/results.json)

下一次可进行前台诊断时，优先在原GUI自检+追加输出组合中记录每个PID及起止时刻，结合Procmon确认实际发出模块、写入路径和拒绝原因。可准备只作用于诊断子进程的IME对照，必须在创建窗口前设置，并记录是否真的改变了加载模块；不改变用户全局输入法或客户端中文录入功能。微软说明[ImmDisableIME](https://learn.microsoft.com/en-us/windows/desktop/api/Imm/nf-imm-immdisableime)作用于线程/进程且须在首个顶层窗口创建前调用；具体对照尚未执行，不能据文档宣布验证通过。

M2-5 的 501 项回归及八组实际 EXE 结果仍只覆盖 `75c9ce5` 工程候选。本次没有产品源码变化，不需要假装又做了一次完整回归；本次新增的是诊断证据。当前候选产物哈希继续匹配原验收包，未替换稳定版。

尚未取得警告对应的失败路径或原生调用栈，因此不做产品补丁、不以“未复现”关闭警告。干净 VM 安装/升级/卸载、签名及 G-DATA 也未因本次排查完成；它们与警告归因分开记录。
