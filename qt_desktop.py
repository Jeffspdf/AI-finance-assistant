"""Launch the independent PySide6 client or its synthetic smoke test."""

import argparse
import os
from pathlib import Path
import sys
from tempfile import gettempdir

from PySide6.QtWidgets import QApplication

from qt_finance.window import FinanceWindow


def main(argv=None):
    parser = argparse.ArgumentParser(description='AI理财小助手 PySide6 预览')
    parser.add_argument('--data-dir', type=Path)
    parser.add_argument('--self-test', nargs='?', type=Path, const=True,
                        metavar='OUTPUT', help='运行合成账本自检，默认输出到系统临时目录的 AIFinanceHelperQt-self-test')
    parser.add_argument('--tk-backup-fixture', type=Path)
    parser.add_argument('--qt-return-fixture', type=Path)
    parser.add_argument('--live-model-settings', type=Path)
    args = parser.parse_args(argv)
    if args.self_test is True:
        args.self_test = Path(gettempdir()) / 'AIFinanceHelperQt-self-test'
    if (args.tk_backup_fixture or args.qt_return_fixture or args.live_model_settings) and not args.self_test:
        parser.error('测试选项仅可与 --self-test 一起使用。')
    if args.qt_return_fixture and (not args.tk_backup_fixture or args.live_model_settings):
        parser.error('双向迁移测试需要 Tk 合成备份。')
    app = QApplication([sys.argv[0]])
    app.setApplicationName('AI理财小助手 · Qt 预览')
    app.setStyle('Fusion')
    if args.self_test:
        from qt_finance.smoke import exercise, exercise_live_model
        try:
            if args.live_model_settings:
                exercise_live_model(app, args.self_test, args.live_model_settings)
            else:
                exercise(app, args.self_test, args.tk_backup_fixture, args.qt_return_fixture)
        except Exception:
            from traceback import format_exc
            args.self_test.mkdir(parents=True, exist_ok=True)
            failure = format_exc()
            (args.self_test / 'error.txt').write_text(failure, encoding='utf-8')
            if sys.stderr is not None:
                sys.stderr.write(failure)
            return 1
        return 0
    base = Path(os.environ.get('LOCALAPPDATA') or Path.home())
    directory = (args.data_dir or base / 'ShiguangLedger-QtPreview').resolve()
    tkinter_directory = Path(os.environ.get('FINANCE_DESKTOP_DATA_DIR') or base / 'ShiguangLedger').resolve()
    if directory == tkinter_directory:
        parser.error('请选择独立的 Qt 数据目录。需要使用 Tk 账本时，可在 Qt 里恢复一份备份。')
    window = FinanceWindow(directory)
    window.show()
    code = app.exec()
    window.jobs.pool.waitForDone()
    return code


if __name__ == '__main__':
    raise SystemExit(main())
