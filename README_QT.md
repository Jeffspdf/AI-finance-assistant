# AI理财小助手 · PySide6 预览

课程提交基线是 Tk `v0.10.0-rc.4`。独立 Qt 客户端位于 `prototype/pyside6-finance`。Windows 便携包解压后，双击文件夹内的 `AIFinanceHelperQt.exe`。

首次启动打开演示空间。默认数据目录是 `%LOCALAPPDATA%\ShiguangLedger-QtPreview`。要核对 Tk 中已有的记录，先在 Tk 客户端创建备份，然后在 Qt 里切到目标空间，通过「备份与恢复」恢复那份备份。

「设置与连接」使用与 Tk 相同的模型协议和 Windows DPAPI。填写同一 Base URL、Model ID 和 API Key，测试后保存。Qt 独立保存配置，开启模型后仍需在对话或规划里授权。更换接口地址时会清空表单中的密钥。

## 试用

- AI 对话显示已导入月份、来源记录数和查询范围。可以问「2026-10 星巴克花了多少？」「2026年8月的收入和净支出分别是多少？」或填写明确的起止日期。部分中文数字月份和餐饮问法也可在本地解析。只说「十月份」且账本中有多个年份的十月时，需要补充年份。复杂净支出问法可点击「补充条件」，检查商户和统计日期后再查询。
- 侧栏「历史会话」可在当前空间搜索标题和消息，查看最近一条消息并切换会话。演示与个人空间各保留最多 15 个会话。开启「本机保存对话文本」后，该空间可跨重启恢复。文本可能包含问题里的商户和回答中的金额。完整证据、源文件和行号只在本次运行中可查看。关闭此设置并确认可删除本机历史文件。
- 编辑并删除交易后重新导入相同来源文件时，预览仍会列出已删除行。默认保持删除。显式勾选恢复后，使用原始账单内容恢复并沿用原交易 ID。
- 「导入批次」中的「撤销选中导入」会先显示受影响笔数，保留导入历史、原始记录和审计。
- 财务概览、洞察、账本导入预览、交易增改删、批量分类、预算建议、账户绑定、跨源对账、退款去重与关联、定点修复和备份均调用共享服务。
- 财务概览支持按月切换、净支出趋势与分类图，并显示超支和储蓄目标提醒。预算可打开对应净支出、公式和来源。导入时可新建并选中来源账户，预览和批次历史均可导出异常 CSV，账本净额导出使用选中的月份。
- 储蓄规划支持历史账单或手动假设，调用原 A/B/C Agent 和本地计算，展示工具轨迹。可以把选中方案填入目标表单，确认保存后才会写入账本。
- 手动 A/B 可分别填写两套月预算和确切的开始日、截止日，也能从近三个完整月估算并预填。
- 演示空间支持六笔对账示例及按月份生成合成账单。桌面小助手支持拖动、点击招手和暂停动画。`Ctrl+K` 搜索或跳转页面，`Ctrl+N` 快速记账。

Qt 检查更新使用独立发布通道。安装版下载 SHA256 校验通过的安装包，由用户确认关闭 Qt 并覆盖到当前程序目录。便携版下载 ZIP，解压后启动。Qt 预览版可检查较新的 Qt 预览或正式版。Qt 正式版仅检查 Qt 正式更新。

安装向导默认在 Windows「已安装的应用」中注册。在限制写入卸载注册项的环境，勾选 `Install without a Windows Installed Apps entry`。该选项保留安装目录中的卸载程序和开始菜单的卸载入口。升级会沿用该模式。

本版仍是迁移预览。AI 对话保持只读。Windows 原生测试和真实模型联调都使用合成账本。真实模型联调需另行提供已启用的本机配置目录，联调进程只读取其中的模型设置。

## 从源码运行

在工作树中用 Python 3.12 建立虚拟环境，安装 `requirements-qt.txt`，然后运行：

```powershell
python qt_desktop.py
```

测试与打包：

```powershell
python scripts/verify_qt_native.py .test_qt/source150 --scale 1.5 --tk-migration
python scripts/verify_qt_native.py .test_qt/source200 --scale 2 --tk-migration
./packaging/build_qt.ps1 -Python python
```

直接运行打包自检时，可以指定输出目录，例如 `AIFinanceHelperQt.exe --self-test C:\temp\qt-self-test`。单独使用 `--self-test` 时，结果写入系统临时目录下的 `AIFinanceHelperQt-self-test`。

打包脚本使用 PyInstaller 生成 Qt 独立目录。打包 EXE 在 150% 和 200% 下通过自检及 Tk → Qt → Tk 双向备份后，生成带版本号的便携 ZIP。传入 `-Iscc` 可同时生成独立的 Qt 用户级安装包。程序、账本和配置使用各自的目录。

可选的真实模型 L1 单独运行，使用独立输出目录。查看结果中的 `status`、`fallback` 和 `model_calls`。

```powershell
python scripts/verify_qt_native.py .test_qt/live-qt --scale 1.5 --exe dist/AIFinanceHelperQt/AIFinanceHelperQt.exe --live-model-settings "$env:LOCALAPPDATA\ShiguangLedger-QtPreview"
```
