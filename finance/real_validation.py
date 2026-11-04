"""Offline pilot-study summaries. Declarations and samples never certify a product."""
from collections import defaultdict
from pathlib import Path
import csv,json,math
from statistics import median

TABLES={
    'files':['file_id','participant_id','source','bank_alias','format_version','data_kind','consent_ref','status','expected_rows','parsed_rows','mapping_seconds'],
    'transactions':['transaction_id','file_id','kind','record_type'],
    'candidates':['left_id','right_id','relation','level','algorithm_version','run_id'],
    'ground_truth':['left_id','right_id','relation','labeler_a','label_a','labeler_b','label_b','adjudicator','final_label'],
    'scan_coverage':['transaction_id','labeler_id','reviewed','blind_to_candidates'],
    'classifications':['transaction_id','predicted_category','automatic','labeler_a','label_a','labeler_b','label_b','adjudicator','final_category','user_corrected'],
    'qa_checks':['case_id','expected_action','actual_action','expected_value','actual_value','evidence_recomputed_value','unit','expected_basis','actual_basis','evidence_ids','reviewer_id'],
    'user_tasks':['participant_id','task','completed','elapsed_seconds','first_insight_seconds','blocker'],
    'import_trials':['file_id','trial_id','elapsed_seconds','max_ui_freeze_seconds'],
    'issues':['issue_id','severity','status','blocks_task'],
}
KINDS={'收入','支出','转账'}
RELATIONS={'purchase_duplicate','refund_duplicate'}
TASKS={'import','insight','budget_goal','question','refund'}


def initialize(directory):
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    targets=[directory/'manifest.json',*(directory/(name+'.csv') for name in TABLES)]
    if any(path.exists() for path in targets):raise ValueError('采集模板已存在；为避免覆盖记录，请使用新目录')
    manifest=dict(schema_version=1,dataset_id='',data_kind='uncollected',application_version='',ground_truth_scope='',
                  independent_scan_declared_complete=False,candidate_snapshot_ref='',notes='')
    (directory/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    for name,columns in TABLES.items():
        with (directory/(name+'.csv')).open('w',encoding='utf-8-sig',newline='') as stream:
            csv.writer(stream).writerow(columns)
    return directory


def _rows(directory,name):
    with (directory/(name+'.csv')).open(encoding='utf-8-sig',newline='') as stream:
        reader=csv.DictReader(stream)
        if reader.fieldnames!=TABLES[name]:raise ValueError(name+'表头与模板不一致')
        rows=list(reader)
    if any(None in row or any(v is None for v in row.values()) for row in rows):raise ValueError(name+'存在缺列或多余列')
    return [{k:v.strip() for k,v in row.items()} for row in rows if any(v.strip() for v in row.values())]


def _boolean(value):
    if value in ('true','1','yes'):return True
    if value in ('false','0','no',''):return False
    raise ValueError('布尔字段只接受true/false、1/0、yes/no')


def _number(value,*,integer=False,optional=False,signed=False):
    if optional and value=='':return None
    try:result=int(value) if integer else float(value)
    except (ValueError,TypeError):raise ValueError('数值字段无效') from None
    if not math.isfinite(result) or (not signed and result<0):raise ValueError('数值须有限，计数和耗时不能为负')
    return result


def _unique(rows,column):
    values=[r[column] for r in rows]
    if any(not v for v in values) or len(values)!=len(set(values)):raise ValueError(column+'不能为空或重复')
    return {r[column]:r for r in rows}


def _ratio(a,b):return a/b if b else None


def _wilson(correct,total):
    if not total:return None
    z=1.959963984540054;p=correct/total;den=1+z*z/total
    middle=(p+z*z/(2*total))/den
    radius=z*math.sqrt(p*(1-p)/total+z*z/(4*total*total))/den
    return [max(0,middle-radius),min(1,middle+radius)]


def _adjudicated(row,field):
    a,b=row['label_a'],row['label_b'];final=row[field]
    if not final:return False
    if not a or not b or not row['labeler_a'] or not row['labeler_b'] or row['labeler_a']==row['labeler_b']:
        raise ValueError('最终标签须有两位不同标注者的独立标签')
    if a==b:
        if final!=a:raise ValueError('一致标签与最终标签冲突')
    elif not row['adjudicator'] or row['adjudicator'] in (row['labeler_a'],row['labeler_b']):
        raise ValueError('争议标签须记录第三位仲裁者')
    return True


def evaluate(directory):
    directory=Path(directory)
    manifest=json.loads((directory/'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('schema_version')!=1:raise ValueError('不支持的采集模板版本')
    data_kind=manifest.get('data_kind')
    if data_kind not in ('uncollected','synthetic','deidentified_real'):raise ValueError('须明确声明数据性质')
    tables={name:_rows(directory,name) for name in TABLES}
    files=_unique(tables['files'],'file_id');transactions=_unique(tables['transactions'],'transaction_id')
    for row in transactions.values():
        if row['file_id'] not in files or row['kind'] not in KINDS or row['record_type'] not in ('ordinary','refund','transfer'):
            raise ValueError('交易来源、类型或记录角色无效')
    imports=defaultdict(lambda:dict(files=0,parsed=0,failed=0,excluded=0,expected_rows=0,parsed_rows=0,mapping_observations=[]))
    for row in files.values():
        if row['data_kind']!=data_kind or row['source'] not in ('wechat','alipay','bank') or row['status'] not in ('parsed','failed','excluded'):
            raise ValueError('文件的数据性质、来源或导入状态无效')
        expected=_number(row['expected_rows'],integer=True);parsed=_number(row['parsed_rows'],integer=True)
        if parsed>expected:raise ValueError('解析行数超过人工确认的有效行数，请先核对分母')
        mapping_seconds=_number(row['mapping_seconds'],optional=True)
        result=imports[(row['source'],row['format_version'])];result['files']+=1;result['parsed']+=row['status']=='parsed'
        result['failed']+=row['status']=='failed';result['excluded']+=row['status']=='excluded'
        result['expected_rows']+=expected;result['parsed_rows']+=parsed
        if mapping_seconds is not None:result['mapping_observations'].append(mapping_seconds)
    for result in imports.values():
        observations=result.pop('mapping_observations')
        result['mapping_observations']=len(observations)
        result['mapping_median_seconds']=median(observations) if observations else None
        result['mapping_max_seconds']=max(observations) if observations else None
        result['file_success_rate']=_ratio(result['parsed'],result['files'])
        result['row_coverage']=_ratio(result['parsed_rows'],result['expected_rows'])

    def pair(row):
        left,right=row['left_id'],row['right_id'];relation=row['relation']
        if left==right or left not in transactions or right not in transactions or relation not in RELATIONS:
            raise ValueError('重复关系引用无效；必须是两个已登记交易及明确关系类型')
        roles={transactions[t]['record_type'] for t in (left,right)}
        if relation=='refund_duplicate' and roles!={'refund'}:raise ValueError('退款重复关系必须引用两条退款')
        if relation=='purchase_duplicate' and any(transactions[t]['kind']!='支出' or transactions[t]['record_type']!='ordinary' for t in (left,right)):
            raise ValueError('消费重复关系必须引用普通支出')
        return (relation,*sorted((left,right)))
    predicted={};truth={}
    for row in tables['candidates']:
        key=pair(row)
        if key in predicted or row['level'] not in ('A','B','C') or not row['algorithm_version'] or not row['run_id']:
            raise ValueError('候选不可重复，须记录等级、算法版本和单次运行标识')
        predicted[key]=row
    if len({r['run_id'] for r in predicted.values()})>1:raise ValueError('一次评估只能使用同一冻结候选快照')
    for row in tables['ground_truth']:
        key=pair(row)
        if key in truth:raise ValueError('人工关系标签不可重复')
        if any(row[k] not in ('','duplicate','not_duplicate') for k in ('label_a','label_b','final_label')):raise ValueError('人工重复标签无效')
        truth[key]=row['final_label'] if _adjudicated(row,'final_label') else None
    scans=defaultdict(set);seen_scans=set()
    for row in tables['scan_coverage']:
        key=(row['transaction_id'],row['labeler_id'])
        if key in seen_scans or key[0] not in transactions or not key[1]:raise ValueError('独立扫描覆盖记录无效或重复')
        seen_scans.add(key)
        if _boolean(row['reviewed']) and _boolean(row['blind_to_candidates']):scans[key[1]].add(key[0])
    complete_scanners=sum(ids==set(transactions) for ids in scans.values()) if transactions else 0
    independent=bool(transactions and complete_scanners>=2 and manifest.get('ground_truth_scope') and manifest.get('candidate_snapshot_ref') and
                     manifest.get('independent_scan_declared_complete') is True and truth and all(v is not None for v in truth.values()))
    def duplicate_metrics(keys):
        labeled=[key for key in keys if truth.get(key) is not None]
        tp=sum(truth[key]=='duplicate' for key in labeled)
        positives={key for key,value in truth.items() if value=='duplicate'}
        return dict(candidates=len(keys),labeled_candidates=len(labeled),true_positive_candidates=tp,
                    candidate_label_coverage=_ratio(len(labeled),len(keys)),precision_on_labeled_candidates=_ratio(tp,len(labeled)),
                    precision_wilson95=_wilson(tp,len(labeled)),
                    recall=_ratio(len(set(keys)&positives),len(positives)) if independent else None,
                    ground_truth_positive_pairs=len(positives) if independent else None)
    duplicates=dict(prediction_unit='candidate pair, not an automatic or user-confirmed merge',
                    full_blind_scanners=complete_scanners,independent_denominator_available=independent,
                    all_levels=duplicate_metrics(set(predicted)),
                    confirmable_A_B=duplicate_metrics({k for k,v in predicted.items() if v['level'] in ('A','B')}))
    duplicates['by_relation']={relation:duplicate_metrics({k for k in predicted if k[0]==relation}) for relation in RELATIONS}
    for relation,metrics in duplicates['by_relation'].items():
        positives={k for k,v in truth.items() if v=='duplicate' and k[0]==relation}
        metrics['ground_truth_positive_pairs']=len(positives) if independent else None
        metrics['recall']=_ratio(len({k for k in predicted if k[0]==relation}&positives),len(positives)) if independent else None

    classifications=_unique(tables['classifications'],'transaction_id');eligible={k for k,r in transactions.items() if r['record_type']=='ordinary' and r['kind']!='转账'}
    auto=correct=finalized=corrections=correction_observations=0
    from .categories import category_options
    for identifier,row in classifications.items():
        if identifier not in eligible:raise ValueError('分类评估只引用普通收入/支出')
        if not _adjudicated(row,'final_category'):continue
        if row['final_category'] not in category_options(transactions[identifier]['kind']):raise ValueError('人工最终分类不在该收支类型范围内')
        if not row['automatic']:raise ValueError('已标注分类须明确记录是否为自动分类')
        finalized+=1
        if _boolean(row['automatic']):auto+=1;correct+=row['predicted_category']==row['final_category']
        if row['user_corrected']:
            correction_observations+=1;corrections+=_boolean(row['user_corrected'])
    classification=dict(eligible_transactions=len(eligible),adjudicated_transactions=finalized,automatic_labeled_transactions=auto,
                        coverage_on_labeled=_ratio(auto,finalized),label_coverage=_ratio(finalized,len(eligible)),
                        accuracy_on_automatic_labeled=_ratio(correct,auto),accuracy_wilson95=_wilson(correct,auto),
                        user_correction_observations=correction_observations,observed_user_correction_rate=_ratio(corrections,correction_observations))

    qa=_unique(tables['qa_checks'],'case_id');answered=grounded=should_refuse=correct_refusals=0
    for row in qa.values():
        if row['expected_action'] not in ('answer','refuse') or row['actual_action'] not in ('answer','refuse') or not row['reviewer_id']:
            raise ValueError('问答评估须记录预期/实际动作及复核者')
        should_refuse+=row['expected_action']=='refuse'
        correct_refusals+=row['expected_action']==row['actual_action']=='refuse'
        if row['actual_action']=='answer':
            answered+=1;ids={v for v in row['evidence_ids'].split('|') if v}
            values=[_number(row[k],integer=True,optional=True,signed=True) for k in ('expected_value','actual_value','evidence_recomputed_value')]
            grounded+=bool(row['expected_action']=='answer' and row['unit'] in ('cents','count','ratio_bps') and ids and ids<=set(transactions)
                           and all(v is not None for v in values) and len(set(values))==1 and row['expected_basis']==row['actual_basis']
                           and row['actual_basis'] in ('cash','original_purchase_month','gross'))
    qa_summary=dict(cases=len(qa),answered=answered,reviewed_grounded_rate=_ratio(grounded,answered),
                    should_refuse=should_refuse,correct_refusal_rate=_ratio(correct_refusals,should_refuse))

    user_tasks=defaultdict(set);seen_tasks=set();insight_times=[];incomplete=blockers=0
    for row in tables['user_tasks']:
        key=(row['participant_id'],row['task'])
        if key in seen_tasks or not key[0] or row['task'] not in TASKS:raise ValueError('用户任务无效或重复')
        seen_tasks.add(key);user_tasks[key[0]].add(key[1]);_number(row['elapsed_seconds'])
        incomplete+=not _boolean(row['completed']);blockers+=_boolean(row['blocker'])
        value=_number(row['first_insight_seconds'],optional=True)
        if row['task']=='insight' and value is not None:insight_times.append(value)
    task_summary=dict(participants=len(user_tasks),participants_with_all_five_tasks=sum(t==TASKS for t in user_tasks.values()),
                      incomplete_tasks=incomplete,reported_blocking_tasks=blockers,first_insight_seconds=insight_times,
                      conclusion_scope='small qualitative trial; no population satisfaction or market claim')
    trials=defaultdict(list);seen_trials=set()
    for row in tables['import_trials']:
        key=(row['file_id'],row['trial_id'])
        if key in seen_trials or key[0] not in files or not key[1]:raise ValueError('导入计时引用无效或重复')
        seen_trials.add(key);trials[key[0]].append((_number(row['elapsed_seconds']),_number(row['max_ui_freeze_seconds'])))
    timings=[]
    for file_id,observations in trials.items():
        elapsed=sorted(v[0] for v in observations);n=len(elapsed)
        timings.append(dict(file_id=file_id,repeats=n,p95_seconds=elapsed[math.ceil(.95*n)-1] if n>=20 else None,
                            observed_max_seconds=max(elapsed),observed_max_ui_freeze_seconds=max(v[1] for v in observations)))
    issues=_unique(tables['issues'],'issue_id');open_critical=0
    for row in issues.values():
        if row['severity'] not in ('P0','P1','P2','P3') or row['status'] not in ('open','fixed','accepted'):raise ValueError('问题严重度/状态无效')
        open_critical+=row['status']!='fixed' and (row['severity']=='P0' or _boolean(row['blocks_task']))

    missing=[]
    def require(condition,message):
        if not condition:missing.append(message)
    require(data_kind=='deidentified_real','尚无声明为脱敏真实数据的试验')
    require(bool(manifest.get('application_version')),'需记录实际受测的应用版本')
    require(len(files)>=8,'真实导出不足8份')
    require(len({r['participant_id'] for r in files.values() if r['participant_id']})>=3,'数据提供者不足3位')
    require(bool(files) and all(r['consent_ref'] for r in files.values()),'需补每份数据的同意记录引用')
    require(bool(files) and all(r['format_version'] and (r['source']!='bank' or r['bank_alias']) for r in files.values()),'需补来源格式版本与银行代号')
    require({r['source'] for r in files.values()}>={'wechat','alipay','bank'},'需覆盖微信、支付宝与银行来源')
    require(independent,'独立盲扫分母尚未完整，不发布召回率')
    require(bool(manifest.get('candidate_snapshot_ref')) and all(truth.get(k) is not None for k in predicted),'冻结候选快照或候选标注尚未完成')
    require(finalized>=200,'双人标注/仲裁分类不足200笔')
    require(task_summary['participants_with_all_five_tasks']>=5,'五位用户的完整任务记录尚未齐备')
    require(answered>0 and should_refuse>0,'金额回答与应拒答案例均需记录')
    requires_blocker_review=bool(open_critical or blockers or incomplete or any(r['status']=='failed' for r in files.values()))
    status='awaiting_data' if data_kind=='uncollected' else 'synthetic_practice_only' if data_kind=='synthetic' else 'incomplete_pilot' if missing else 'needs_blocker_review' if requires_blocker_review else 'ready_for_manual_acceptance'
    return dict(schema_version=1,status=status,declared_data_kind=data_kind,external_authenticity_verified=False,
                automatic_real_validation_pass=False,missing_requirements=missing,requires_blocker_review=requires_blocker_review,
                imports=[dict(source=k[0],format_version=k[1],**v) for k,v in sorted(imports.items())],duplicate_detection=duplicates,classification=classification,qa=qa_summary,
                user_trial=task_summary,import_timing=timings,open_critical_or_blocking_issues=open_critical)
