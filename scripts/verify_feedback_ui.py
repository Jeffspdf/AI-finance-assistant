"""Course feedback screenshots, using isolated synthetic data and a 1920x1080 viewport."""
import os
import sys
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def run(stage):
    import ctypes
    from PIL import ImageGrab
    from scripts.m2_ui_fixture import seed_ui_refunds
    with TemporaryDirectory(prefix='.test_feedback_', dir=ROOT) as temp, patch('finance.ui_scale.work_area',return_value=(0,0,1920,1020)):
        os.environ['FINANCE_DESKTOP_DATA_DIR'] = temp
        os.environ['FINANCE_UI_SCALE'] = '1.5'
        seed_ui_refunds(Path(temp) / 'demo.sqlite3')
        (Path(temp) / 'preferences.json').write_text(json.dumps({'space':'demo','month':'2026-09'}))
        from desktop import Desktop
        app = Desktop()
        app.update()
        errors=[]
        app.report_callback_exception=lambda *args:errors.append(str(args))
        output=ROOT/'docs'/'screenshots'/'feedback'/stage
        output.mkdir(parents=True,exist_ok=True)
        report={'scale':app.ui_scale,'viewport':[1920,1080],'viewport_type':'simulated 1920x1080 work area with 60px taskbar and 150% Tk scale','window':[app.winfo_width(),app.winfo_height()],'pages':{}}
        assert app.winfo_width()<=1920 and app.winfo_height()+72<=1020
        for name in ('home','records','records_selected','budget_page','plan','assistant','settings'):
            page='records' if name=='records_selected' else name
            app.show(page);app.update()
            app.content_canvas.yview_moveto(0);app.update()
            if page=='assistant':
                app.ask('2026-09餐饮花了多少？');app.update()
            if name=='records_selected' and hasattr(app,'selection_bar'):
                app.tree.selection_set(app.tree.get_children()[0]);app.update()
            def descendants(widget):
                for child in widget.winfo_children():
                    yield child
                    yield from descendants(child)
            clipped=[]
            for w in descendants(app.main):
                if not w.winfo_ismapped():continue
                x=w.winfo_rootx()-app.main.winfo_rootx()
                if x < -3 or x+w.winfo_width()>app.main.winfo_width()+3:
                    clipped.append(str(w))
            report['pages'][name]={'horizontal_clipping':clipped}
            ImageGrab.grab(window=ctypes.windll.user32.GetParent(app.winfo_id())).save(output/(name+'.png'))
        report['callback_errors']=errors
        (output/'metrics.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        if '--hold' in sys.argv:
            app.show('records');app.update()
            app.title('课程界面验收 · '+stage)
            # No input injection. Hold only for optional external window capture.
            control=ROOT/'.test_feedback_control.json'
            control.write_text(json.dumps({'page':'records'}))
            def poll():
                try:command=json.loads(control.read_text())
                except (OSError,ValueError):command={}
                if command.get('stop'):app.destroy();return
                if command.get('page') in ('home','records','assistant') and command['page']!=app.page:
                    app.show(command['page']);app.update()
                app.after(300,poll)
            app.after(300,poll);app.after(180000,app.destroy);app.mainloop()
        else:app.destroy()
        if stage=='after':
            assert not errors,errors
            assert not any(p['horizontal_clipping'] for p in report['pages'].values()),report
        print(json.dumps(report,ensure_ascii=False))


if __name__=='__main__':run(sys.argv[1])
