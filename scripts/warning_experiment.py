"""Standalone A/B experiment driver. Preparation/analysis never starts the GUI.

Driver evidence and synthetic ledgers stay in the explicit session. Native
components may still access other paths; that is what Procmon must investigate.
The driver does not change application code, global input settings or Procmon.
"""
import argparse
import csv
import ctypes
from ctypes import wintypes
from datetime import datetime, timezone
from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path, PureWindowsPath
import runpy
import shutil
import subprocess
import sys
import time
import uuid
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
BASELINE = '18336e5411f32921d6785c2d0364c53f90790993'
WARNING = "can't open file for write !"
OPERATIONS = {'CreateFile', 'WriteFile', 'SetEndOfFile'}
RESULTS = {'ACCESS DENIED', 'SHARING VIOLATION', 'PATH NOT FOUND'}


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec='microseconds')


def save(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + '.pending')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    os.replace(temporary, path)


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def digest(path):
    h = sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def warning_present(raw):
    return any(WARNING.encode(encoding).lower() in raw.lower() for encoding in ('utf-8', 'utf-16-le'))


def trial_plan(repetitions):
    if not 10 <= repetitions <= 20:
        raise ValueError('Each arm requires 10–20 repetitions')
    plan = [dict(phase='A', arm='original', iteration=i + 1) for i in range(repetitions)]
    # Matched sham wrapper prevents attributing a runpy/instrumentation effect to IME.
    for i in range(repetitions):
        arms = ('ime_normal', 'ime_disabled') if i % 2 == 0 else ('ime_disabled', 'ime_normal')
        plan.extend(dict(phase='B', arm=arm, iteration=i + 1) for arm in arms)
    return plan


def extract_baseline(archive, target):
    with ZipFile(BytesIO(archive)) as package:
        for member in package.infolist():
            parts = Path(member.filename).parts
            if Path(member.filename).is_absolute() or PureWindowsPath(member.filename).drive or '..' in parts or '\\' in member.filename:
                raise ValueError('Unsafe archive member')
        package.extractall(target)


# Each wrapper waits for the driver to assign a kill-on-close job before it may
# launch Python. Native invocation retains the historical *>> operator exactly.
TRIAL_POWERSHELL = r'''param([Parameter(Mandatory=$true)][string]$Spec)
$ErrorActionPreference = 'Stop'
$s = Get-Content -LiteralPath $Spec -Raw -Encoding utf8 | ConvertFrom-Json
$deadline = [DateTime]::UtcNow.AddSeconds(20)
while (-not (Test-Path -LiteralPath $s.gate)) {
  if ([DateTime]::UtcNow -gt $deadline) { exit 125 }
  Start-Sleep -Milliseconds 50
}
$env:FINANCE_DESKTOP_DATA_DIR = $s.data
$env:DIAG_WARNING_CHILD_META = $s.child_meta
$env:DIAG_WARNING_ARM = $s.arm
$env:DIAG_WARNING_SOURCE = $s.source
$env:DIAG_WARNING_VISIBLE_GUI = 'yes'
$env:DIAG_WARNING_GATE = $s.gate
Set-Location -LiteralPath $s.working_dir
$started = [DateTimeOffset]::Now.ToString('o')
try {
  $ErrorActionPreference = 'Continue'
  if ($s.arm -eq 'original') {
    & $s.python $s.source --self-test *>> $s.log
  } else {
    & $s.python $s.driver _ime_child *>> $s.log
  }
  $code = $LASTEXITCODE
  [pscustomobject]@{started=$started;ended=[DateTimeOffset]::Now.ToString('o');exit_code=$code;pid=$PID;powershell_version=$PSVersionTable.PSVersion.ToString()} |
    ConvertTo-Json | Set-Content -LiteralPath $s.wrapper_result -Encoding utf8
  exit $code
} catch {
  $_ | Out-String | Set-Content -LiteralPath $s.wrapper_error -Encoding utf8
  exit 126
}
'''


def prepare(out, python, repetitions, powershell=None):
    out, python = Path(out).resolve(), Path(python).resolve()
    if out.exists():
        raise ValueError('Session must use a new output directory; existing evidence is never replaced')
    if not python.is_file():
        raise ValueError('Python executable not found')
    shell = powershell or shutil.which('pwsh') or shutil.which('powershell')
    if not shell or not Path(shell).is_file():
        raise ValueError('PowerShell executable not found')
    shell = Path(shell).resolve()
    plan = trial_plan(repetitions)
    archive = subprocess.check_output(['git', 'archive', '--format=zip', BASELINE], cwd=ROOT)
    out.mkdir(parents=True)
    extract_baseline(archive, out / 'baseline')
    (out / 'trial.ps1').write_text(TRIAL_POWERSHELL, encoding='utf-8-sig')
    hashes = {str(p.relative_to(out / 'baseline')): digest(p) for p in (out / 'baseline').rglob('*') if p.is_file()}
    manifest = dict(schema=1, created_utc=utc_now(), baseline_commit=BASELINE, python=str(python), python_sha256=digest(python),
                    powershell=str(shell), powershell_exe_sha256=digest(shell),
                    driver=str(Path(__file__).resolve()), source=str(out / 'baseline/desktop.py'),
                    driver_sha256=digest(__file__), powershell_sha256=digest(out / 'trial.ps1'),working_dir=str(ROOT),
                    baseline_sha256=hashes, plan=plan, raw_data='synthetic only',
                    record_type='preparation_snapshot', gui_started_during_prepare=False, formal_release_approved=False)
    save(out / 'session.json', manifest)
    return manifest


class WindowsJob:
    """Bound only our waiting wrapper and descendants, never unrelated processes."""
    def __init__(self):
        if os.name != 'nt':
            raise RuntimeError('Windows is required for controlled GUI execution')
        k = ctypes.WinDLL('kernel32', use_last_error=True)
        self.k = k
        k.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        k.CreateJobObjectW.restype = wintypes.HANDLE
        k.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        k.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p]
        k.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        k.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        k.CloseHandle.argtypes = [wintypes.HANDLE]
        class Basic(ctypes.Structure):
            _fields_ = [('process_time', ctypes.c_longlong), ('job_time', ctypes.c_longlong),
                        ('flags', wintypes.DWORD), ('minimum', ctypes.c_size_t), ('maximum', ctypes.c_size_t),
                        ('active_limit', wintypes.DWORD), ('affinity', ctypes.c_size_t),
                        ('priority', wintypes.DWORD), ('scheduling', wintypes.DWORD)]
        class Extended(ctypes.Structure):
            _fields_ = [('basic', Basic), ('io', ctypes.c_ulonglong * 6), ('process_memory', ctypes.c_size_t),
                        ('job_memory', ctypes.c_size_t), ('peak_process', ctypes.c_size_t), ('peak_job', ctypes.c_size_t)]
        self.handle = k.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = Extended()
        # Permit explicitly requested breakaway (e.g. a shared native helper),
        # rather than silently turning helper startup into a new failure mode.
        limits.basic.flags = 0x2000 | 0x0800
        if not k.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            self.close()
            raise ctypes.WinError(ctypes.get_last_error())

    def assign(self, process):
        if not self.k.AssignProcessToJobObject(self.handle, wintypes.HANDLE(int(process._handle))):
            raise ctypes.WinError(ctypes.get_last_error())

    def pids(self):
        class Members(ctypes.Structure):
            _fields_ = [('assigned', wintypes.DWORD), ('count', wintypes.DWORD), ('ids', ctypes.c_size_t * 2048)]
        members = Members()
        if not self.k.QueryInformationJobObject(self.handle, 3, ctypes.byref(members), ctypes.sizeof(members), None):
            raise ctypes.WinError(ctypes.get_last_error())
        if members.count > 2048:
            raise RuntimeError('Process list exceeded the bounded job buffer')
        return list(members.ids[:members.count])

    def stop(self):
        if self.handle:
            self.k.TerminateJobObject(self.handle, 124)

    def close(self):
        if self.handle:
            self.k.CloseHandle(self.handle)
            self.handle = None


def inspect_process(pid):
    """Read image/module identities for job members only, not their memory data."""
    k = ctypes.WinDLL('kernel32', use_last_error=True)
    psapi = ctypes.WinDLL('psapi', use_last_error=True)
    k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k.OpenProcess.restype = wintypes.HANDLE
    k.CloseHandle.argtypes = [wintypes.HANDLE]
    k.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    psapi.EnumProcessModulesEx.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), wintypes.DWORD]
    psapi.GetModuleFileNameExW.argtypes = [wintypes.HANDLE, wintypes.HMODULE, wintypes.LPWSTR, wintypes.DWORD]
    handle = k.OpenProcess(0x0410 | 0x1000, False, pid)
    if not handle:
        return dict(pid=pid, error=ctypes.get_last_error())
    try:
        image = ctypes.create_unicode_buffer(32768)
        length = wintypes.DWORD(len(image))
        image_ok = k.QueryFullProcessImageNameW(handle, 0, image, ctypes.byref(length))
        modules = (wintypes.HMODULE * 2048)()
        required = wintypes.DWORD()
        ok = psapi.EnumProcessModulesEx(handle, modules, ctypes.sizeof(modules), ctypes.byref(required), 3)
        relevant = []
        if ok:
            for module in modules[:min(required.value // ctypes.sizeof(wintypes.HMODULE), len(modules))]:
                name = ctypes.create_unicode_buffer(32768)
                psapi.GetModuleFileNameExW(handle, module, name, len(name))
                if 'sogou' in name.value.lower() or 'picface' in name.value.lower():
                    relevant.append(name.value)
        return dict(pid=pid, image=image.value if image_ok else None, modules_read=bool(ok), relevant_modules=relevant,
                    module_list_truncated=required.value > ctypes.sizeof(modules))
    finally:
        k.CloseHandle(handle)


def run_trial(command, folder, timeout, spec, *, sample_modules=True):
    """The gate stays closed if job containment cannot be established."""
    folder = Path(folder)
    job = WindowsJob()
    process = None
    record = dict(started_utc=utc_now(), processes={}, module_file_sha256={}, warning_observed_window=None, timed_out=False,
                  containment='kill-on-close job; explicit breakaway permitted; escaped/shared helper processes are not exhaustively tracked')
    try:
        env = os.environ.copy()
        for key in list(env):
            if key.startswith('FINANCE_LLM_') or key in ('OPENAI_API_KEY', 'DEEPSEEK_API_KEY'):
                env.pop(key)
        with (folder / 'launcher.stdout').open('wb') as stdout, (folder / 'launcher.stderr').open('wb') as stderr:
            process = subprocess.Popen(command, env=env, cwd=folder, stdout=stdout, stderr=stderr,
                                       creationflags=subprocess.CREATE_NO_WINDOW)
            record['wrapper_pid'] = process.pid
            job.assign(process)
            record['gate_released_utc'] = utc_now()
            Path(spec['gate']).touch(exist_ok=False)
            deadline = time.monotonic() + timeout
            previous_poll = record['gate_released_utc']
            while True:
                now = utc_now()
                for pid in job.pids():
                    key = str(pid)
                    item = record['processes'].setdefault(key, dict(pid=pid, first_seen_utc=now, last_seen_utc=now, relevant_modules=[]))
                    item['last_seen_utc'] = now
                    if sample_modules:
                        current = inspect_process(pid)
                        item['image'] = current.get('image') or item.get('image')
                        item['modules_read_at_least_once'] = item.get('modules_read_at_least_once', False) or current.get('modules_read', False)
                        item['relevant_modules'] = sorted(set(item['relevant_modules']) | set(current.get('relevant_modules', [])))
                        for module in current.get('relevant_modules', []):
                            if module not in record['module_file_sha256']:
                                try:
                                    record['module_file_sha256'][module] = digest(module)
                                except OSError as exc:
                                    record['module_file_sha256'][module] = dict(read_error=type(exc).__name__)
                        if 'error' in current:
                            item['last_read_error'] = current['error']
                log = Path(spec['log'])
                try:
                    if not log.exists():
                        previous_poll = now
                    elif log.stat().st_size > 8 * 1024 * 1024:
                        record['last_log_poll_error'] = dict(type='BoundedLogLimitExceeded')
                    elif warning_present(log.read_bytes()):
                        if record['warning_observed_window'] is None:
                            record['warning_observed_window'] = dict(after_utc=previous_poll,by_utc=now,meaning='polling detection interval; not exact emission timestamp')
                    else:
                        previous_poll = now
                except OSError as exc:
                    record['last_log_poll_error'] = dict(type=type(exc).__name__, winerror=getattr(exc,'winerror',None))
                if process.poll() is not None:
                    record['descendants_alive_at_wrapper_exit'] = job.pids()
                    break
                if time.monotonic() >= deadline:
                    record['timed_out'] = True
                    job.stop()
                    process.wait(timeout=10)
                    break
                time.sleep(.1)
            record['exit_code'] = process.returncode
    finally:
        # On error/interrupt only processes in this freshly created job are ended.
        record['cleanup_started_utc'] = utc_now()
        log = Path(spec['log'])
        try:
            if log.exists():
                if log.stat().st_size > 8 * 1024 * 1024:
                    raise ValueError('Log exceeded bounded snapshot size')
                snapshot = log.read_bytes()
            else:
                snapshot = b''
            (folder / 'append.before-cleanup.log').write_bytes(snapshot)
            record['pre_cleanup_log_sha256'] = sha256(snapshot).hexdigest()
        except (OSError, ValueError) as exc:
            record['pre_cleanup_snapshot_error'] = type(exc).__name__
        job.stop()
        job.close()
        if process is not None:
            if process.poll() is None:
                process.terminate()  # Waiting wrapper cannot escape an unassigned gate.
            process.wait(timeout=10)
        record['ended_utc'] = utc_now()
        save(folder / 'process-record.json', record)
    return record


def ime_child():
    if os.environ.get('DIAG_WARNING_VISIBLE_GUI') != 'yes' or not Path(os.environ.get('DIAG_WARNING_GATE', '__absent_gate__')).is_file():
        raise ValueError('Internal child requires an explicitly released visible-GUI experiment gate')
    arm = os.environ['DIAG_WARNING_ARM']
    source = Path(os.environ['DIAG_WARNING_SOURCE']).resolve()
    meta_path = Path(os.environ['DIAG_WARNING_CHILD_META'])
    meta = dict(pid=os.getpid(), arm=arm, started_utc=utc_now(), ime_called=False, ime_return=None)
    if arm == 'ime_disabled':
        imm = ctypes.WinDLL('imm32', use_last_error=True)
        imm.ImmDisableIME.argtypes = [wintypes.DWORD]
        imm.ImmDisableIME.restype = wintypes.BOOL
        kernel = ctypes.WinDLL('kernel32')
        kernel.GetCurrentThreadId.restype = wintypes.DWORD
        meta.update(ime_called=True, ime_thread_argument=0, ime_thread_id=kernel.GetCurrentThreadId(),
                    ime_return=bool(imm.ImmDisableIME(0)), ime_call_utc=utc_now())
    elif arm != 'ime_normal':
        raise ValueError('Unknown wrapper arm')
    save(meta_path, meta)
    if arm == 'ime_disabled' and not meta['ime_return']:
        return 42
    sys.path.insert(0, str(source.parent))
    sys.argv = [str(source), '--self-test']
    runpy.run_path(str(source), run_name='__main__')
    return 0


def evaluate_trial(folder, arm):
    folder = Path(folder)
    process = read(folder / 'process-record.json')
    log = folder / 'append.log'
    raw = log.read_bytes() if log.exists() else b''
    snapshot = folder / 'append.before-cleanup.log'
    before_cleanup = snapshot.read_bytes() if snapshot.exists() else raw
    marker = folder / 'data/self-test.ok'
    meta = read(folder / 'child-meta.json') if (folder / 'child-meta.json').is_file() else None
    status = process.get('exit_code') == 0 and not process['timed_out'] and marker.is_file() and marker.read_text(encoding='utf-8') == 'Desktop smoke test passed'
    if 'cleanup_started_utc' in process:
        status = status and snapshot.exists() and sha256(before_cleanup).hexdigest() == process.get('pre_cleanup_log_sha256')
    if arm != 'original':
        status = status and meta is not None and meta['arm'] == arm
    if arm == 'ime_disabled':
        status = status and meta.get('ime_return') is True
    return dict(arm=arm, valid_execution=bool(status), warning=warning_present(before_cleanup), warning_anytime=warning_present(raw),log_bytes=len(raw),
                log_sha256=digest(log) if log.exists() else None, child_meta=meta,
                warning_outside_append=any(warning_present(p.read_bytes()) for p in [folder/'launcher.stdout',folder/'launcher.stderr'] if p.exists()),
                evidence_complete=False, root_cause_closed=False, formal_release_approved=False)


def execute(session, phase, *, allow_visible_gui=False, timeout=90):
    if not allow_visible_gui:
        raise ValueError('GUI execution requires --allow-visible-gui; prepare/analyze are background-only')
    session = Path(session).resolve()
    manifest = read(session / 'session.json')
    if manifest['schema'] != 1 or manifest['baseline_commit'] != BASELINE:
        raise ValueError('Unknown experiment manifest')
    if Path(manifest['source']).resolve() != session / 'baseline/desktop.py' or Path(manifest['driver']).resolve() != Path(__file__).resolve():
        raise ValueError('Manifest source or driver path changed')
    if digest(__file__) != manifest['driver_sha256'] or digest(session / 'trial.ps1') != manifest['powershell_sha256'] or digest(manifest['python']) != manifest['python_sha256'] or digest(manifest['powershell']) != manifest['powershell_exe_sha256']:
        raise ValueError('Driver, PowerShell wrapper or interpreter changed; prepare a new session')
    for name, expected in manifest['baseline_sha256'].items():
        if digest(session / 'baseline' / name) != expected:
            raise ValueError('Baseline export changed; refusing experiment')
    shell = manifest['powershell']
    for trial in manifest['plan']:
        if trial['phase'] != phase:
            continue
        folder = session / f'{phase}-{trial["iteration"]:02d}-{trial["arm"]}'
        folder.mkdir()  # Never reuse old markers/logs, even after an interrupted run.
        (folder / 'data').mkdir()
        spec = dict(arm=trial['arm'], python=manifest['python'], source=manifest['source'],
                    driver=manifest['driver'], data=str(folder / 'data'), log=str(folder / 'append.log'),
                    working_dir=manifest['working_dir'],
                    gate=str(folder / 'go'), child_meta=str(folder / 'child-meta.json'),
                    wrapper_result=str(folder / 'wrapper-result.json'), wrapper_error=str(folder / 'wrapper-error.txt'))
        save(folder / 'spec.json', spec)
        record = run_trial([shell, '-NoProfile', '-File', str(session / 'trial.ps1'), '-Spec', str(folder / 'spec.json')], folder, timeout, spec)
        verdict = evaluate_trial(folder, trial['arm'])
        save(folder / 'verdict.json', verdict)
        print(json.dumps(dict(trial=folder.name, **verdict), ensure_ascii=False), flush=True)
        if not verdict['valid_execution']:
            raise RuntimeError('Invalid trial; inspect retained records before creating a new session')


def candidate_events(csv_path, allowed_pids):
    """CSV is only an event index. PML stack/time review remains mandatory."""
    aliases = {'pid': ('PID',), 'operation': ('Operation', '操作'), 'result': ('Result', '结果'),
               'path': ('Path', '路径'), 'time': ('Time of Day', 'Time', '时间'), 'process': ('Process Name', '进程名称', '进程名')}
    data = Path(csv_path).read_bytes()
    text = data.decode('utf-16') if data.startswith((b'\xff\xfe', b'\xfe\xff')) else data.decode('utf-8-sig')
    import io
    reader = csv.DictReader(io.StringIO(text))
    fields = {key: next((name for name in names if name in (reader.fieldnames or [])), None) for key, names in aliases.items()}
    if any(fields[k] is None for k in ('pid', 'operation', 'result', 'path', 'time')):
        raise ValueError('Unrecognized Procmon CSV columns; no silent empty result')
    events = []
    for row in reader:
        try:
            pid = int(row[fields['pid']])
        except (ValueError, TypeError):
            continue
        if pid not in allowed_pids or row[fields['operation']] not in OPERATIONS or row[fields['result']] not in RESULTS:
            continue
        events.append(dict(pid=pid, operation=row[fields['operation']], result=row[fields['result']],
                           path=row[fields['path']], time_text=row[fields['time']],
                           status='candidate only; exact trial/time/PID reuse and PML native stack require review'))
    return events


def analyze(session, csv_path=None, pml=None):
    session = Path(session).resolve()
    manifest = read(session / 'session.json')
    groups = {}
    pids = set()
    trials = []
    for path in sorted(session.glob('*/verdict.json')):
        stored = read(path)
        verdict = evaluate_trial(path.parent, stored['arm'])
        verdict['verdict_matches_current_evidence'] = all(verdict[k] == stored.get(k) for k in verdict)
        if not verdict['verdict_matches_current_evidence']:
            verdict['valid_execution'] = False
        process = read(path.parent / 'process-record.json')
        pids.update(int(key) for key in process['processes'])
        row = groups.setdefault(verdict['arm'], dict(total=0, valid=0, warnings_in_valid=0))
        row['total'] += 1
        row['valid'] += int(verdict['valid_execution'])
        row['warnings_in_valid'] += int(verdict['warning'] and verdict['valid_execution'])
        trials.append(dict(trial=path.parent.name, **verdict))
    planned = {}
    for trial in manifest['plan']:
        planned[trial['arm']] = planned.get(trial['arm'], 0) + 1
    result = dict(groups=groups, planned_per_arm=planned, trials=trials, pids=sorted(pids),
                  started_without_verdict=[p.parent.name for p in session.glob('*/spec.json') if not (p.parent / 'verdict.json').is_file()],
                  trace_events=candidate_events(csv_path, pids) if csv_path else [],
                  trace_csv_provided=bool(csv_path), trace_pml=dict(bytes=Path(pml).stat().st_size,sha256=digest(pml),status='provided but not stack-reviewed') if pml else None,
                  timing='polling intervals and process boundaries are not exact stderr emission timestamps',
                  interpretation='descriptive counts only; no p-value, causal verdict or release authorization',
                  root_cause_closed=False, formal_release_approved=False)
    save(session / 'summary.json', result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    p = commands.add_parser('prepare');p.add_argument('--out',required=True);p.add_argument('--python',default=sys.executable);p.add_argument('--powershell');p.add_argument('--repetitions',type=int,default=10)
    p = commands.add_parser('run');p.add_argument('--session',required=True);p.add_argument('--phase',choices=['A','B'],required=True);p.add_argument('--allow-visible-gui',action='store_true');p.add_argument('--timeout',type=int,default=90)
    p = commands.add_parser('analyze');p.add_argument('--session',required=True);p.add_argument('--procmon-csv');p.add_argument('--pml')
    commands.add_parser('_ime_child')
    args = parser.parse_args()
    if args.command == 'prepare':
        m = prepare(args.out,args.python,args.repetitions,args.powershell);print(json.dumps(dict(session=str(Path(args.out).resolve()),trials=len(m['plan']),gui_started=False)))
    elif args.command == 'run':
        if not 5 <= args.timeout <= 300:parser.error('timeout must be 5–300 seconds')
        execute(args.session,args.phase,allow_visible_gui=args.allow_visible_gui,timeout=args.timeout)
    elif args.command == 'analyze':
        r = analyze(args.session,args.procmon_csv,args.pml);print(json.dumps(dict(groups=r['groups'],root_cause_closed=False)))
    else:
        return ime_child()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
