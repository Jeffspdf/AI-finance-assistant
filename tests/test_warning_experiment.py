"""Background-only diagnostic tests. Never import Tk or execute the finance GUI."""
from io import BytesIO
import json
from pathlib import Path
import os
import shutil
import subprocess
import sys
from zipfile import ZipFile
import pytest
from scripts import warning_experiment as experiment


def test_a_is_direct_and_b_has_a_matched_sham_control():
    plan = experiment.trial_plan(10)
    assert len(plan) == 30
    assert [r['arm'] for r in plan[:10]] == ['original'] * 10
    assert [r['arm'] for r in plan[10:14]] == ['ime_normal', 'ime_disabled', 'ime_disabled', 'ime_normal']
    assert all(r['phase'] == 'B' for r in plan[10:])
    assert '& $s.python $s.source --self-test *>> $s.log' in experiment.TRIAL_POWERSHELL
    assert '-RedirectStandardOutput' not in experiment.TRIAL_POWERSHELL


@pytest.mark.parametrize('count',[0,9,21,100])
def test_plan_refuses_out_of_scope_repetition_counts(count):
    with pytest.raises(ValueError):experiment.trial_plan(count)


def test_visible_gui_requires_explicit_flag_before_any_source_or_process_access(monkeypatch):
    monkeypatch.setattr(experiment,'read',lambda *args:pytest.fail('Manifest accessed before GUI consent gate'))
    with pytest.raises(ValueError,match='allow-visible-gui'):experiment.execute('does-not-exist','A')


def test_internal_child_cannot_accidentally_start_gui_without_released_gate(monkeypatch):
    monkeypatch.delenv('DIAG_WARNING_VISIBLE_GUI',raising=False)
    with pytest.raises(ValueError,match='released'):experiment.ime_child()


def test_prepare_and_changed_baseline_never_launch_a_process(tmp_path,monkeypatch):
    stream=BytesIO()
    with ZipFile(stream,'w') as z:z.writestr('desktop.py','# synthetic preparation fixture; never executed\n')
    monkeypatch.setattr(experiment.subprocess,'check_output',lambda *a,**k:stream.getvalue())
    monkeypatch.setattr(experiment.subprocess,'Popen',lambda *a,**k:pytest.fail('Preparation or integrity failure must not launch anything'))
    out=tmp_path/'session'
    manifest=experiment.prepare(out,sys.executable,10,powershell=sys.executable)
    assert manifest['gui_started_during_prepare'] is False and len(manifest['plan'])==30
    assert manifest['powershell']==str(Path(sys.executable).resolve())
    assert (out/'trial.ps1').is_file()
    with pytest.raises(ValueError,match='never replaced'):experiment.prepare(out,sys.executable,10)
    (out/'baseline/desktop.py').write_text('changed synthetic source')
    with pytest.raises(ValueError,match='Baseline export changed'):experiment.execute(out,'A',allow_visible_gui=True)


def test_outer_shell_warning_is_not_counted_as_historical_append_reproduction(tmp_path):
    folder=make_trial(tmp_path)
    (folder/'append.log').write_text('no append warning')
    (folder/'launcher.stderr').write_text(experiment.WARNING)
    result=experiment.evaluate_trial(folder,'original')
    assert result['warning_outside_append'] and not result['warning']


def test_cleanup_induced_output_is_retained_but_not_counted_as_baseline_reproduction(tmp_path):
    from hashlib import sha256
    folder=make_trial(tmp_path)
    (folder/'append.before-cleanup.log').write_bytes(b'')
    record=experiment.read(folder/'process-record.json')
    record.update(cleanup_started_utc='synthetic',pre_cleanup_log_sha256=sha256(b'').hexdigest())
    experiment.save(folder/'process-record.json',record)
    result=experiment.evaluate_trial(folder,'original')
    assert result['valid_execution'] and result['warning_anytime'] and not result['warning']


@pytest.mark.parametrize('member',['../escape','C:/escape','folder/../../escape','//server/share/escape'])
def test_archive_cannot_escape_session(tmp_path,member):
    stream=BytesIO()
    with ZipFile(stream,'w') as z:z.writestr(member,'not executed')
    with pytest.raises(ValueError):experiment.extract_baseline(stream.getvalue(),tmp_path/'export')
    assert not (tmp_path/'escape').exists()


@pytest.mark.parametrize('encoding',['utf-8','utf-16-le'])
def test_warning_detection_handles_powershell_encoding_and_case(encoding):
    assert experiment.warning_present(("CAN'T OPEN FILE FOR WRITE !\r\n").encode(encoding))
    assert not experiment.warning_present('unrelated write error'.encode(encoding))


def make_trial(root,arm='original',*,marker=True,ime=True,timeout=False):
    folder=root/'A-01-original';folder.mkdir();(folder/'data').mkdir()
    experiment.save(folder/'process-record.json',dict(exit_code=0,timed_out=timeout,processes={'321':dict(pid=321)}))
    (folder/'append.log').write_text(experiment.WARNING+'\n',encoding='utf-8')
    if marker:(folder/'data/self-test.ok').write_text('Desktop smoke test passed',encoding='utf-8')
    if arm!='original':experiment.save(folder/'child-meta.json',dict(arm=arm,ime_return=ime))
    return folder


@pytest.mark.parametrize('marker,timeout,arm,ime',[(False,False,'original',True),(True,True,'original',True),(True,False,'ime_disabled',False)])
def test_zero_exit_does_not_hide_missing_marker_timeout_or_failed_ime(tmp_path,marker,timeout,arm,ime):
    folder=make_trial(tmp_path,arm,marker=marker,timeout=timeout,ime=ime)
    verdict=experiment.evaluate_trial(folder,arm)
    assert not verdict['valid_execution'] and verdict['warning']
    assert not verdict['root_cause_closed'] and not verdict['formal_release_approved']


def test_procmon_import_scopes_pids_and_never_treats_csv_as_native_stack(tmp_path):
    path=tmp_path/'events.csv'
    path.write_text('Time of Day,Process Name,PID,Operation,Path,Result\n'
                    '19:00:01.1234567,python.exe,321,CreateFile,C:\\synthetic.dat,ACCESS DENIED\n'
                    '19:00:01.1234567,python.exe,999,CreateFile,C:\\unrelated.dat,ACCESS DENIED\n'
                    '19:00:01.1234567,python.exe,321,ReadFile,C:\\read.dat,ACCESS DENIED\n',encoding='utf-8-sig')
    events=experiment.candidate_events(path,{321})
    assert len(events)==1 and events[0]['path']=='C:\\synthetic.dat'
    assert 'stack require review' in events[0]['status']


def test_unrecognized_procmon_export_cannot_silently_look_like_zero_failures(tmp_path):
    path=tmp_path/'bad.csv';path.write_text('wrong,columns\n1,2\n')
    with pytest.raises(ValueError,match='columns'):experiment.candidate_events(path,{321})


def test_analyze_keeps_unrun_phase_and_partial_evidence_visible(tmp_path):
    experiment.save(tmp_path/'session.json',dict(plan=experiment.trial_plan(10)))
    empty=experiment.analyze(tmp_path)
    assert empty['groups']=={} and empty['planned_per_arm']==dict(original=10,ime_normal=10,ime_disabled=10)
    folder=make_trial(tmp_path);experiment.save(folder/'verdict.json',experiment.evaluate_trial(folder,'original'))
    result=experiment.analyze(tmp_path)
    assert result['groups']['original']==dict(total=1,valid=1,warnings_in_valid=1)
    assert not result['root_cause_closed'] and not result['formal_release_approved']
    (folder/'append.log').write_text('changed evidence')
    changed=experiment.analyze(tmp_path)
    assert changed['groups']['original']['valid']==0 and not changed['trials'][0]['verdict_matches_current_evidence']


def test_failed_containment_keeps_gate_closed_and_cleans_only_own_waiting_process(tmp_path,monkeypatch):
    calls=[]
    class Job:
        def assign(self,p):raise OSError('synthetic denied assignment')
        def stop(self):calls.append('stop_job')
        def close(self):calls.append('close_job')
    class Process:
        pid=123
        def poll(self):return None
        def terminate(self):calls.append('terminate_waiting_wrapper')
        def wait(self,timeout):return 1
    monkeypatch.setattr(experiment,'WindowsJob',Job)
    monkeypatch.setattr(experiment.subprocess,'Popen',lambda *a,**k:Process())
    monkeypatch.setattr(experiment.subprocess,'CREATE_NO_WINDOW',0,raising=False)
    gate=tmp_path/'go'
    with pytest.raises(OSError,match='assignment'):
        experiment.run_trial(['never-executed'],tmp_path,1,dict(gate=str(gate),log=str(tmp_path/'out')))
    assert not gate.exists() and calls==['stop_job','close_job','terminate_waiting_wrapper']
    assert (tmp_path/'process-record.json').exists()


@pytest.mark.skipif(os.name!='nt',reason='Windows native process-containment preflight')
def test_real_powershell_append_pid_and_log_capture_with_synthetic_console_child(tmp_path):
    shell=shutil.which('pwsh') or shutil.which('powershell')
    if not shell:pytest.skip('PowerShell is required for the synthetic console preflight')
    source=tmp_path/'synthetic.py'
    source.write_text("import os,time\nfrom pathlib import Path\np=Path(os.environ['FINANCE_DESKTOP_DATA_DIR']);p.mkdir(exist_ok=True)\n"
                      "(p/'self-test.ok').write_text('Desktop smoke test passed')\n(p/'pid').write_text(str(os.getpid()))\n"
                      "print(\"SYNTHETIC_CONTROL: can't open file for write !\",flush=True)\ntime.sleep(.4)\n",encoding='utf-8')
    script=tmp_path/'trial.ps1';script.write_text(experiment.TRIAL_POWERSHELL,encoding='utf-8-sig')
    spec=dict(arm='original',python=sys.executable,source=str(source),driver='unused',data=str(tmp_path/'data'),
              log=str(tmp_path/'append.log'),gate=str(tmp_path/'go'),child_meta=str(tmp_path/'child-meta.json'),
              working_dir=str(tmp_path),wrapper_result=str(tmp_path/'wrapper-result.json'),wrapper_error=str(tmp_path/'wrapper-error.txt'))
    experiment.save(tmp_path/'spec.json',spec);(tmp_path/'append.log').write_text('EXISTING_PREFIX\n')
    record=experiment.run_trial([shell,'-NoProfile','-ExecutionPolicy','Bypass','-File',str(script),'-Spec',str(tmp_path/'spec.json')],tmp_path,15,spec)
    assert record['exit_code']==0 and not record['timed_out']
    actual_pid=(tmp_path/'data/pid').read_text()
    assert actual_pid in record['processes'] and actual_pid!=str(record['wrapper_pid'])
    assert (tmp_path/'append.log').read_text().startswith('EXISTING_PREFIX')
    verdict=experiment.evaluate_trial(tmp_path,'original')
    assert verdict['valid_execution'] and verdict['warning'] and not verdict['root_cause_closed']


@pytest.mark.skipif(os.name!='nt',reason='Windows native process-containment preflight')
def test_native_timeout_terminates_only_the_owned_gated_process_family(tmp_path):
    worker=tmp_path/'worker.py'
    worker.write_text("from pathlib import Path\nimport time,sys,subprocess\n"
                      "while not Path(sys.argv[1]).exists():time.sleep(.01)\n"
                      "subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'])\n"
                      "time.sleep(30)\n",encoding='utf-8')
    spec=dict(gate=str(tmp_path/'go'),log=str(tmp_path/'absent.log'))
    record=experiment.run_trial([sys.executable,str(worker),spec['gate']],tmp_path,2,spec,sample_modules=False)
    assert record['timed_out'] and record['exit_code']!=0 and len(record['processes'])>=2
