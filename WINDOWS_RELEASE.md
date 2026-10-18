# Windows 安装与 GitHub 发布

## 普通用户

打开仓库 Releases，选择对应版本，在 Assets 下载 `AIFinanceHelper-0.4.2-Setup-x64.exe`。双击安装，可创建桌面快捷方式；开始菜单和 Windows 已安装应用均可找到程序。无需 Python。ZIP 为便携程序目录，必须完整解压后启动其中的 EXE，不能只复制 EXE。

安装界面暂为英文，应用界面为中文。程序为课程原型，未购买代码签名证书，可能显示未知发布者。数据保存在 `%LOCALAPPDATA%\ShiguangLedger`，卸载不删除账单，更新使用同一个 AppId；不要手动删除数据目录。备份时先退出程序。

## 本地构建

需要 Windows x64、Python 3.12（带 Tk）、Inno Setup 6。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-desktop.txt
.\packaging\build.ps1 -Python '.\.venv\Scripts\python.exe' -Iscc 'C:\Program Files (x86)\Inno Setup 6\ISCC.exe'
```

产物：安装 EXE、便携 ZIP、SHA256SUMS.txt。打包只收集程序依赖，不包含 data/、.env 和个人账单。

## GitHub 项目准备

将此项目目录作为仓库根目录，包含 `.github/workflows/release.yml`、`packaging/`、`finance/`、`desktop.py`、requirements 和文档。不要上传整个上级课程目录，不要上传 .venv、tools、data、build、dist 或个人账单。发布源码 ZIP 使用白名单打包，避免夹带本地数据。

上传源码后可先手动运行 Actions 的 Windows release，下载 artifact 验证。之后创建并推送 `v0.4.2` 标签，工作流自动构建程序、执行 EXE 冒烟检查，并建立包含三个产物的草稿 Release。检查草稿内容后发布，用户即可下载。工作流运行依赖 GitHub Actions 可用额度与仓库权限；不要提交访问令牌。

目标仓库为 https://github.com/Jeffspdf/AI-finance-assistant 。未配置自动更新，用户下载新版安装包覆盖安装。

## 验收范围

已执行源代码界面冒烟和 47 项核心测试；安装包与 EXE 验证结果以本次交付说明为准。发布前还应在干净 Windows x64 机器验证安装、快捷方式、升级数据保留和卸载。本地测试不能替代该验收。

工具参考：[Inno Setup 官方下载](https://jrsoftware.org/isdl.php)。

## 应用内检查更新

侧栏底部“关于与更新” → “检查更新”。只请求官方仓库公共 Release 元数据，不携带账单或令牌。后台线程请求，8 秒超时，失败可重试。仅展示正式版本，按数字比较版本号；安装包下载限定本仓库的相应版本和文件名。浏览器下载后退出应用再运行安装程序。实现参考 https://docs.github.com/en/rest/releases/releases 。
