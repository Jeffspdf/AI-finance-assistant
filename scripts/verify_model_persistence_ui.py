"""Isolated synthetic credentials; actual DPAPI; no external API requests."""
from pathlib import Path
from tempfile import TemporaryDirectory
import os
import subprocess
import sys
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))


def children(widget):
    for child in widget.winfo_children():yield child;yield from children(child)


def phase(directory,name):
    os.environ['FINANCE_DESKTOP_DATA_DIR']=directory
    from desktop import Desktop
    from finance.model_settings import ModelSettings
    with patch('finance.llm.urlopen',side_effect=AssertionError('Unexpected model request')):
        app=Desktop();app.update()
        if name in ('reopen','endpoint','clear'):
            assert app.model_client is not None and app.model_key=='synthetic-ui-key-only'
        if name=='session_reopen':
            assert app.model_client is None and not app.model_key
            app.destroy();print(name+'_OK');return
        if name=='cleared_reopen':
            assert app.model_client is None and not app.model_key
            assert app.model_settings['base_url']=='https://example.com/v1'
            app.destroy();print(name+'_OK');return
        app.show_model_settings();app.update()
        window=next(c for c in app.winfo_children() if c.winfo_class()=='Toplevel')
        entries=[w for w in children(window) if w.winfo_class()=='TEntry']
        checks=[w for w in children(window) if w.winfo_class()=='Checkbutton']
        def button(text):return next(w for w in children(window) if w.winfo_class()=='Button' and w.cget('text')==text)
        if name in ('save','session'):
            for entry,value in zip(entries,['https://example.com/v1','synthetic-model','synthetic-ui-key-only']):
                entry.delete(0,'end');entry.insert(0,value)
            if not bool(app.getvar(checks[0].cget('variable'))):checks[0].invoke()
            if name=='session' and bool(app.getvar(checks[1].cget('variable'))):checks[1].invoke()
            button('保存设置').invoke();app.update()
            assert not window.winfo_exists() and app.model_client is not None
        elif name=='reopen':
            assert entries[2].get()=='synthetic-ui-key-only' and entries[2].cget('show')=='•'
            assert bool(app.getvar(checks[0].cget('variable')))
            window.destroy()
            import time
            app.show('assistant');app.ask('累计结余是多少？')
            deadline=time.monotonic()+5
            while getattr(app,'chat_busy',False) and time.monotonic()<deadline:
                app.update();time.sleep(.01)
            assert '结余为' in app.answer.cget('text')
        elif name=='endpoint':
            entries[0].delete(0,'end');entries[0].insert(0,'https://other.example/v1');app.update()
            assert entries[2].get()==''
            window.destroy()
            assert ModelSettings(directory).load().api_key=='synthetic-ui-key-only'
        elif name=='clear':
            button('清除密钥').invoke();app.update()
            assert app.model_client is None and not app.model_key and not entries[2].get()
            assert not ModelSettings(directory).load().enabled
            window.destroy()
        app.destroy()
    print(name+'_OK')


if __name__=='__main__':
    if len(sys.argv)>1:phase(sys.argv[1],sys.argv[2])
    else:
        with TemporaryDirectory(prefix='.test_model_ui_',dir=ROOT) as temp:
            for name in ['save','reopen','endpoint','clear','cleared_reopen','session','session_reopen']:
                subprocess.run([sys.executable,'-X','utf8',__file__,temp,name],check=True,cwd=ROOT)
        print('MODEL_UI_SAVE_RESTART_CLEAR_SESSION_ENDPOINT_OK')
