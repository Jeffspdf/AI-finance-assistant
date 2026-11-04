# 真实数据试验：采集与离线验证协议

日期：2026-11-04。状态：**未收集真实样本**。工具可用不等于G-DATA已完成。M2尚未实现，退款任务结果目前不能填写为已完成。

工具位于当前GitHub main源码中；已发布的v0.8.5安装包及源码附件保持原样，不含本轮新增采集工具。

## 使用

```powershell
.\.venv\Scripts\python.exe scripts\real_validation.py init
.\.venv\Scripts\python.exe scripts\real_validation.py evaluate
```

默认生成本机`.validation-private/`下manifest.json及10份**只有表头**的CSV；初始化不会覆盖已有表格。evaluate在同目录输出summary.json，只输出汇总，不调用网络/模型、不读取用户API配置、不发送文件。原始账单另存在这个忽略目录的raw子目录；不要把实际采集表移进docs/samples或其他公开目录。源码归档白名单不包含该私有目录。

可用`--directory`指定本机目录。示例路径和所有ID均应使用项目代号/稳定假名，不填姓名、卡号、手机号。脱敏需要保留跨文件可对应的稳定假名、金额关系与日期间隔，否则会破坏对账验证；脱敏密钥或对应表另行私有保存。

## manifest与数据性质

- schema_version固定1；dataset_id使用试验代号，application_version记录实际测的版本。
- data_kind为uncollected（未收集）、synthetic（练习）或deidentified_real（团队声明的脱敏真实样本）；files中每行必须一致。
- candidate_snapshot_ref记录冻结候选文件/版本的私有引用，candidate_snapshot_complete只有该范围的系统候选全量导出时才设为JSON true；50条抽样不能代替全量快照。不能把看到系统候选后的标注冒充盲扫。
- precision_sampling_plan记录预先随机/分层抽样方法、种子和范围；在知道人工标签之前选定precision_sample，不按真假标签凑比例。
- ground_truth_scope明确人员、月份、账户、完整交易快照和纳入/排除范围。只有两位独立扫描完整交易且仲裁后，才把independent_scan_declared_complete设为JSON布尔true。
- 真实性、授权和标注质量不能靠一个字段由工具验证；external_authenticity_verified始终false，automatic_real_validation_pass始终false。

## 十份表

| 表 | 内容与填写约定 |
|---|---|
| files.csv | 一份导出一行。file_id/提供者假名、wechat/alipay/bank、银行代号/格式版本、数据性质、同意记录引用、parsed/failed/excluded、人工确认有效行数与解析行数。mapping_seconds为实际格式准备/列映射耗时；不适用留空，实际无需映射填0。程序当前没有独立手动列映射界面，不把整段解析等待时间冒充手动映射。 |
| transactions.csv | 本次完整判重范围的稳定假名交易ID、file_id、收入/支出/转账、ordinary/refund/transfer。无需金额/商户等原文进入汇总工具；完整脱敏账单供人工盲扫在私有目录保存。有效行数与去重后交易ID数量不一致时，在scope中解释。 |
| candidates.csv | 冻结的一次全量候选快照，左右交易ID、purchase_duplicate/refund_duplicate、A/B/C、算法版本、run_id和precision_sample标志。同一无序交易对只记一次；随机精度抽样至少50条，候选不足50时全标。其余候选仍保留用于召回，不混入多次运行或把人工确认等同于系统自动判定。 |
| ground_truth.csv | 左右ID、关系类型、两位不同标注者及各自duplicate/not_duplicate标签、仲裁者、final_label。两人不一致时，第三位仲裁并记录；未完成最终标签留空，不凑数。 |
| scan_coverage.csv | 每交易×每标注者一行，reviewed和blind_to_candidates。标注者先看完整交易快照，不看系统建议，记录各自发现的所有真实重复；候选快照由另一人冻结保存。之后再核对候选、补漏检/负例与仲裁。 |
| classifications.csv | 标注样本交易ID、系统预测、automatic、双人标签/仲裁/最终分类，以及实际用户是否修改user_corrected。最后一项未观察时留空；不能用系统错误率代替用户实测修正率。收入/支出类别需合法。 |
| qa_checks.csv | 问题代号、预期/实际answer或refuse、人工标准值、实际值、证据重算值、cents/count/ratio_bps、预期/实际口径、以竖线分开的来源ID和复核者。金额类默认整数分；现金月负值合法。未核对就不标完成。 |
| user_tasks.csv | 被试假名、import/insight/budget_goal/question/refund、完成情况、任务耗时、首次洞察耗时和是否阻塞。首次洞察从“确认导入完成”计到“首次看到并理解可追溯洞察”，与选文件到确认导入的整体交互耗时分开。 |
| import_trials.csv | 同一个file_id的不同trial_id，实际解析/提交总耗时和最大UI冻结时长。每文件不足20次只显示观测最大值，p95不输出；20次起采用nearest-rank p95，须同时记录硬件、冷热启动等条件于notes。 |
| issues.csv | 问题代号、P0/P1/P2/P3、open/fixed/accepted及是否阻塞任务。详情单独私有保存，不能以勾选accepted掩盖仍存在的P0/任务阻塞。 |

布尔字段用true/false、yes/no或1/0，不能把字符串false当真。计数/耗时须非负有限值；问答带符号整数允许负值。

## 指标边界

- 导入按来源/格式分别报告文件成功率与有效行覆盖率；8份里1份失败只是小样本事实，不将10%触发线当稳定总体估计。
- 候选precision只使用预先选定precision_sample中已完成的标签；不能把盲扫补齐的全部真阳性和只抽部分的负例直接混合算precision，否则会偏高。报告同时列全量候选数、抽样标签覆盖率、样本量和Wilson95区间；额外50条边界负例是挑战集，不人为凑成总体正负比例。
- recall只有系统全量候选声明、完整独立盲扫声明、两位扫描覆盖全快照、范围/候选快照引用及最终标签齐备时输出；分母来自人工全部真实重复，包括系统漏检。分子在全量候选中核对，不能只拿精度抽样求召回。仍是该范围内、以人工真值为准的召回。
- all_levels与confirmable_A_B分别统计，消费与退款关系另列。单位是无序交易对；三成员事件的多个对不能当作多个独立消费。当前系统只生成候选、由人确认，指标不叫自动合并正确率。
- 分类覆盖率/准确率限定已完成双人标注的样本；预测待确认和人工分类不混为自动分类。总体标签覆盖率、自动样本量、用户修改观察数分别报告。Wilson区间仅是二项近似，非随机或同人交易相关时不能解释成普遍精度保证。
- 问答必须动作正确、来源ID存在、标准值=实际值=证据重算值、口径一致，才计为复核有依据；有链接不等于有依据。另统计应拒答中的正确拒答。
- 5人是小规模定性试验，只记录任务障碍/实测耗时，不推断满意度、市场有效性或普遍可用性。

工具不会凭空生成TP/FP、标注者、数据或用户时间。无数据时比例为null而非0%/100%，状态为awaiting_data；synthetic只输出synthetic_practice_only。真实声明但协议不齐为incomplete_pilot；齐备后仍只可进入人工验收，不自动宣布通过。还要由团队审阅指标、证据及阻塞问题，M2工程关口独立验收。

## 团队采集清单

准备3–5位自愿数据提供者与8–12份脱敏导出，覆盖微信/支付宝/1–2家银行、跨月、退款、转账、异常行。使用代号记录告知内容、范围、同意记录引用、退出联系途径与保留/删除安排；原文件不进公开仓库、截图或日志。团队自行组织参与者，本工具不会代为发送招募消息。

两位独立标注者与第三位仲裁者由团队实际安排，不由AI编造。完成200笔分类和判重盲扫后，再用5位目标用户跑完整任务；暂缺人员/文件就如实记录未完成。取消参与、删除原文件/对应表与保留汇总的实际处理也要记录，不能只写一句“已脱敏”。

## 当前准备状态

离线指标工具新增13项合成单元测试通过，包含全量候选/精度抽样分母分离；最终完整回归260项（原247＋新增13）通过，20.86秒。空采集目录已实际初始化并评估，输出awaiting_data。这些合成用例只验证计算与门槛，不是一次真实试验，也不是M2退款业务测试。
