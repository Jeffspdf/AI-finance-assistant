"""Exercise generator controls with isolated data, including cancel and space guards."""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import ctypes,json,os,sys,time
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))


def descendants(widget):
    for child in widget.winfo_children():yield child;yield from descendants(child)


def button(widget,text):return next(c for c in descendants(widget) if c.winfo_class()=='Button' and c.cget('text')==text)


def labels(widget):return '\n'.join(str(c.cget('text')) for c in descendants(widget) if c.winfo_class()=='Label')


with TemporaryDirectory(prefix='.test_demo_generator_ui_',dir=ROOT) as temp:
    os.environ['FINANCE_DESKTOP_DATA_DIR']=temp
    from desktop import Desktop
    from finance.ui_scale import px,set_window_geometry
    from PIL import ImageGrab
    app=Desktop();errors=[];app.report_callback_exception=lambda *args:errors.append(str(args))
    app.toggle_animation();set_window_geometry(app,1100,720,minimum=(1100,720));app.update()
    button(app.main,'生成演示账单').invoke();app.update();window=app.demo_generator_dialog
    entries=[c for c in descendants(window) if c.winfo_class()=='TEntry']
    for entry,value in zip(entries,['2024-02','2','4','10.00','20.00','2000.00']):entry.delete(0,'end');entry.insert(0,value)
    next(c for c in descendants(window) if c.winfo_class()=='Checkbutton' and c.cget('text').startswith('每月增加1笔退款')).invoke()
    button(window,'预览数量').invoke();app.update();assert '共 14 笔' in labels(window)
    entry=entries[0];entry.delete(0,'end');entry.insert(0,'2024-13');button(window,'生成演示账单').invoke();app.update()
    assert '起始月份' in labels(window) and not app.store.import_batches()
    entry.delete(0,'end');entry.insert(0,'2024-02')
    button(window,'生成演示账单').invoke();button(window,'生成演示账单').invoke()
    end=time.monotonic()+8
    while time.monotonic()<end:
        app.update();time.sleep(.01)
        if '已生成 14 笔' in labels(window):break
    else:raise AssertionError('Generator did not finish')
    assert len(app.store.import_batches())==1 and app.selected_month=='2024-03'
    assert len(app.store.source_transactions().query("channel=='演示生成'"))==14
    # Save/cancel replacement must leave the generated batch intact.
    checkbox=next(c for c in descendants(window) if c.winfo_class()=='Checkbutton' and c.cget('text').startswith('替换所选月份'))
    checkbox.invoke()
    with patch('finance.desktop_demo.messagebox.askyesno',return_value=False):button(window,'生成演示账单').invoke()
    app.update();assert len(app.store.import_batches())==1
    clipped=[]
    for c in descendants(window):
        if c.winfo_class() in ('Button','TEntry','TCombobox') and c.winfo_ismapped():
            if c.winfo_rootx()+c.winfo_width()>window.winfo_rootx()+window.winfo_width()+px(2):clipped.append(c.winfo_class())
    actions=[c for c in descendants(window) if c.winfo_class()=='Button']
    assert all(c.winfo_ismapped() and c.winfo_rooty()+c.winfo_height()<=window.winfo_rooty()+window.winfo_height()+px(2) for c in actions)
    out=ROOT/'docs/screenshots/demo_generator';out.mkdir(parents=True,exist_ok=True)
    ImageGrab.grab(window=ctypes.windll.user32.GetParent(window.winfo_id())).save(out/'dialog.png')
    button(window,'查看生成账单').invoke();app.update()
    assert app.page=='records' and app.selected_month=='2024-03' and len(app.record_rows)==7
    ImageGrab.grab(window=ctypes.windll.user32.GetParent(app.winfo_id())).save(out/'records.png')
    app.switch_space();app.update();assert app.mode=='personal' and app.store.source_transactions().empty
    assert not any(c.winfo_class()=='Button' and c.cget('text')=='生成演示账单' for c in descendants(app.main))
    with patch('finance.desktop_demo.messagebox.showinfo'):app.demo_generator_window()
    assert not any(c.winfo_class()=='Toplevel' for c in app.winfo_children())
    report=dict(data='synthetic only',months=['2024-02','2024-03'],rows_added=14,batches=1,
                double_click_did_not_duplicate=True,cancel_replace_preserved_rows=True,personal_unchanged=True,
                native_scale=app.ui_scale,horizontal_clipping=clipped,callback_errors=errors)
    (ROOT/'docs/demo_generator_ui_validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    app.destroy();assert not errors and not clipped,(errors,clipped)
print('DEMO_GENERATOR_UI_PREVIEW_GENERATE_CANCEL_SPACE_GUARD_OK')
