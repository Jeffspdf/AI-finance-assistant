"""Run the actual frozen EXE against isolated, synthetic-only M1a acceptance."""
from pathlib import Path
from tempfile import TemporaryDirectory
import json,os,subprocess,sys

ROOT=Path(__file__).resolve().parents[1]
exe=Path(sys.argv[1]) if len(sys.argv)>1 else ROOT/'dist/AIFinanceHelper/AIFinanceHelper.exe'
with TemporaryDirectory(prefix='.test_packaged_m1a_',dir=ROOT) as temp:
    env=os.environ.copy();env['FINANCE_DESKTOP_DATA_DIR']=temp
    result=subprocess.run([str(exe),'--self-test-m1a'],env=env,timeout=60,creationflags=subprocess.CREATE_NO_WINDOW)
    assert result.returncode==0
    report=json.loads((Path(temp)/'m1a-self-test.json').read_text(encoding='utf-8'))
    assert [report[k] for k in ('before_cents','confirmed_cents','split_cents')]==[12000,6000,12000]
    report['execution']='actual packaged Windows EXE'
    (ROOT/'docs/m1a_packaged_validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False))
print('PACKAGED_M1A_SIX_ROW_ACCEPTANCE_OK')
