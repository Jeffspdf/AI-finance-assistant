"""Run bounded 1920x1080 / 150% UI acceptance; never modify system display settings."""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import json,os,sys

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))


def run(output):
    with TemporaryDirectory(prefix='.test_closure_ui_',dir=ROOT) as temp,patch('finance.ui_scale.work_area',return_value=(0,0,1920,1020)):
        os.environ['FINANCE_DESKTOP_DATA_DIR']=temp;os.environ['FINANCE_UI_SCALE']='1.5'
        from desktop import Desktop
        from finance.feedback_acceptance import verify_feedback_controls
        app=Desktop();app.update()
        try:report=verify_feedback_controls(app,output)
        finally:app.destroy()
        report['display']='simulated 1920x1080 work area with 150% Tk scale'
        Path(output,'results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        return report


if __name__=='__main__':print(json.dumps(run(sys.argv[1]),ensure_ascii=False))
