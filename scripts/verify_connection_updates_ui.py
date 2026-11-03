"""Mock-only UI checks for diagnostic messages and in-app latest downloads."""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.error import URLError,HTTPError
import os,sys,time
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))

def children(w):
    for child in w.winfo_children():yield child;yield from children(child)
def button(w,text):return next(c for c in children(w) if c.winfo_class()=='Button' and c.cget('text')==text)
def text(w):return '\n'.join(str(c.cget('text')) for c in children(w) if c.winfo_class()=='Label')
def wait(app,predicate):
    deadline=time.monotonic()+4
    while time.monotonic()<deadline:
        app.update();time.sleep(.01)
        if predicate():return
    raise AssertionError('UI wait timed out')

with TemporaryDirectory(prefix='.test_connection_update_ui_',dir=ROOT) as temp:
    os.environ['FINANCE_DESKTOP_DATA_DIR']=temp
    from desktop import Desktop
    from finance.updates import APP_VERSION,REPO
    app=Desktop();app.update()
    for error,expected in [(URLError(TimeoutError()),'连接超时'),(HTTPError('',402,'synthetic',{},None),'余额不足')]:
        app.show_model_settings();app.update()
        window=next(c for c in app.winfo_children() if c.winfo_class()=='Toplevel')
        entries=[c for c in children(window) if c.winfo_class()=='TEntry'];entries[1].insert(0,'synthetic-model')
        entries[2].insert(0,'synthetic-test-key')
        with patch('finance.llm.ModelClient.test_connection',side_effect=error):
            button(window,'测试连接').invoke();wait(app,lambda:expected in text(window))
        window.destroy();app.update()
    url=f'https://github.com/{REPO}/releases/download/v{APP_VERSION}/AIFinanceHelper-{APP_VERSION}-Setup-x64.exe'
    release=dict(version=APP_VERSION,newer=False,installer_url=url,sha256='0'*64,notes='Synthetic test',source='official_release_page')
    app.show_updates();app.update();window=app.update_window
    with patch('desktop.check_latest',return_value=release):
        button(window,'检查更新').invoke();wait(app,lambda:'仍可在应用内下载' in text(window))
    assert str(button(window,'下载最新安装包').cget('state'))=='normal'
    assert '当前已是最新版本' in text(window)
    received=[]
    def mock_download(url_arg,directory,progress,cancelled,**kwargs):
        received.append((url_arg,kwargs))
        target=Path(temp)/'synthetic-installer.exe';target.write_bytes(b'MZsynthetic')
        progress(11,11);return str(target),'0'*64
    with patch('desktop.download_installer',side_effect=mock_download),patch('desktop.os.startfile'):
        button(window,'下载最新安装包').invoke();wait(app,lambda:'下载完成' in text(window))
    assert received[0][0]==url and received[0][1]['verify_release'] is True
    with patch('desktop.download_installer',side_effect=ValueError('SHA256 不匹配')):
        button(window,'下载最新安装包').invoke();wait(app,lambda:'SHA256 不匹配' in text(window))
    with patch('desktop.check_latest',side_effect=ValueError('未能确认 GitHub 最新版本，不能判断是否需要更新。')):
        button(window,'检查更新').invoke();wait(app,lambda:'不能判断' in text(window))
    assert str(button(window,'下载最新安装包').cget('state'))=='disabled'
    assert '当前已是最新版本' not in text(window)
    window.destroy();app.destroy()
print('MODEL_DIAGNOSTICS_LATEST_DOWNLOAD_API_FALLBACK_CHECKSUM_UI_OK')
