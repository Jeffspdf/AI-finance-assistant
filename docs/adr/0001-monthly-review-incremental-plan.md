# ADR-0001：账户、对账与证据规则（A1修订）

状态：修订提案，以下数据模型尚未实施。日期：2026-11-01。基线：v0.7.1 / fa84b40。

依据用户提供的ADR修订说明，替代初稿相应口径。关联：[差距分析](../DIRECTION_GAP_ANALYSIS.md)、[一页计划](../A1_IMPLEMENTATION_PLAN.md)。用户本轮明确要求的API密钥持久化作为独立修复先行交付，不扩大为整套对账重构。

## 1. Account与FundingLink先于自动关联

Account(id,channel,type[wallet/debit/credit],label,last4,created_at)，ID由应用生成，last4仅显示，不作唯一身份。相同银行不代表相同账户；新交易身份包含account_id，旧ID/signature保持不变。

FundingLink(id,wallet_account_id,bank_account_id,active_from,active_to,created_at)。设置页由用户声明绑定及有效期，不采集银行密码或完整卡号。链路未声明、过期或存在冲突时，均不自动关联。

| 等级 | 条件 | 首版行为 |
|---|---|---|
| A | 有效绑定、同额同向、时间差≤2分钟、银行描述匹配相应财付通/支付宝支付通道、候选唯一 | 高置信候选，默认仍需确认；自动关联开关关闭。独立真实标注precision≥99%是讨论默认开启的必要条件，不单独充分。 |
| B | 同额、日期±1天、商户/描述相似 | 人工单笔/批量确认。 |
| C | 仅同额/同日 | 不主推；必要时标“可能相关”，不影响统计。 |

自动关联若将来启用，仍须记录决定、算法版本及撤销入口。信用卡还款、余额充值/提现进入转账识别，不参加普通消费去重；无法确定的类型先复核，不能只按关键词悄悄改类型。

## 2. 关联组硬规则

1. purchase_duplicate组必须同币种、同整数分金额、同方向。金额不一致只作待核对线索，不能确认成消费排重组，不改写差额。
2. 代表优先：有商户信息的钱包purchase记录（微信/支付宝）>商户级记录>银行记录；同优先级取最早导入，再以稳定ID排序。银行funding腿不再单独进入分类统计。
3. 成员只属于一个有效确认组，不做传递闭包。三成员逐个与代表校验并明确确认，不能由A~B、B~C推断A~C。两个不同钱包的同额消费不能自动合并。
4. 转账、退款、信用卡还款永不进入消费排重；transfer_pair/refund_pair走独立规则，不套用消费组同向约束。
5. 只有status=confirmed且kind=purchase_duplicate影响消费单次计数；候选、拒绝、拆分不改变金额。有效组校验失败时退出投影并提示复核。
6. 账本保留所有来源行，标明代表/资金腿/是否计入统计。概览、账本摘要、预算、洞察、问答、规划及旧Web原型共用一个统计投影，禁止各页各自过滤。
7. 拆分/撤销后所有入口同步恢复计数和证据；排除决定持久化，重跑不覆盖人工决定；关键字段编辑触发重新核对。

微信60+银行60：120→确认60→拆分120；微信60+银行59.99不能形成消费排重确认组；微信60+支付宝60+银行60仅在逐成员证据充分且明确确认后才成组三成员。同额同日同商户的两笔真实消费必须覆盖负样本。

## 3. 退款里程碑与口径

M1a保留退款status和来源，展示“退款待处理¥X”，不静默跳过，也不在未确认时抵消。M2/A7实现refund_links、完整/部分/多次退款关联及口径选择。

优先按原交易单号+账户匹配，缺单号再给商户/金额/日期候选，人工确认。累计确认退款不得超过原消费可退额；超额、币种冲突、原单缺失进入复核。保留消费及所有退款行，退款不参加消费去重。

- 默认现金口径：当月净支出=当月消费−发生在当月的已确认退款；可为负，退款不当工资等普通收入。
- 原消费月口径：确认退款回溯冲减原消费月并标明重述时间，发生月不再重复抵扣。
- 切换只改变投影，不改原始日期/金额，所有页面同步采用所选口径。

100−100=0、100−30=70；9月消费10月退款时，两种口径分别调整10月/9月，均有消费、退款和关联决定证据。

## 4. 可实现的版本与恢复保证

只能保证**修复后的新程序**发现user_version高于自身支持时拒绝写入并提示升级。不能让已分发旧二进制获得此能力；既有0.7.1可能覆盖user_version并忽略新表语义，因此不支持直接降级，使用对应版本备份恢复。

schema_migrations(version,applied_at,checksum)记录迁移，配合user_version。M1a做有限已知版本升级，先不造通用框架。迁移前SQLite backup()一致快照、integrity_check、关键表计数；迁移及版本更新原子完成，失败不留半套模式。M1a有基本故障回滚测试，M1b扩展故障注入矩阵。

恢复前预览版本/时间/空间/计数并由用户确认，先自动快照当前库。保留历史ID、金额、分类及预算/目标/存入；缺原始文件标legacy，不伪造raw_json/哈希。演示/个人空间分别验证。

## 5. 最小模型、隐私与复杂度

M1a仅新增：

```text
accounts(id PK, channel, type, label, last4, created_at)
funding_links(id PK, wallet_account_id, bank_account_id, active_from, active_to, created_at)
source_documents(id PK, sha256, filename, channel, account_id, parser_version, imported_at)
raw_rows(id PK, document_id, row_no, raw_json, parse_status)
import_batches(id PK, document_id, started_at, finished_at, status, added, duplicates, conflicts, issues_json)
link_groups(id PK, kind, status, reason, confidence, algorithm_version, created_at, decided_at, decided_by)
link_members(group_id, transaction_id, role, PRIMARY KEY(group_id, transaction_id))
schema_migrations(version PK, applied_at, checksum)
transactions增加source_document_id、raw_row_id、account_id、link_group_id、currency、status
```

kind=purchase_duplicate/transfer_pair/refund_pair，role=purchase/funding/refund。link_group_id与成员关系同事务维护，有效成员跨组唯一性需约束/事务测试。M2/A7再新增refund_links(refund_txn_id,original_txn_id,amount_cents,basis,created_at)，金额为正整数分分配，禁止重复分配/超额。

RawRow只存追溯必要字段，不默认存原文件字节，无关敏感字段剔除；数据/审计留本机，不进公开仓库。批次提供保留与删除策略：先撤销/软删除并刷新关联/投影，物理清理明确范围，保留审计不能被称为隐私已彻底清除。

候选按账户/币种/方向/金额/日期分桶及索引，只查±1天，每笔候选上限20，溢出提示需进一步筛选。禁止全表两两比较；分桶有序/索引构建加有界比较约O(n log n+n·k)，k≤20，记录算法版本与阈值。

## 6. 最小证据和评测

先做result_type、formula_id、inputs、evidence[]、computed_at、algorithm_version；Evidence区分Transaction/Budget/Goal/Contribution/LinkDecision。仅预算/目标存参数快照，暂不做全量CalculationTrace。异常卡带本期+历史均值来源，目标预测带目标参数+实际存入+日期假设；旧ids/evidence通过适配器兼容。

50正+50负，负样本含不同商户同额同日、同商户不同日、同商户同额两笔真实消费、退款/转账与消费、三成员冲突。真实样本双人标注+仲裁，记录一致性、阈值、PR曲线及P/R；开发与评估分离，A层另测99%目标。合成回归不用于真实准确率声明。

## 7. 分阶段实施

M1a：最小模式/一致备份/有限迁移→账户链路→微信CSV+指定银行模板XLSX→候选/确认/拆分→统一投影→最小证据。未指定银行前只承诺通过夹具的模板。M1b：支付宝、三格式golden/20类边界、完整迁移与故障矩阵、5万行性能/取消。M2：退款净额和正式对账评测。

六行演示固定：钱包20+银行20、钱包40+银行40、待处理退款10、转账500。消费排重120→60→120，退款10单独提示、转账500不计消费；不能把待退款调整的120/60称为最终净支出。

保留Windows/Python/Tkinter/SQLite、整数分、规则优先、显式商户学习与模型白名单。按用户本轮要求，密钥独立改为可选Windows当前用户加密保存及启用状态恢复，启动不请求模型，保留清除和仅本次使用，不把密钥放进原始行或公开文档。

本轮A1为文档，密钥为单独授权修复；账户/导入/对账/退款按后续阶段小步实施，不以一次大改替换全链路。
