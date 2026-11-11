"""Verify the Qt source or built executable on an inactive Windows desktop."""

import argparse
import json
import os
from pathlib import Path
import sys

from verify_savings_planner_ui import ROOT, isolated_run


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('output', type=Path)
    parser.add_argument('--exe', type=Path)
    parser.add_argument('--scale', default='1.5')
    args = parser.parse_args()
    os.environ['QT_QPA_PLATFORM'] = 'windows'
    os.environ.pop('QT_SCALE_FACTOR', None)
    os.environ['QT_SCREEN_SCALE_FACTORS'] = args.scale
    entry = [args.exe.resolve()] if args.exe else [sys.executable, ROOT / 'qt_desktop.py']
    report = isolated_run(args.output, child_arguments=[*entry, '--self-test', args.output.resolve()])
    assert report['platform'] == 'windows'
    assert abs(report['scale'] - float(args.scale)) < 0.01, report
    print(json.dumps(report, ensure_ascii=False))
