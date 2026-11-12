"""Verify the Qt source or built executable on an inactive Windows desktop."""

import argparse
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

from verify_savings_planner_ui import ROOT, isolated_run


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('output', type=Path)
    parser.add_argument('--exe', type=Path)
    parser.add_argument('--scale', default='1.5')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--tk-migration', action='store_true')
    mode.add_argument('--live-model-settings', type=Path)
    args = parser.parse_args()
    os.environ['QT_QPA_PLATFORM'] = 'windows'
    os.environ.pop('QT_SCALE_FACTOR', None)
    os.environ['QT_SCREEN_SCALE_FACTORS'] = args.scale
    entry = [args.exe.resolve()] if args.exe else [sys.executable, ROOT / 'qt_desktop.py']
    command = [*entry, '--self-test', args.output.resolve()]
    if args.tk_migration:
        args.output.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix='migration-', dir=args.output) as fixture:
            fixture = Path(fixture).resolve()
            result = isolated_run(fixture, child_arguments=[
                sys.executable, ROOT / 'scripts/create_tk_migration_fixture.py', fixture,
            ])
            report = isolated_run(args.output, child_arguments=[*command, '--tk-backup-fixture', result['backup']])
    else:
        if args.live_model_settings:
            command += ['--live-model-settings', args.live_model_settings.resolve()]
        report = isolated_run(args.output, child_arguments=command)
    assert report['platform'] == 'windows'
    assert abs(report['scale'] - float(args.scale)) < 0.01, report
    print(json.dumps(report, ensure_ascii=False))
    if args.live_model_settings and report['status'] != 'passed':
        raise SystemExit(1)
