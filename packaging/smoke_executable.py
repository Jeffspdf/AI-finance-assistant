"""Run a known local build directly, with bounded waits and isolated data."""
import argparse,os,subprocess,sys
from pathlib import Path

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--exe',required=True);parser.add_argument('--data-dir',required=True)
    args=parser.parse_args();exe=Path(args.exe).resolve();data=Path(args.data_dir).resolve();data.mkdir(parents=True,exist_ok=True)
    env=os.environ.copy();env['FINANCE_DESKTOP_DATA_DIR']=str(data)
    checks=[('--self-test','self-test.ok'),('--self-test-m1a','m1a-self-test.json'),
            ('--self-test-round5','round5-self-test.json'),('--self-test-round6','round6-self-test.json'),
            ('--self-test-round7','round7-self-test.json'),('--self-test-round8','round8-self-test.json'),
            ('--self-test-m2','m2-ui/gui-results.json'),('--self-test-m2-migration','m2-migration.json')]
    for flag,marker in checks:
        isolated=data/flag.removeprefix('--');isolated.mkdir(parents=True,exist_ok=True)
        env['FINANCE_DESKTOP_DATA_DIR']=str(isolated)
        target=isolated/marker;target.unlink(missing_ok=True)
        if flag=='--self-test-m2-migration':
            sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tests'))
            from m2_v4_fixture import create_v4
            create_v4(isolated/'personal.sqlite3')
        result=subprocess.run([str(exe),flag],env=env,shell=False,timeout=90,capture_output=True,
                              creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        (isolated/'process.stdout.log').write_bytes(result.stdout)
        (isolated/'process.stderr.log').write_bytes(result.stderr)
        if b"can't open file for write" in (result.stdout+result.stderr).lower():
            raise RuntimeError(f'Packaged {flag} emitted unresolved write warning; logs retained; installer build stopped')
        if result.returncode!=0 or not target.is_file():raise RuntimeError(f'Packaged {flag} failed; installer build stopped')
        print('PACKAGED_CHECK_OK',flag,flush=True)

if __name__=='__main__':main()
