"""Execute round-six acceptance in the actual EXE with isolated synthetic data."""
from pathlib import Path
from tempfile import TemporaryDirectory
import json,os,subprocess,sys
ROOT=Path(__file__).resolve().parents[1]
exe=Path(sys.argv[1]) if len(sys.argv)>1 else ROOT/'dist/AIFinanceHelper/AIFinanceHelper.exe'
with TemporaryDirectory(prefix='.test_packaged_round6_',dir=ROOT) as temp:
    env=os.environ.copy();env['FINANCE_DESKTOP_DATA_DIR']=temp
    result=subprocess.run([str(exe),'--self-test-round6'],env=env,timeout=60,creationflags=subprocess.CREATE_NO_WINDOW)
    assert result.returncode==0
    report=json.loads((Path(temp)/'round6-self-test.json').read_text(encoding='utf-8'))
    assert report['read_queries_unchanged'] and report['no_wallet_ack_required'] and report['invalid_transfer_category_rejected']
    report['execution']='actual packaged Windows EXE'
    (ROOT/'docs/round6_packaged_validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False))
print('PACKAGED_ROUND6_SEMANTICS_AND_READ_ONLY_ACCEPTANCE_OK')
