# AI理财小助手 · PySide6 预览

课程提交基线是 Tk `v0.10.0-rc.4`。独立 Qt 客户端位于 `prototype/pyside6-finance`。Windows 便携包解压后，双击文件夹内的 `AIFinanceHelperQt.exe`。

首次启动打开演示空间。默认数据目录是 `%LOCALAPPDATA%\ShiguangLedger-QtPreview`。要核对 Tk 中已有的记录，先在 Tk 客户端创建备份，然后在 Qt 里切到目标空间，通过「备份与恢复」恢复那份备份。

「设置与连接」使用与 Tk 相同的模型协议和 Windows DPAPI。填写同一 Base URL、Model ID 和 API Key，测试后保存。Qt 独立保存配置，开启模型后仍需在对话或规划里授权。更换接口地址时会清空表单中的密钥。

## 试用

- AI 对话支持本地查询、计算依据、问题级模型授权和停止等待。演示与个人对话各自保留在运行期间。
- 财务概览、洞察、账本导入预览、交易增改删、批量分类、预算建议、账户绑定、跨源对账、退款去重与关联、定点修复和备份均调用共享服务。
- 财务概览可切换月份并从分类进入账本。预算可打开对应净支出、公式和来源。导入时可新建并选中来源账户，预览和批次历史均可导出异常 CSV，账本净额导出使用选中的月份。
- 储蓄规划支持历史账单或手动假设，调用原 A/B/C Agent 和本地计算，展示工具轨迹。可以把选中方案填入目标表单，确认保存后才会写入账本。
- 手动 A/B 可分别填写两套月预算和确切的开始日、截止日，也能从近三个完整月估算并预填。
- 演示空间支持六笔对账示例及按月份生成合成账单。桌面小助手支持拖动、点击招手和暂停动画。`Ctrl+K` 搜索或跳转页面，`Ctrl+N` 快速记账。

Qt 检查更新使用独立发布通道，读取 `qt-vX.Y.Z` 对应的 Qt 便携包并校验 SHA256。便携包下载完成后需手动解压启动。

本版仍是迁移预览。Tk 的图表、概览预算和目标进度提醒尚未迁移。AI 对话保持只读。Windows 原生测试和真实模型联调都使用合成账本。真实模型联调需另行提供已启用的本机配置目录，联调进程只读取其中的模型设置。

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

打包脚本使用 PyInstaller 生成 Qt 独立目录。打包 EXE 在 150% 和 200% 下通过自检及 Tk → Qt → Tk 双向备份后，才压缩到 `release/AIFinanceHelperQt-preview-Windows-x64.zip`。传入 `-Iscc` 可同时生成独立的 Qt 用户级安装包。安装程序目录与 Tk 分开，账本和配置目录不受卸载影响。应用内更新目前使用 Qt ZIP 通道。

可选的真实模型 L1 单独运行，使用独立输出目录。查看结果中的 `status`、`fallback` 和 `model_calls`。

```powershell
python scripts/verify_qt_native.py .test_qt/live-qt --scale 1.5 --exe dist/AIFinanceHelperQt/AIFinanceHelperQt.exe --live-model-settings "$env:LOCALAPPDATA\ShiguangLedger-QtPreview"
```
