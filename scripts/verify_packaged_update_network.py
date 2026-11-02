"""Execute the actual windowed EXE's update check and verified download path."""
from pathlib import Path
from tempfile import TemporaryDirectory
import os,sys,subprocess,json
ROOT=Path(__file__).resolve().parents[1]
with TemporaryDirectory(prefix='.test_packaged_update_net_',dir=ROOT) as temp:
    env=os.environ.copy();env['FINANCE_DESKTOP_DATA_DIR']=temp
    for name in ['HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','http_proxy','https_proxy','all_proxy']:
        env.pop(name,None)
    exe=Path(sys.argv[1]) if len(sys.argv)>1 else ROOT/'dist/AIFinanceHelper/AIFinanceHelper.exe'
    result=subprocess.run([str(exe),'--diagnose-updates','--download-latest'],env=env,
                          timeout=220,creationflags=subprocess.CREATE_NO_WINDOW)
    report=json.loads((Path(temp)/'update-check-result.json').read_text(encoding='utf-8'))
    assert result.returncode==0 and report['check_ok'] and report['download_ok'],report
    # The report is public release metadata and allowlisted network events only.
    destination=ROOT/'docs'/'packaged_update_network.json'
    destination.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='events'},ensure_ascii=False),flush=True)
    print('PACKAGED_UPDATER_NETWORK_AND_SHA256_OK',flush=True)
