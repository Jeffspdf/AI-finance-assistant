"""AST gate for product money entrypoints and narrowly permitted source readers."""
import ast
from pathlib import Path
import re

# Purpose-specific source readers, not a whole-file exemption.
SOURCE_READERS={
 ('desktop.py','Desktop.records'):'editable source ledger',
 ('app.py','_source_records'):'editable source ledger',
 ('finance/desktop_round2.py','RoundTwo.batch_correct'):'source IDs for classification',
 ('finance/desktop_review.py','ReviewFeatures.suggest_categories_ui'):'source IDs for classification',
 ('finance/desktop_demo.py','DemoGeneratorFeatures.demo_generator_window.generate'):'preview replacement source count',
 ('finance/desktop_refunds.py','RefundFeatures.refund_window.refresh'):'refund and purchase source selection',
 ('finance/web_refunds.py','render_refunds'):'refund and purchase source selection',
 ('finance/storage.py','Store.category_conflicts'):'legacy source classification discovery',
 ('finance/m1a_demo.py','seed_six'):'isolated engineering sample initialization',
}
ENGINEERING={
 ('finance/m1a_demo.py','verify_six'),('finance/demo_generator.py','verify_generator'),
 ('finance/round5_acceptance.py','verify_round5'),('finance/round6_acceptance.py','verify_round6'),
 ('finance/round7_acceptance.py','verify_round7'),('finance/round8_acceptance.py','verify_round8')}
NET_ENTRIES={
 ('desktop.py','Desktop.show'),('desktop.py','Desktop.home'),('desktop.py','Desktop.refresh_records'),
 ('finance/desktop_review.py','ReviewFeatures.budget_page'),('finance/desktop_review.py','ReviewFeatures.budget_evidence'),
 ('finance/desktop_review.py','ReviewFeatures.home_budget_alerts'),('finance/desktop_review.py','ReviewFeatures.draw_trend'),
 ('finance/desktop_review.py','ReviewFeatures.estimate_plan'),
 ('finance/desktop_round2.py','RoundTwo.home_insights'),('finance/desktop_round2.py','RoundTwo.show_insight_evidence'),
 ('finance/desktop_round2.py','RoundTwo.propose_budgets'),('finance/desktop_round2.py','RoundTwo.import_async.poll'),
 ('finance/conversation.py','ConversationFeatures.conversation_ask'),
 ('finance/desktop_m1a.py','M1Features.reconciliation_window.refresh'),
 ('finance/desktop_refunds.py','RefundFeatures.refund_window.refresh'),
 ('finance/web_refunds.py','render_refunds'),('app.py','<module>')}
TRUSTED_SQL={'finance/storage.py','finance/reconciliation.py','finance/refunds.py','finance/refund_rules.py',
             'finance/migrations.py','finance/backup_management.py','finance/demo.py','finance/demo_generator.py'}
MONEY_SINKS={'overview','budget_report','estimate_from_records','answer_question','make_insights','suggested_budgets','spending_totals','sum'}


def _scopes(tree):
    result={'<module>':[]}
    def visit(node,path):
        if isinstance(node,(ast.ClassDef,ast.FunctionDef,ast.AsyncFunctionDef)):
            path=(*path,node.name);result.setdefault('.'.join(path),[])
        result['.'.join(path) if path else '<module>'].append(node)
        for child in ast.iter_child_nodes(node):visit(child,path)
    for node in tree.body:visit(node,())
    return result


def projection_violations(root,*,check_entrypoints=True):
    root=Path(root);files=sorted(set(root.glob('*.py'))|set((root/'finance').rglob('*.py')))
    violations=[];seen_entries=set()
    for path in files:
        relative=path.relative_to(root).as_posix();tree=ast.parse(path.read_text(encoding='utf-8-sig'))
        scopes=_scopes(tree);attribute_sources=set()
        def getter(node):
            return isinstance(node,ast.Attribute) and node.attr in ('transactions','source_transactions','_source_frame')
        for nodes in scopes.values():
            for node in nodes:
                if isinstance(node,(ast.Assign,ast.AnnAssign)):
                    value=node.value;targets=node.targets if isinstance(node,ast.Assign) else [node.target]
                    if value is not None and any(getter(n) for n in ast.walk(value)):
                        attribute_sources.update(ast.unparse(t) for t in targets if isinstance(t,ast.Attribute))
        for name,nodes in scopes.items():
            key=(relative,name);engineering=any(relative==file and (name==scope or name.startswith(scope+'.')) for file,scope in ENGINEERING)
            def issue(node,text):violations.append(f'{relative}:{getattr(node,"lineno",1)} {name}: {text}')
            if key in NET_ENTRIES:
                seen_entries.add(key)
                if not any(isinstance(n,ast.Call) and ((isinstance(n.func,ast.Attribute) and n.func.attr=='spending_view') or
                    isinstance(n.func,ast.Name) and n.func.id=='spending_totals') for n in nodes):
                    issue(nodes[0] if nodes else tree,'金额入口缺少spending_view/受校验的净额展示')
            tainted=set()
            def taint(value):
                return any((getter(n)) or isinstance(n,ast.Name) and n.id in tainted or
                    isinstance(n,ast.Attribute) and ast.unparse(n) in attribute_sources or
                    isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='_source_records' for n in ast.walk(value))
            # Fixed point handles aliases and subscript/filter chains, including self.source_frame.
            for _ in range(len(nodes)+1):
                old=set(tainted)
                for node in nodes:
                    if isinstance(node,(ast.Assign,ast.AnnAssign)) and node.value is not None and taint(node.value):
                        targets=node.targets if isinstance(node,ast.Assign) else [node.target]
                        tainted.update(t.id for t in targets if isinstance(t,ast.Name))
                if old==tainted:break
            for node in nodes:
                if engineering:continue
                if getter(node):
                    if node.attr=='transactions':issue(node,'产品代码禁止读取gross transactions（含方法别名）')
                    elif node.attr=='_source_frame':
                        if relative not in TRUSTED_SQL:issue(node,'产品页面禁止调用内部gross工厂')
                    elif key not in SOURCE_READERS:issue(node,'来源读取未列入用途明确的源记录入口')
                if isinstance(node,ast.Call):
                    if isinstance(node.func,ast.Name) and node.func.id=='getattr' and len(node.args)>1:
                        literal=node.args[1].value if isinstance(node.args[1],ast.Constant) else None
                        store_target=isinstance(node.args[0],ast.Name) and node.args[0].id=='store' or isinstance(node.args[0],ast.Attribute) and node.args[0].attr=='store'
                        if literal in ('transactions','source_transactions','_source_frame') or literal is None and store_target:issue(node,'禁止反射绕过投影入口')
                    if isinstance(node.func,ast.Attribute) and node.func.attr in ('execute','read_sql','read_sql_query') and relative not in TRUSTED_SQL:
                        if any(isinstance(n,ast.Constant) and isinstance(n.value,str) and re.search(r'\b(?:FROM|JOIN)\s+["\[]?transactions\b',n.value,re.I) for n in ast.walk(node)):
                            issue(node,'产品入口禁止直接SQL读取原始交易表')
                    sink=(isinstance(node.func,ast.Attribute) and node.func.attr in ('sum','agg','aggregate','cumsum','mean','median','quantile') and taint(node.func.value)) or (
                        isinstance(node.func,ast.Name) and node.func.id in MONEY_SINKS and any(taint(arg) for arg in node.args))
                    if sink:issue(node,'源记录入口不得进行消费统计，须从spending_view计算')
    if check_entrypoints:
        for key in sorted(NET_ENTRIES-seen_entries):violations.append('/'.join(key)+': 缺少受保护的金额入口')
    return sorted(set(violations))
