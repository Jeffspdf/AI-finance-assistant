"""Pilot metrics must keep uncollected/synthetic data and unknown denominators explicit."""
from pathlib import Path
import csv,json
import pytest
from finance.real_validation import TABLES,initialize,evaluate


def write(directory,name,rows):
    with (directory/(name+'.csv')).open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=TABLES[name]);writer.writeheader();writer.writerows(rows)


def practice(tmp_path):
    initialize(tmp_path)
    manifest=json.loads((tmp_path/'manifest.json').read_text())
    manifest.update(data_kind='synthetic',ground_truth_scope='six synthetic source rows',candidate_snapshot_ref='synthetic-run-1',independent_scan_declared_complete=True)
    (tmp_path/'manifest.json').write_text(json.dumps(manifest))
    write(tmp_path,'files',[dict(file_id='f',participant_id='p',source='bank',bank_alias='b',format_version='fixture',data_kind='synthetic',consent_ref='not-real',status='parsed',expected_rows=6,parsed_rows=6,mapping_seconds=1)])
    write(tmp_path,'transactions',[dict(transaction_id=t,file_id='f',kind='支出',record_type='ordinary') for t in 'abcdef'])
    def pair(a,b):return dict(left_id=a,right_id=b,relation='purchase_duplicate')
    write(tmp_path,'candidates',[dict(pair(a,b),level='A',algorithm_version='test-only',run_id='one') for a,b in [('a','b'),('c','d')]])
    write(tmp_path,'ground_truth',[dict(pair(a,b),labeler_a='r1',label_a=label,labeler_b='r2',label_b=label,adjudicator='',final_label=label)
                                  for a,b,label in [('a','b','duplicate'),('c','d','not_duplicate'),('e','f','duplicate')]])
    write(tmp_path,'scan_coverage',[dict(transaction_id=t,labeler_id=r,reviewed='true',blind_to_candidates='true') for r in ['r1','r2'] for t in 'abcdef'])
    write(tmp_path,'classifications',[dict(transaction_id=t,predicted_category=pred,automatic=auto,labeler_a='r1',label_a='餐饮',labeler_b='r2',label_b='餐饮',adjudicator='',final_category='餐饮')
                                     for t,pred,auto in [('a','餐饮','true'),('b','交通','true'),('c','餐饮','false')]])
    return tmp_path


def test_empty_templates_have_no_fabricated_metrics_and_do_not_overwrite(tmp_path):
    initialize(tmp_path);report=evaluate(tmp_path)
    assert report['status']=='awaiting_data' and report['automatic_real_validation_pass'] is False
    assert report['duplicate_detection']['all_levels']['recall'] is None
    assert report['classification']['accuracy_on_automatic_labeled'] is None
    before=(tmp_path/'manifest.json').read_bytes()
    with pytest.raises(ValueError,match='覆盖'):initialize(tmp_path)
    assert (tmp_path/'manifest.json').read_bytes()==before


def test_independent_false_negative_denominator_and_classification_scope(tmp_path):
    report=evaluate(practice(tmp_path));metrics=report['duplicate_detection']['all_levels']
    assert metrics['precision_on_labeled_candidates']==.5 and metrics['recall']==.5
    assert metrics['ground_truth_positive_pairs']==2
    assert report['classification']['coverage_on_labeled']==pytest.approx(2/3)
    assert report['classification']['accuracy_on_automatic_labeled']==.5
    assert report['classification']['accuracy_wilson95'][0]<.5<report['classification']['accuracy_wilson95'][1]
    assert report['status']=='synthetic_practice_only' and report['external_authenticity_verified'] is False
    assert report['duplicate_detection']['by_relation']['refund_duplicate']['recall'] is None


def test_candidate_only_labels_do_not_invent_recall(tmp_path):
    practice(tmp_path);write(tmp_path,'scan_coverage',[])
    result=evaluate(tmp_path)['duplicate_detection']
    assert result['all_levels']['precision_on_labeled_candidates']==.5
    assert result['all_levels']['recall'] is None and not result['independent_denominator_available']


def test_nonblind_or_partial_second_scan_is_not_independent(tmp_path):
    practice(tmp_path)
    write(tmp_path,'scan_coverage',[dict(transaction_id=t,labeler_id=r,reviewed='true',blind_to_candidates='true' if r=='r1' else 'false') for r in ['r1','r2'] for t in 'abcdef'])
    report=evaluate(tmp_path);assert report['duplicate_detection']['full_blind_scanners']==1
    assert report['duplicate_detection']['all_levels']['recall'] is None


def test_duplicate_pairs_are_unordered_and_rejected_when_repeated(tmp_path):
    practice(tmp_path)
    write(tmp_path,'candidates',[dict(left_id=a,right_id=b,relation='purchase_duplicate',level='A',algorithm_version='test',run_id='one') for a,b in [('a','b'),('b','a')]])
    with pytest.raises(ValueError,match='不可重复'):evaluate(tmp_path)


def test_disagreement_requires_distinct_arbitrator(tmp_path):
    practice(tmp_path)
    write(tmp_path,'classifications',[dict(transaction_id='a',predicted_category='餐饮',automatic='true',labeler_a='r1',label_a='餐饮',labeler_b='r2',label_b='交通',adjudicator='r1',final_category='餐饮')])
    with pytest.raises(ValueError,match='第三位'):evaluate(tmp_path)


def test_qa_requires_matching_evidence_amount_and_basis_not_just_links(tmp_path):
    practice(tmp_path)
    row=dict(case_id='q1',expected_action='answer',actual_action='answer',expected_value=-100,actual_value=-100,evidence_recomputed_value=-100,unit='cents',expected_basis='cash',actual_basis='original_purchase_month',evidence_ids='a|b',reviewer_id='r1')
    write(tmp_path,'qa_checks',[row]);assert evaluate(tmp_path)['qa']['reviewed_grounded_rate']==0
    row['actual_basis']='cash';write(tmp_path,'qa_checks',[row]);assert evaluate(tmp_path)['qa']['reviewed_grounded_rate']==1
    row['evidence_recomputed_value']=-99;write(tmp_path,'qa_checks',[row]);assert evaluate(tmp_path)['qa']['reviewed_grounded_rate']==0


def test_one_import_trial_does_not_claim_p95(tmp_path):
    practice(tmp_path)
    write(tmp_path,'import_trials',[dict(file_id='f',trial_id='1',elapsed_seconds=2,max_ui_freeze_seconds=.1)])
    assert evaluate(tmp_path)['import_timing'][0]['p95_seconds'] is None
    write(tmp_path,'import_trials',[dict(file_id='f',trial_id=str(i),elapsed_seconds=i,max_ui_freeze_seconds=.1) for i in range(1,21)])
    assert evaluate(tmp_path)['import_timing'][0]['p95_seconds']==19


def test_real_declaration_alone_does_not_pass_protocol(tmp_path):
    initialize(tmp_path);manifest=json.loads((tmp_path/'manifest.json').read_text());manifest['data_kind']='deidentified_real'
    (tmp_path/'manifest.json').write_text(json.dumps(manifest));report=evaluate(tmp_path)
    assert report['status']=='incomplete_pilot' and report['missing_requirements']
    assert report['automatic_real_validation_pass'] is False and report['external_authenticity_verified'] is False


def test_accepted_p0_is_still_a_blocker_until_fixed(tmp_path):
    initialize(tmp_path)
    write(tmp_path,'issues',[dict(issue_id='synthetic-p0',severity='P0',status='accepted',blocks_task='false')])
    report=evaluate(tmp_path)
    assert report['requires_blocker_review'] and report['open_critical_or_blocking_issues']==1


def test_import_compatibility_is_reported_per_format_not_hidden_in_source_average(tmp_path):
    practice(tmp_path)
    common=dict(participant_id='p',source='bank',bank_alias='b',data_kind='synthetic',consent_ref='not-real',expected_rows=6,mapping_seconds=0)
    write(tmp_path,'files',[dict(common,file_id='f',format_version='good',status='parsed',parsed_rows=6),
                            dict(common,file_id='f2',format_version='unsupported',status='failed',parsed_rows=0)])
    report=evaluate(tmp_path);byformat={r['format_version']:r for r in report['imports']}
    assert byformat['good']['file_success_rate']==1 and byformat['unsupported']['file_success_rate']==0
    assert report['requires_blocker_review']
