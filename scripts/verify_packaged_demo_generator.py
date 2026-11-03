"""Run the actual EXE's generator/TLS/UI self-test with fresh, synthetic data."""
from pathlib import Path
from tempfile import TemporaryDirectory
import json,os,subprocess,sys
ROOT=Path(__file__).resolve().parents[1]
exe=Path(sys.argv[1]) if len(sys.argv)>1 else ROOT/'dist/AIFinanceHelper/AIFinanceHelper.exe'
with TemporaryDirectory(prefix='.test_packaged_demo_generator_',dir=ROOT) as temp:
    env=os.environ.copy();env['FINANCE_DESKTOP_DATA_DIR']=temp
    result=subprocess.run([str(exe),'--self-test'],env=env,timeout=60,creationflags=subprocess.CREATE_NO_WINDOW)
    assert result.returncode==0 and (Path(temp)/'self-test.ok').is_file()
    report=json.loads((Path(temp)/'demo-generator-self-test.json').read_text(encoding='utf-8'))
    assert report['generated_rows']==14 and report['replacement_removed']==7 and report['replacement_added']==8 and report['personal_unchanged']
    report['execution']='actual packaged Windows EXE'
    (ROOT/'docs/demo_generator_packaged_validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False))
print('PACKAGED_DEMO_GENERATOR_UI_AND_SERVICE_OK')
