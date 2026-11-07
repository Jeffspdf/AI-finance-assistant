from pathlib import Path
import subprocess,sys,json


def test_real_closure_controls_with_bounded_150_percent_viewport(tmp_path):
    root=Path(__file__).resolve().parents[1]
    result=subprocess.run([sys.executable,str(root/'scripts/verify_feedback_closure_ui.py'),str(tmp_path/'ui')],cwd=root,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=90)
    assert result.returncode==0,result.stdout+'\n'+result.stderr
    report=json.loads((tmp_path/'ui/results.json').read_text(encoding='utf-8'))
    assert len(report['checks'])==4 and report['original_ledger_unchanged']
    assert not report['callback_errors'] and report['ui_scale']==1.5
    assert all(not layout['horizontal_spill'] for layout in report['layouts'].values())
