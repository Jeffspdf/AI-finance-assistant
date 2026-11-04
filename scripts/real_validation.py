"""Initialize private collection sheets or summarize an offline pilot study."""
from pathlib import Path
import argparse,json,sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from finance.real_validation import initialize,evaluate

def main():
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['init','evaluate'])
    parser.add_argument('--directory',type=Path,default=ROOT/'.validation-private')
    args=parser.parse_args()
    try:
        if args.action=='init':
            initialize(args.directory);print('空采集模板已创建；尚无真实样本或评测结论。')
        else:
            report=evaluate(args.directory)
            (args.directory/'summary.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps({'status':report['status'],'missing_requirements':report['missing_requirements']},ensure_ascii=False))
    except (OSError,ValueError) as exc:parser.exit(2,'采集表尚未就绪：'+str(exc)+'\n')

if __name__=='__main__':main()
