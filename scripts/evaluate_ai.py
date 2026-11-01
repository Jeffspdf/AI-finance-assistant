"""Reproducible comparison. Default mock-oracle mode is NOT a model benchmark."""
from pathlib import Path
import sys,json,time,argparse,os
from datetime import date
import pandas as pd
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from finance.demo import demo_bytes
from finance.importer import parse_statement
from finance.qa import answer_question,parse_question,validate_model_intent
from finance.llm import ModelClient

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--live',action='store_true');args=parser.parse_args()
    cases=json.loads((ROOT/'samples/ai_evaluation.json').read_text(encoding='utf-8'))
    frame=pd.DataFrame(parse_statement(demo_bytes(date(2026,10,9)),'evaluation-synthetic.csv').rows)
    model=None
    if args.live:
        model=ModelClient(os.environ['FINANCE_LLM_BASE_URL'],os.environ['FINANCE_LLM_MODEL'],os.environ.get('FINANCE_LLM_API_KEY',''),enabled=True)
    output=[]
    for mode in ['rules','live' if args.live else 'mock-oracle']:
        for case in cases:
            class Oracle:
                def parse_question(self,q):return case['intent']
            selected=model if mode=='live' else Oracle() if mode=='mock-oracle' else None
            actual_intent=parse_question(case['question']);fallback=False
            class Capture:
                def parse_question(self,q):
                    nonlocal actual_intent,fallback
                    try:raw=selected.parse_question(q)
                    except Exception:fallback=True;raise
                    actual_intent=validate_model_intent(q,raw)
                    if actual_intent is None:fallback=True
                    return raw
            start=time.perf_counter()
            result=answer_question(frame,case['question'],date(2026,10,9),model=Capture() if selected else None)
            elapsed=time.perf_counter()-start
            expected=case['intent'];intent_ok=actual_intent==expected if expected is not None else result['status']=='unsupported'
            evidence_ok=all(row['id'] in set(frame.id) and int(frame.set_index('id').loc[row['id'],'amount_cents'])==row['amount_cents'] for row in result['evidence'])
            # A reference execution uses fixture intent and real calculation code,
            # so consistency tests the integration, not independent finance math.
            reference=answer_question(frame,case['question'],date(2026,10,9),model=Oracle())
            output.append(dict(id=case['id'],group=case['group'],mode=mode,question=case['question'],status=result['status'],intent=actual_intent,intent_correct=intent_ok,answer_consistent=(result['answer']==reference['answer']),source_consistent=evidence_ok,evidence_count=len(result['evidence']),fallback=fallback,seconds=round(elapsed,4),answer=result['answer']))
    directory=ROOT/'docs';directory.mkdir(exist_ok=True)
    (directory/'ai_evaluation_results.json').write_text(json.dumps(output,ensure_ascii=False,indent=2),encoding='utf-8')
    text='# AI evaluation\n\nReference date: 2026-10-09. Dataset: 30 authored questions and synthetic transactions.\n\n'
    text+='Mode: '+('LIVE MODEL' if args.live else '**MOCK ORACLE ONLY. No real model was evaluated. Oracle reads the expected intent directly; its scores measure code plumbing, NOT intelligence or accuracy.**')+'\n\n'
    text+='| Mode | Exact intent / 30 | Answer consistency / 30 | Evidence integrity / 30 | Correct refusals / 10 | Fallbacks | Mean seconds |\n|---|---:|---:|---:|---:|---:|---:|\n'
    for mode in sorted(set(r['mode'] for r in output)):
        rows=[r for r in output if r['mode']==mode]
        text+=f"| {mode} | {sum(r['intent_correct'] for r in rows)} | {sum(r['answer_consistent'] for r in rows)} | {sum(r['source_consistent'] for r in rows)} | {sum(r['status']=='unsupported' for r in rows if r['group']=='refuse')} | {sum(r['fallback'] for r in rows)} | {sum(r['seconds'] for r in rows)/len(rows):.4f} |\n"
    text+='\nDefinitions: exact intent compares validated fields to the authored intent; answer consistency compares to fixture-intent execution of the same deterministic calculator; evidence integrity checks every returned ID/amount against imported data (vacuous for refusals); refusal checks 10 unsupported questions. Fallback counts failed/invalid model parsing. Latency includes query and calculation, plus network in live mode.\n\nLimitations: small non-random dataset, synthetic transactions, same calculation code in reference. No real-world generalization claim, no classification accuracy claim. Finance math is tested separately.\n\nReproduce: `python scripts/evaluate_ai.py`. For live testing set FINANCE_LLM_BASE_URL, FINANCE_LLM_MODEL, FINANCE_LLM_API_KEY locally, then use `--live` (questions only are transmitted; may incur API fees). No keys are saved in the results.\n'
    (directory/'AI_EVALUATION.md').write_text(text,encoding='utf-8');print(text)

if __name__=='__main__':main()
