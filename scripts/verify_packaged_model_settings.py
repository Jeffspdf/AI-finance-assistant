"""Seed a synthetic encrypted key and run the actual packaged restart self-test."""
from pathlib import Path
from tempfile import TemporaryDirectory
import os
import subprocess
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from finance.model_settings import ModelSettings


with TemporaryDirectory(prefix='.test_packaged_model_',dir=ROOT) as temp:
    store=ModelSettings(temp)
    store.save('https://example.com/v1','synthetic-model','synthetic-packaged-key-only')
    original=store.path.read_bytes()
    env=os.environ.copy();env['FINANCE_DESKTOP_DATA_DIR']=temp
    exe=ROOT/'dist/AIFinanceHelper/AIFinanceHelper.exe'
    for _ in range(2):
        (Path(temp)/'self-test.ok').unlink(missing_ok=True)
        process=subprocess.run([str(exe),'--self-test'],env=env,timeout=40,creationflags=subprocess.CREATE_NO_WINDOW)
        if process.returncode!=0:
            log=Path(temp)/'startup-error.log'
            print(log.read_text(encoding='utf-8') if log.is_file() else 'No startup log',flush=True)
            raise AssertionError(f'Packaged exit code {process.returncode}')
        assert (Path(temp)/'self-test.ok').is_file()
        assert store.path.read_bytes()==original
        assert store.load().enabled and store.load().api_key=='synthetic-packaged-key-only'
    print('PACKAGED_DPAPI_CONFIG_RESTART_TWICE_OK')
