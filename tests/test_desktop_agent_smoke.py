"""Full desktop entry/prefill/layout in an owned test desktop, never foreground."""
from pathlib import Path
import json
import subprocess
import sys
import pytest


@pytest.mark.skipif(sys.platform != 'win32', reason='Native Windows isolated-desktop acceptance')
def test_actual_desktop_savings_entry_and_150_percent_layout(tmp_path):
    root = Path(__file__).resolve().parents[1]
    process = subprocess.run([sys.executable, str(root/'scripts/verify_savings_planner_ui.py'), str(tmp_path/'ui')],
                             cwd=root, capture_output=True, encoding='utf-8', errors='replace', timeout=100)
    assert process.returncode == 0, process.stdout + process.stderr
    report = json.loads((tmp_path/'ui/results.json').read_text(encoding='utf-8'))
    assert len(report['checks']) == 4 and report['source_ledger_unchanged']
    assert not report['callback_errors'] and not report['layouts']['planner']['horizontal_spill']
    assert report['scale'] == 1.5 and report['layouts']['planner']['size'] == [1590, 948]
