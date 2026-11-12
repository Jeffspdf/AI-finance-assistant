"""Create a synthetic backup using the Tk course client's backup button."""

import json
import os
from pathlib import Path
import sys
from unittest.mock import patch


if __name__ == '__main__':
    output = Path(sys.argv[1]).resolve()
    output.mkdir(parents=True, exist_ok=True)
    incoming = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else None
    directory = output / ('tk-return' if incoming else 'tk-source')
    os.environ['FINANCE_DESKTOP_DATA_DIR'] = str(directory)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    try:
        from desktop import Desktop
        from finance.feedback_acceptance import button

        app = Desktop()
        try:
            app.withdraw()
            if app.mode != 'personal':
                app.switch_space()
            store = app.store
            assert app.mode == 'personal' and store.source_transactions().empty
            if incoming:
                before = incoming.read_bytes()
                app.backup_window()
                with patch('finance.desktop_m1a.filedialog.askopenfilename', return_value=str(incoming)), \
                     patch('finance.desktop_m1a.messagebox.askyesno', return_value=True):
                    button(app, '选择备份并预览恢复').invoke()
                assert sorted(store.source_transactions().amount_cents) == [4200, 520000]
                assert store.budgets('2026-10') == {'餐饮': 22500}
                goals = store.goals().to_dict('records')
                assert len(goals) == 1 and goals[0]['target_cents'] == 600000
                assert store.contributions(goals[0]['id']).amount_cents.tolist() == [10000]
                assert incoming.read_bytes() == before and store.managed_backups()
                (output / 'results.json').write_text(json.dumps({'restored_qt_budget_cents': 22500}), encoding='utf-8')
                raise SystemExit(0)
            store.add_manual_transaction('2026-10-15', '合成餐厅', 4200, '支出', '餐饮')
            store.add_manual_transaction('2026-10-16', '合成公司', 520000, '收入', '工资')
            store.save_budget('2026-10', '餐饮', 20000)
            store.save_goal(dict(name='合成应急储蓄', target_cents=600000, saved_cents=5000,
                                 start_date='2026-10-01', deadline='2027-04-01',
                                 income_cents=520000, budget_cents=485000))
            store.add_contribution(int(store.goals().iloc[0]['id']), '2026-10-20', 10000)
            source = Path(store.path)
            before = source.read_bytes()
            app.backup_window()
            button(app, '创建备份').invoke()
            backups = store.managed_backups()
            assert len(backups) == 1 and source.read_bytes() == before
            (output / 'results.json').write_text(json.dumps({'backup': backups[0]['path']}), encoding='utf-8')
        finally:
            app.destroy()
    except Exception:
        from traceback import format_exc
        (output / 'error.txt').write_text(format_exc(), encoding='utf-8')
        raise
