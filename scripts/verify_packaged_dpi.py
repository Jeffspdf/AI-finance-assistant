"""Launch only our packaged EXE with temporary data; verify its native client size."""
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from finance.ui_theme import enable_dpi_awareness
from finance.updates import APP_VERSION


def main():
    enable_dpi_awareness()
    user32=ctypes.windll.user32
    callback_type=ctypes.WINFUNCTYPE(wintypes.BOOL,wintypes.HWND,wintypes.LPARAM)
    user32.EnumWindows.argtypes=[callback_type,wintypes.LPARAM]
    user32.GetWindowThreadProcessId.argtypes=[wintypes.HWND,ctypes.POINTER(wintypes.DWORD)]
    user32.GetClientRect.argtypes=[wintypes.HWND,ctypes.POINTER(wintypes.RECT)]
    user32.PostMessageW.argtypes=[wintypes.HWND,wintypes.UINT,wintypes.WPARAM,wintypes.LPARAM]
    exe=Path(sys.argv[1]) if len(sys.argv)>1 else ROOT/'dist/AIFinanceHelper/AIFinanceHelper.exe'
    with TemporaryDirectory(prefix='.test_packaged_dpi_',dir=ROOT) as temp:
        env=os.environ.copy();env['FINANCE_DESKTOP_DATA_DIR']=temp;env.pop('FINANCE_UI_SCALE',None)
        process=subprocess.Popen([str(exe)],env=env,creationflags=subprocess.CREATE_NO_WINDOW)
        handles=[]
        @callback_type
        def visit(handle,_):
            pid=wintypes.DWORD();user32.GetWindowThreadProcessId(handle,ctypes.byref(pid))
            if pid.value==process.pid and user32.IsWindowVisible(handle):handles.append(handle)
            return True
        try:
            end=time.monotonic()+25
            while time.monotonic()<end:
                handles.clear();user32.EnumWindows(visit,0)
                if handles:break
                assert process.poll() is None,'Packaged app exited before opening'
                time.sleep(.1)
            assert handles,'Packaged window did not open'
            time.sleep(.8)
            rect=wintypes.RECT();user32.GetClientRect(handles[0],ctypes.byref(rect))
            dpi=user32.GetDpiForSystem();s=dpi/96
            size=[rect.right-rect.left,rect.bottom-rect.top]
            assert size[0]==round(1200*s),size
            assert size[1]<=round(800*s),size
            from PIL import ImageGrab
            out=ROOT/'docs/screenshots/dpi'
            ImageGrab.grab(window=handles[0]).save(out/'packaged_native_home.png')
            report={'version':APP_VERSION,'exe':exe.name,'system_dpi':dpi,'client_size':size,'temporary_synthetic_data':True}
            (out/'packaged_native_metrics.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps(report),flush=True)
            user32.PostMessageW(handles[0],0x0010,0,0)
            assert process.wait(timeout=8)==0
        finally:
            if process.poll() is None:process.terminate();process.wait(timeout=8)


if __name__=='__main__':main()
