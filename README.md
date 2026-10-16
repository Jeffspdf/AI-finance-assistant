# AI 个人理财助手

## Windows 桌面版（新增）

普通用户请从 [GitHub Releases](https://github.com/Jeffspdf/AI-finance-assistant/releases/latest) 下载 `AIFinanceHelper-0.2.0-Setup-x64.exe`，安装后通过桌面快捷方式或开始菜单启动，无需安装 Python。本地安装包位于 `release/`。不要把 GitHub 的 Download ZIP 源码包当作安装包。

桌面版支持独立演示/个人空间；数据位于 `%LOCALAPPDATA%\ShiguangLedger`，安装更新与卸载默认保留数据。当前助手为本地规则模式。Windows x64，尚未签名。

开发与发布方式见 `WINDOWS_RELEASE.md`。下面原有说明适用于 Streamlit 网页版。

按《提案大纲》实现的本地中文应用。技术栈为 Streamlit、Python/Pandas、SQLite；可选接入兼容 Chat Completions 的模型。仅支持人民币。

## 在这台电脑上运行

双击 `start.bat`，或在 PowerShell 中运行：

```powershell
cd "C:\Users\23076\Desktop\计算机软件概论\finance_assistant"
.\.venv\Scripts\python.exe -m streamlit run app.py --server.address 127.0.0.1 --browser.gatherUsageStats false
```

打开 http://127.0.0.1:8501 。关闭终端或按 Ctrl+C 停止服务。应用绑定本机地址，请勿直接用于公网服务。

## 在其他电脑上安装

需要 Python 3.11 或 3.12。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m streamlit run app.py
```

测试：`.\.venv\Scripts\python.exe -m pytest -q`。

当前已验证版本完整锁定在 `requirements-lock.txt`，需要重现相同环境时用它替代 `requirements.txt`。

## 六步演示

1. 默认进入“演示数据”，合成 100 笔最近三个月的记录。演示与个人账单使用独立 SQLite 文件。
2. “导入与分类”下载并重新导入 `demo_with_errors.csv`。展示非法金额、错误日期、外币、空商户描述的异常报告，以及重复导入计数。已存在交易不重复保存。
3. 查看月度趋势、分类占比和结余。修改某笔分类并保存，汇总随之更新。
4. “储蓄方案”：目标 6000 元，已有 0 元，每月收入 5000 元，开始日之后六个月截止。A 预算 4200 元，到期 4800 元，差 1200 元；B 预算 3800 元，到期 7200 元，可以达成。
5. 问“上个月餐饮花了多少？”并检查交易 ID、日期、金额和源文件行号。2026-10-09 的样例回答为 145.00 元。
6. “预算与提醒”查看上个月餐饮预算 100 元，已支出 145 元，超支 45 元。

静态样例的日期为 2026 年 7 至 9 月。首次打开空演示空间时自动生成的样例随当前日期滚动；未来使用时，可执行 `python -m finance.demo` 调整生成脚本日期后重新制作静态样例。样例用于流程验收，不代表真实银行账单和真实用户分类准确率。

## 导入约定

- CSV 支持 UTF-8、UTF-8 BOM、GB18030；Excel 支持 `.xlsx` 第一个工作表。表头可位于前 60 行，适配常见微信、支付宝字段以及标准模板；不保证任意银行导出格式都可直接导入。
- 必填：日期、金额，商户或描述至少一项。建议填写收支、交易 ID、平台和币种。列名别名见 `finance/importer.py`。
- 收支类型：收入、支出、转账。金额按绝对值入账；缺少类型时，正金额按收入、负金额按支出，并显示提示。请勿直接使用“正数支出且无类型”的账单。
- 日期范围 2000 至 2100 年，使用本地无时区日期时间；不猜测 Excel 数字序号。人民币金额最多两位小数，以整数“分”存储和汇总。
- 有原始交易 ID 时按“平台 + ID”去重，同 ID 内容冲突单独报告。无 ID 时按日期时间、金额、类型、商户、描述、平台组合去重。组合相同的真实多笔交易可能被合并，需人工核对；不同平台间的同一实际支付不会盲目合并。
- 失败、关闭、撤销或完全退款状态跳过；退款及部分退款记录列为待人工处理。已导入交易不因后来导入退款自动冲销。涉及退款时应先整理净账单，避免误判收支。
- 最多 20 MB、50000 行；异常行不入库，有效行可继续保存。
- 转账不算收入支出，结余不是账户余额。完整历史须自行导入，系统不能判断缺失交易。

## AI 与隐私

默认离线：规则分类、约束问题解析、模板解释，不向模型发送任何请求。Streamlit 使用统计已关闭，界面使用内置字体。浏览器访问本机服务是本地通信。

可选 AI 配置在侧栏。可填本机兼容服务的 `http://localhost:11434/v1` 或 HTTPS 远程兼容接口，提供模型名与 API Key，并勾选启用和数据发送许可。也可设置 `FINANCE_LLM_BASE_URL`、`FINANCE_LLM_MODEL`、`FINANCE_LLM_API_KEY` 环境变量来预填配置，仍需界面启用。

- 分类仅发送待确认记录的商户、描述、收支，每批最多 20 笔，结果标注“模型建议（需核对）”；规则与用户修正不被模型覆盖。
- 问答仅发送问题文本，用于获取有限的查询意图；模型不接收完整账单、不负责计算、不生成金额。问题本身也可能含敏感信息，请自行决定是否发送。
- AI 返回的分类、查询类型和日期格式须验证；接口失败回退或说明无法理解，不补造答案。
- 有依据问答覆盖月份、收入、支出、结余、笔数、最高支出分类、占比和预算。无法可靠解析的商户查询、日期段、分类比较等会提示改写，不能将其误当成全部支出。不存在记录时明确说明不足。
- 本机模型与云模型均需自行安装或获得凭据；项目不内置密钥。不包含银行接口、投资建议、OCR 或后台推送。

记录保存在 `data/demo.sqlite3` 与 `data/personal.sqlite3`，可用 `FINANCE_DATA_DIR` 指定目录。预算和目标持久化；备份前停止应用并复制数据库。API Key 仅在当前进程会话使用，不写入 SQLite。

## 计算假设

目标规划使用固定月收入和固定总支出预算，无利息、投资收益或不确定收入。每月可存为 `max(收入 − 预算, 0)`。在开始日之后每个月同日存一次，短月取月底；截止前没有完整存入日则不计新增储蓄。每月需存向上取整到分，已有存款视为专用于该目标。负现金流明确提示，不假设新增储蓄为负数。

## 项目结构与验收

```text
app.py                 中文界面与完整流程
finance/importer.py    字段映射、清洗、异常与导入预览
finance/categories.py  分类规则
finance/storage.py     SQLite 交易、预算、目标
finance/analytics.py   确定性汇总、预算、目标规划
finance/qa.py          有依据问答与全部证据
finance/llm.py         可选模型分类与问题理解
finance/demo.py        100 条标注样例生成
samples/               模板、标注集、异常账单
tests/                 导入、计算、问答、隐私和界面测试
```

US1：导入 CSV/Excel，查看并修正分类。US2：持久化存钱目标，调整金额、日期与预算并比较两方案。US3：由记录回答问题、查看源证据、收到应用内超预算提醒。

测试包含 100 条标注数据分类门槛、20 个问题的逐条源证据核对、汇总/趋势/预算/储蓄计算、重复导入和冲突、非法金额/缺列/日期/外币、持久化及默认模式禁止外部连接。合成数据验证属于功能回归，真实准确率还需用独立真实标注集评估。

Streamlit 控件与会话状态参考官方文档：
https://docs.streamlit.io/develop/concepts/design/dataframes
https://docs.streamlit.io/develop/api-reference/caching-and-state/st.session_state
