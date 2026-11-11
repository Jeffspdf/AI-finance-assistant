"""Launch the independent PySide6 client or its synthetic smoke test."""

import argparse
import os
from pathlib import Path
import sys

from PySide6.QtWidgets import QApplication

from qt_finance.window import FinanceWindow


def main(argv=None):
    parser = argparse.ArgumentParser(description='AI理财小助手 PySide6 预览')
    parser.add_argument('--data-dir', type=Path)
    parser.add_argument('--self-test', type=Path, metavar='OUTPUT')
    args = parser.parse_args(argv)
    app = QApplication([sys.argv[0]])
    app.setApplicationName('AI理财小助手 · Qt 预览')
    app.setStyle('Fusion')
    if args.self_test:
        from qt_finance.smoke import exercise
        try:
            exercise(app, args.self_test)
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
