"""Run a known local build directly, with bounded waits and isolated data."""
import argparse,os,subprocess
from pathlib import Path

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--exe',required=True);parser.add_argument('--data-dir',required=True)
    args=parser.parse_args();exe=Path(args.exe).resolve();data=Path(args.data_dir).resolve();data.mkdir(parents=True,exist_ok=True)
    env=os.environ.copy();env['FINANCE_DESKTOP_DATA_DIR']=str(data)
    checks=[('--self-test','self-test.ok'),('--self-test-m1a','m1a-self-test.json'),
            ('--self-test-round5','round5-self-test.json'),('--self-test-round6','round6-self-test.json'),
            ('--self-test-round7','round7-self-test.json'),('--self-test-round8','round8-self-test.json')]
    for flag,marker in checks:
        target=data/marker;target.unlink(missing_ok=True)
        result=subprocess.run([str(exe),flag],env=env,shell=False,timeout=60,
                              creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        if result.returncode!=0 or not target.is_file():raise RuntimeError(f'Packaged {flag} failed; installer build stopped')
        print('PACKAGED_CHECK_OK',flag,flush=True)

if __name__=='__main__':main()
