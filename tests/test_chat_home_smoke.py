"""Actual Tk controls, read-only ledger and late-result smoke on Windows."""

import json
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.skipif(sys.platform != 'win32', reason='Native Windows isolated-desktop acceptance')
def test_read_only_chat_home_controls(tmp_path):
    root = Path(__file__).resolve().parents[1]
    output = tmp_path / 'ui'
    process = subprocess.run(
        [sys.executable, str(root / 'scripts/verify_savings_planner_ui.py'), str(output), '--chat-home'],
        cwd=root, capture_output=True, encoding='utf-8', errors='replace', timeout=100,
    )
    assert process.returncode == 0, process.stdout + process.stderr
    report = json.loads((output / 'results.json').read_text(encoding='utf-8'))
    assert report['late_results_rejected'] == ['stop', 'clear', 'switch_space']
    assert report['ledger_bytes_unchanged'] and report['isolated_histories']
