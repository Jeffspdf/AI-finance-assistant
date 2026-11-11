# AI理财小助手 · PySide6 预览

独立客户端位于 `prototype/pyside6-finance`，从 rc.4 派生。Windows 便携包解压后，双击文件夹内的 `AIFinanceHelperQt.exe`。

首次启动打开演示空间。默认数据目录是 `%LOCALAPPDATA%\ShiguangLedger-QtPreview`。要核对 Tk 中已有的记录，先在 Tk 客户端创建备份，然后在 Qt 里切到目标空间，通过「备份与恢复」恢复那份备份。模型需在 Qt 设置中自行配置。

## 试用

- AI 对话支持本地查询、计算依据、问题级模型授权和停止等待。演示与个人对话各自保留在运行期间。
- 财务概览、账本导入预览、交易增改删、预算、账户绑定、跨源对账、退款关联及备份恢复均调用共享服务。账本原始行、净额与预算统计使用各自的原有数据入口。
- 储蓄规划支持历史账单或手动假设，调用原 A/B/C Agent 和本地计算，展示工具轨迹。单独的「目标与存款」窗口用于手工保存目标和实际存款。

本版仍是迁移预览。桌面悬浮助手、自动更新、批量分类和退款去重建组交互仍在 Tk 客户端。Windows 原生测试使用合成账本，本次模型联网未实测。

## 从源码运行

在工作树中用 Python 3.12 建立虚拟环境，安装 `requirements-qt.txt`，然后运行：

```powershell
python qt_desktop.py
```

测试与打包：

```powershell
python scripts/verify_qt_native.py .test_qt/source150 --scale 1.5
python scripts/verify_qt_native.py .test_qt/source200 --scale 2
./packaging/build_qt.ps1 -Python python
```

打包脚本使用 PyInstaller 生成 Qt 独立目录，先运行打包 EXE 自检，再压缩到 `release/AIFinanceHelperQt-preview-Windows-x64.zip`。
