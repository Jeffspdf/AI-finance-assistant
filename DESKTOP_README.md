# 拾光账本桌面原型

双击 `start_desktop.bat` 启动 Windows 桌面窗口。现有网页版本不受影响。

本版采用 Python 自带 Tkinter（无需安装 PySide6），复用 finance 业务模块、pandas 与 SQLite；不需要启动网页服务。其他电脑需安装 requirements.txt 中的数据处理依赖及带 Tk 的 Python。

功能：财务概览、CSV/XLSX 导入与重复检测、分类修正、来源追溯、双方案储蓄比较、计划保存、本地规则问答及逐笔依据。

数据库位于 `%LOCALAPPDATA%\ShiguangLedger`，可通过 FINANCE_DESKTOP_DATA_DIR 覆盖。演示空间首次加载 100 笔合成数据；个人空间初始为空。两空间数据隔离，导入前请确认当前空间。

助手未接入大模型，明确采用本地规则解析和代码计算。未包含登录、云同步、后台通知和安装包。保存的计划目前可在数据库查看，尚未提供历史计划页面。

开发启动：`.venv\Scripts\python.exe desktop.py`

验证：四个页面创建、交易选择、规划计算、问答与来源的界面冒烟检查通过；已有核心回归测试 47 项通过。
