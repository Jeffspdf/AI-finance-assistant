"""An explicit course preview may report the known warning, never a failed executable."""
import importlib.util
from pathlib import Path
import subprocess
import sys
import shutil
import pytest

ROOT=Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('fault',[None,'exit_failure','missing_marker'])
def test_preview_warning_concession_preserves_all_eight_checks(tmp_path,monkeypatch,capsys,fault):
    script=ROOT/'packaging/smoke_executable.py'
    spec=importlib.util.spec_from_file_location('course_preview_smoke',script)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    monkeypatch.setattr(sys,'argv',[str(script),'--exe',str(tmp_path/'synthetic.exe'),'--data-dir',str(tmp_path/'smoke'),'--allow-known-write-warning'])
    markers={'--self-test':'self-test.ok','--self-test-m1a':'m1a-self-test.json',
        '--self-test-round5':'round5-self-test.json','--self-test-round6':'round6-self-test.json',
        '--self-test-round7':'round7-self-test.json','--self-test-round8':'round8-self-test.json',
        '--self-test-m2':'m2-ui/gui-results.json','--self-test-m2-migration':'m2-migration.json'}
    calls=[]
    def simulated(args,**kwargs):
        calls.append(args[1])
        if fault!='missing_marker':
            marker=Path(kwargs['env']['FINANCE_DESKTOP_DATA_DIR'])/markers[args[1]]
            marker.parent.mkdir(parents=True,exist_ok=True);marker.write_text('synthetic acceptance marker')
        return subprocess.CompletedProcess(args,1 if fault=='exit_failure' else 0,b'',b"can't open file for write !")
    monkeypatch.setattr(module.subprocess,'run',simulated)
    if fault:
        with pytest.raises(RuntimeError,match='failed; installer build stopped'):module.main()
    else:
        module.main()
        assert calls==list(markers)
        output=capsys.readouterr().out
        assert output.count('PACKAGED_KNOWN_LIMITATION')==8
        assert output.count('PACKAGED_CHECK_OK')==8
    assert (tmp_path/'smoke/self-test/process.stderr.log').read_bytes()==b"can't open file for write !"


@pytest.mark.parametrize('version',['0.9.0','0.9.1'])
def test_stable_build_cannot_request_preview_concession(version):
    shell=shutil.which('pwsh') or shutil.which('powershell')
    if not shell:pytest.skip('PowerShell is required for Windows build entry point')
    result=subprocess.run([shell,'-NoProfile','-File',str(ROOT/'packaging/build.ps1'),'-Version',version,'-CoursePreview','-Python','must-not-execute'],capture_output=True,text=True,errors='replace',timeout=20)
    assert result.returncode!=0
    assert 'stable release gates cannot be bypassed' in result.stdout+result.stderr
