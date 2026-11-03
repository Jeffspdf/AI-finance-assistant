"""Run actual frozen round-five acceptance without loading personal data or keys."""
from pathlib import Path
from tempfile import TemporaryDirectory
import json,os,subprocess,sys
ROOT=Path(__file__).resolve().parents[1]
exe=Path(sys.argv[1]) if len(sys.argv)>1 else ROOT/'dist/AIFinanceHelper/AIFinanceHelper.exe'
with TemporaryDirectory(prefix='.test_packaged_round5_',dir=ROOT) as temp:
    env=os.environ.copy();env['FINANCE_DESKTOP_DATA_DIR']=temp
    result=subprocess.run([str(exe),'--self-test-round5'],env=env,timeout=60,creationflags=subprocess.CREATE_NO_WINDOW)
    assert result.returncode==0
    report=json.loads((Path(temp)/'round5-self-test.json').read_text(encoding='utf-8'))
    assert report['two_wallets_rejected'] and report['group_status']=='needs_review' and report['C_confirm_rejected']
    report['execution']='actual packaged Windows EXE'
    (ROOT/'docs/round5_packaged_validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False))
print('PACKAGED_ROUND5_CORRECTNESS_ACCEPTANCE_OK')
