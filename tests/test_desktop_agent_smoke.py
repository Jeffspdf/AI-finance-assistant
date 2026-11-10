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
    assert report['scale'] == 1.5
    fixes=report['rc2_controls']
    assert fixes['help_singleton'] and fixes['agent_help_checked'] and fixes['help_chapters']==12
    assert fixes['preview_readonly'] and fixes['default_deleted_kept'] and fixes['explicit_restore_same_id']
    assert fixes['original_ledger_unchanged'] and not fixes['callback_errors']
    # Windows/Tk can constrain a requested client size on a hosted desktop.
    # Keep the minimum usable viewport and requested upper bounds; the smoke
    # already checks overflow, scrolling, footer reachability and real controls.
    width, height = report['layouts']['planner']['size']
    assert 1140 <= width <= 1590 and 810 <= height <= 948, (width, height)
