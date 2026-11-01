"""Isolated UI layout checks. Overrides simulate Tk scale, not OS display changes."""
import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from datetime import date
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


def run(scale):
    from PIL import ImageGrab
    import tkinter as tk
    import tkinter.font as tkfont
    from tkinter import ttk
    from finance.ui_scale import px, set_window_geometry, work_area
    from finance.storage import Store
    from finance.importer import parse_statement
    from finance.demo import demo_bytes
    with TemporaryDirectory(prefix='.test_dpi_',dir=ROOT) as temp:
        os.environ['FINANCE_DESKTOP_DATA_DIR']=temp
        if scale!='native':os.environ['FINANCE_UI_SCALE']=scale
        else:os.environ.pop('FINANCE_UI_SCALE',None)
        store=Store(Path(temp)/'demo.sqlite3')
        store.import_rows(parse_statement(demo_bytes(date(2026,10,9)),'dpi-demo.csv').rows)
        (Path(temp)/'preferences.json').write_text(json.dumps({'space':'demo','month':'2026-09'}))
        from desktop import Desktop
        app=Desktop();errors=[]
        app.report_callback_exception=lambda *args:errors.append(str(args))
        app.update();app.pause=True
        app.toggle_animation()
        out=ROOT/'docs'/'screenshots'/'dpi';out.mkdir(parents=True,exist_ok=True)
        area=work_area(app)
        report={'mode':'actual system DPI' if scale=='native' else 'simulated Tk scale','scale':app.ui_scale,'tk_scaling':float(app.tk.call('tk','scaling')),'dpi':app.winfo_fpixels('1i'),'work_area':area,'window':[app.winfo_width(),app.winfo_height()],'font_linespace':tkfont.Font(root=app,font=('Microsoft YaHei UI',10)).metrics('linespace'),'rowheight':int(ttk.Style(app).lookup('Treeview','rowheight')),'pages':{},'dialogs':{}}
        assert abs(report['rowheight']-44*app.ui_scale)<=1
        assert report['font_linespace']>=round(17*app.ui_scale)
        if abs(app.ui_scale-1.5)<.01:assert report['font_linespace']>=27
        for minimum in (False,True):
            if minimum:set_window_geometry(app,1100,720,minimum=(1100,720))
            for page in ['home','records','budget_page','plan','assistant','settings']:
                app.show(page);app.update();app.content_canvas.yview_moveto(0);app.update()
                assert app.winfo_rooty()+app.winfo_height()<=area[3]
                if page=='assistant':
                    app.ask('2026-09餐饮花了多少？');app.ask('餐饮占比呢？');app.update()
                assert app.guide_action.winfo_ismapped() and app.guide_action.winfo_height()>=px(30)
                clipped=[]
                for widget in descendants(app.main):
                    if not widget.winfo_ismapped():continue
                    if widget.winfo_class() not in ('Button','TEntry','TCombobox','Frame','Label','Treeview'):continue
                    # All content shares one vertical viewport. Horizontal spill is never intentional.
                    x=widget.winfo_rootx()-app.main.winfo_rootx()
                    if x < -px(2) or x+widget.winfo_width()>app.main.winfo_width()+px(2):
                        clipped.append([widget.winfo_class(),str(widget.cget('text'))[:40] if 'text' in widget.keys() else '',x,widget.winfo_width(),app.main.winfo_width()])
                key=page+('_minimum' if minimum else '')
                report['pages'][key]={'width':app.main.winfo_width(),'horizontal_spill':clipped}
                if not minimum or page=='home':ImageGrab.grab(window=ctypes.windll.user32.GetParent(app.winfo_id())).save(out/f'{scale}_{key}.png')
                # Record evidence before failing so clipped controls can be fixed.
            if not minimum:report['default_window']=[app.winfo_width(),app.winfo_height()]
        # Forms are checked without calling any remote API or writing personal data.
        def open_edit():
            app.show('records');app.update();app.tree.selection_set(app.tree.get_children()[0]);app.edit_selected()
        def open_contribution():
            app.show('plan');app.update()
            with patch('desktop.messagebox.showinfo'):app.save_plan()
            app.update();app.goal_tree.selection_set(app.goal_tree.get_children()[0]);app.contribution_window()
        def open_evidence():app.chat_evidence(app.store.transactions().head(4).to_dict('records'))
        for name,open_dialog in [('quick_entry',app.quick_entry),('edit',open_edit),('contribution',open_contribution),('evidence',open_evidence),('model_settings',app.show_model_settings),('search',app.command_palette),('rules',app.learned_rules_window),('updates',app.show_updates),('pet',app.show_pet)]:
            open_dialog();app.update()
            window=next(child for child in app.winfo_children() if child.winfo_class()=='Toplevel')
            assert window.winfo_rooty()+window.winfo_height()<=area[3],name+' overlaps taskbar'
            clipped=[]
            for widget in descendants(window):
                if widget.winfo_class()!='Button':continue
                if not widget.winfo_ismapped():clipped.append(str(widget.cget('text')))
                elif widget.winfo_rooty()+widget.winfo_height()>window.winfo_rooty()+window.winfo_height()+px(2):clipped.append(str(widget.cget('text')))
                elif widget.winfo_height()<widget.winfo_reqheight()-px(2):clipped.append(str(widget.cget('text'))+' (height)')
            report['dialogs'][name]={'size':[window.winfo_width(),window.winfo_height()],'clipped_buttons':clipped}
            ImageGrab.grab(window=ctypes.windll.user32.GetParent(window.winfo_id())).save(out/f'{scale}_{name}.png')
            window.destroy();app.update()
        report['callback_errors']=errors
        (out/f'{scale}_metrics.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(report,ensure_ascii=False),flush=True)
        app.destroy()
        assert not errors,errors
        assert all(not page['horizontal_spill'] for page in report['pages'].values()),'Horizontal spill; see metrics'
        assert all(not dialog['clipped_buttons'] for dialog in report['dialogs'].values()),'Clipped dialog controls; see metrics'


if __name__=='__main__':
    if len(sys.argv)>1:run(sys.argv[1])
    else:
        for scale in ['1','1.25','1.5','2','native']:
            subprocess.run([sys.executable,__file__,scale],check=True,cwd=ROOT)
        print('DPI_LAYOUT_ALL_SCALES_OK (simulated 100/125/150/200 + native)')
