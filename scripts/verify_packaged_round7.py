"""Execute seventh-review acceptance in the actual EXE using synthetic data."""
from pathlib import Path
from tempfile import TemporaryDirectory
import json,os,subprocess,sys
ROOT=Path(__file__).resolve().parents[1]
exe=Path(sys.argv[1]) if len(sys.argv)>1 else ROOT/'dist/AIFinanceHelper/AIFinanceHelper.exe'
with TemporaryDirectory(prefix='.test_packaged_round7_',dir=ROOT) as temp:
    env=os.environ.copy();env['FINANCE_DESKTOP_DATA_DIR']=temp
    result=subprocess.run([str(exe),'--self-test-round7'],env=env,timeout=60,creationflags=subprocess.CREATE_NO_WINDOW)
    assert result.returncode==0
    report=json.loads((Path(temp)/'round7-self-test.json').read_text(encoding='utf-8'))
    assert all(value is True for key,value in report.items() if key!='data')
    report['execution']='actual packaged Windows EXE'
    (ROOT/'docs/round7_packaged_validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False))
print('PACKAGED_ROUND7_ACCEPTANCE_OK')
