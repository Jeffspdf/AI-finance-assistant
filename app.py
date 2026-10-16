from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import os
import pandas as pd
import streamlit as st
from finance.storage import Store
from finance.importer import parse_statement, money_to_cents
from finance.categories import CATEGORIES
from finance.analytics import overview, budget_report, plan_savings, yuan, month_records
from finance.qa import answer_question, month_shift
from finance.llm import ModelClient

ROOT=Path(__file__).resolve().parent
st.set_page_config(page_title='清账 · 个人理财助手', page_icon='🌿', layout='wide')
try: TODAY=datetime.now(ZoneInfo('Asia/Shanghai')).date()
except Exception: TODAY=date.today()
DEMO_MONTH=month_shift(TODAY,-1)
if 'workspace' not in st.session_state: st.session_state.workspace='演示数据'

with st.sidebar:
    st.title('清账')
    st.caption('看懂支出，规划储蓄')
    workspace=st.radio('数据空间',['演示数据','我的账单'],key='workspace')
    st.caption('两个空间独立保存，演示不会混入你的账单。')
    page=st.radio('工作台',['财务概览','导入与分类','预算与提醒','储蓄方案','有依据问答','使用说明'])
    st.divider()
    st.caption('本地模式 · 人民币 · 不连接银行')
    with st.expander('可选 AI 理解与分类'):
        st.caption('关闭时不请求模型。启用后，分类仅发送商户、描述和收支类型；问答仅发送问题文本。交易金额与完整账单不发送。')
        enabled=st.checkbox('启用模型接口',value=False)
        base=st.text_input('兼容 API 地址',value=os.getenv('FINANCE_LLM_BASE_URL','http://localhost:11434/v1'))
        model_name=st.text_input('模型名称',value=os.getenv('FINANCE_LLM_MODEL','qwen2.5:7b'))
        key=st.text_input('API Key（仅当前会话）',value=os.getenv('FINANCE_LLM_API_KEY',''),type='password')
        consent=st.checkbox('允许发送上述信息至我填写的接口',value=False)
model=None
if st.session_state.get('last_workspace')!=workspace:
    for session_key in ['plan_result','answer']:
        st.session_state.pop(session_key,None)
    st.session_state['last_workspace']=workspace
if enabled and consent:
    try: model=ModelClient(base,model_name,key,enabled=True)
    except ValueError as exc: st.sidebar.error(str(exc))

store=Store(Path(os.getenv('FINANCE_DATA_DIR',str(ROOT/'data')))/('demo.sqlite3' if workspace=='演示数据' else 'personal.sqlite3'))
if workspace=='演示数据' and store.transactions().empty:
    from finance.demo import demo_bytes
    parsed=parse_statement(demo_bytes(TODAY),'三个月演示账单.csv')
    store.import_rows(parsed.rows)
    store.save_budget(DEMO_MONTH,'餐饮',10000)
    store.save_budget(DEMO_MONTH,'交通',15000)
tx=store.transactions()
record_version=hash(tuple(tx[['id','category','kind','amount_cents']].itertuples(index=False,name=None)))
if st.session_state.get('record_version')!=record_version:
    st.session_state.pop('answer',None)
    st.session_state['record_version']=record_version

def view_records(frame):
    renamed=frame.copy()
    if 'amount_cents' in renamed:
        renamed['金额（元）']=renamed.amount_cents/100
        renamed=renamed.drop(columns=['amount_cents'])
    return renamed.rename(columns={'id':'交易 ID','external_id':'原始交易 ID','date':'日期','merchant':'商户','description':'描述','kind':'收支','category':'分类','category_source':'分类依据','channel':'平台','source_file':'来源文件','source_row':'源文件行号'})

st.caption(f'{workspace} / {page}')
if page=='财务概览':
    st.title('你的钱，有迹可循')
    st.write('从已导入的记录，看清每月支出和结余。')
    if tx.empty:
        st.info('还没有账单。请到“导入与分类”上传文件，或在左侧切换到演示数据。')
    else:
        months=sorted(tx.date.str[:7].unique(),reverse=True)
        month=st.selectbox('查看月份',months)
        subset=month_records(tx,month)
        income=int(subset.loc[subset.kind=='收入','amount_cents'].sum())
        expense=int(subset.loc[subset.kind=='支出','amount_cents'].sum())
        a,b,c,d=st.columns(4)
        a.metric('收入',f'¥ {yuan(income)}'); b.metric('支出',f'¥ {yuan(expense)}'); c.metric('结余',f'¥ {yuan(income-expense)}'); d.metric('交易记录',len(subset))
        st.caption('收入 − 支出 = 结余。转账不计入收支；这里只反映已导入记录，不代表银行余额。')
        trend,_=overview(tx); _,cats=overview(subset)
        left,right=st.columns([1.3,1])
        with left:
            st.subheader('月度趋势')
            st.line_chart(trend.set_index('月份')[['收入','支出','结余']])
            st.dataframe(trend,hide_index=True,width='stretch')
        with right:
            st.subheader('支出去向')
            if cats.empty: st.info('本月没有支出记录。')
            else:
                st.bar_chart(cats.set_index('分类')['支出'],horizontal=True)
                st.dataframe(cats.rename(columns={'占比':'占比（%）'}),hide_index=True,width='stretch')
        report=budget_report(tx,month,store.budgets(month))
        alerts=report[report['超支']>0]
        if not alerts.empty:
            for row in alerts.to_dict('records'): st.warning(f"{month} {row['分类']}已超预算 ¥ {row['超支']:,.2f}。请到预算与提醒查看依据。")
        unknown=int((tx.category_source=='待确认').sum())
        if unknown: st.info(f'{unknown} 笔记录需要确认分类，可在“导入与分类”中修改。')
        with st.expander('本月源记录'):
            st.dataframe(view_records(subset.drop(columns=['signature'])),hide_index=True,width='stretch')
elif page=='导入与分类':
    st.title('把账单变成一份清楚的记录')
    st.write('支持 CSV 和 Excel（.xlsx），先预览校验结果，再确认保存。')
    st.download_button('下载导入模板',(ROOT/'samples'/'template.csv').read_bytes(),'账单模板.csv','text/csv')
    st.download_button('下载异常演示账单',(ROOT/'samples'/'demo_with_errors.csv').read_bytes(),'异常演示账单.csv','text/csv')
    uploaded=st.file_uploader('选择账单',type=['csv','xlsx'])
    if uploaded:
        try:
            parsed=parse_statement(uploaded.getvalue(),uploaded.name)
            a,b,c=st.columns(3)
            a.metric('有效行（含待去重）',len(parsed.rows)); b.metric('异常行',len(parsed.issues)); c.metric('跳过行',parsed.skipped)
            for warning in parsed.warnings: st.warning(warning)
            if parsed.issues:
                st.error('以下行不会计入，修正文件后可重新导入。')
                issues=pd.DataFrame(parsed.issues)
                st.dataframe(issues,hide_index=True,width='stretch')
                st.download_button('下载异常行报告',issues.to_csv(index=False).encode('utf-8-sig'),'导入异常.csv','text/csv')
            if parsed.rows:
                preview=pd.DataFrame(parsed.rows)
                st.dataframe(view_records(preview.drop(columns=['signature'])),hide_index=True,width='stretch')
                st.caption('优先按平台与原始交易 ID 去重。同一 ID 内容冲突会标记，不覆盖原记录。没有 ID 时按组合字段识别重复，请核对。')
                if st.button('确认导入',type='primary'):
                    added,duplicates,conflicts=store.import_rows(parsed.rows)
                    st.success(f'已保存 {added} 笔，跳过重复 {duplicates} 笔。')
                    if conflicts: st.dataframe(pd.DataFrame(conflicts),hide_index=True)
                    tx=store.transactions()
        except Exception as exc:
            st.error(f'无法导入：{exc}')
    st.subheader('确认与修正分类')
    if tx.empty: st.info('导入后即可在这里修改分类。')
    else:
        only_pending=st.checkbox('只看待确认记录')
        selected=tx[tx.category_source=='待确认'] if only_pending else tx
        display=view_records(selected[['id','date','merchant','description','amount_cents','kind','category','category_source']])
        with st.form('edit_categories'):
            edited=st.data_editor(display,hide_index=True,width='stretch',disabled=[c for c in display if c!='分类'],column_config={'分类':st.column_config.SelectboxColumn('分类',options=CATEGORIES,required=True)})
            if st.form_submit_button('保存分类修正'):
                original=selected.set_index('id').category.to_dict()
                corrections=[(r['交易 ID'],r['分类']) for r in edited.to_dict('records') if r['分类']!=original[r['交易 ID']]]
                store.correct_categories(corrections); st.success(f'已保存 {len(corrections)} 项修正。')
                st.rerun()
        pending=tx[tx.category_source=='待确认']
        if st.button('让 AI 建议待确认分类（每次最多 20 笔）',disabled=model is None or pending.empty):
            done,failed=0,0
            with st.spinner('正在请求分类建议…'):
                with store.connect() as db:
                    for row in pending.head(20).to_dict('records'):
                        try:
                            label=model.suggest_category(row['merchant'],row['description'],row['kind'])
                            db.execute('UPDATE transactions SET category=?,category_source=? WHERE id=?',(label,'模型建议（需核对）',row['id'])); done+=1
                        except Exception: failed+=1
            st.success(f'更新 {done} 笔建议，失败 {failed} 笔；原记录保留，可手动修正。')
            st.session_state['ai_notice']=f'更新 {done} 笔 AI 分类建议，请核对。失败 {failed} 笔。'
            st.rerun()
        if 'ai_notice' in st.session_state: st.info(st.session_state.pop('ai_notice'))
        st.download_button('导出整理后的记录',view_records(tx.drop(columns=['signature'])).to_csv(index=False).encode('utf-8-sig'),'整理后的账单.csv','text/csv')
elif page=='预算与提醒':
    st.title('给开销留一条边界')
    known=sorted(set(tx.date.str[:7])|{TODAY.strftime('%Y-%m')},reverse=True)
    month=st.selectbox('预算月份',known,index=known.index(DEMO_MONTH) if workspace=='演示数据' and DEMO_MONTH in known else 0)
    existing=store.budgets(month)
    category=st.selectbox('支出分类',[c for c in CATEGORIES if c not in ['工资','生活费','其他收入','转账']])
    with st.form('budget_form'):
        limit=st.number_input('每月预算（元）',min_value=0.0,value=float(existing.get(category,100000)/100),step=50.0,key=f'budget_{workspace}_{month}_{category}')
        if st.form_submit_button('保存预算'):
            store.save_budget(month,category,money_to_cents(limit,allow_zero=True)); st.rerun()
    report=budget_report(tx,month,existing)
    if report.empty: st.info('设置一项分类预算后，将显示使用率和超支提醒。')
    else:
        st.dataframe(report.rename(columns={'使用率':'使用率（%）'}),hide_index=True,width='stretch')
        for row in report[report['超支']>0].to_dict('records'):
            st.warning(f"{row['分类']}超支 ¥ {row['超支']:,.2f}：已支出 ¥ {row['已支出']:,.2f}，预算 ¥ {row['预算']:,.2f}。")
            evidence=month_records(tx,month)
            evidence=evidence[(evidence.kind=='支出')&(evidence.category==row['分类'])]
            with st.expander(f"查看{row['分类']}超支依据"):
                st.dataframe(view_records(evidence[['id','date','merchant','amount_cents','category']]),hide_index=True,width='stretch')
        if not (report['超支']>0).any(): st.success('已设置预算的分类暂未超支。')
        st.caption('提醒在打开页面或账单更新时计算，不发送短信、邮件或后台推送。零预算有支出时视为超支，使用率不适用。')
elif page=='储蓄方案':
    st.title('把目标拆成每个月能做的事')
    st.write('比较两种每月预算。所有结果来自你填写的假设，不预测未来收入。')
    with st.form('plan'):
        name=st.text_input('目标名称','六个月内存下 6000 元')
        left,right=st.columns(2)
        target=left.number_input('目标总额（元）',min_value=1.0,value=6000.0,step=100.0)
        saved=right.number_input('已经为此目标存下（元）',min_value=0.0,value=0.0,step=100.0)
        start=left.date_input('计划开始日期',TODAY)
        index=TODAY.year*12+TODAY.month-1+6; y,m=divmod(index,12)
        from calendar import monthrange
        deadline=right.date_input('截止日期',date(y,m+1,min(TODAY.day,monthrange(y,m+1)[1])))
        income=st.number_input('预计每月收入（元）',min_value=0.0,value=5000.0,step=100.0)
        budget_a=left.number_input('方案 A 每月总支出预算（元）',min_value=0.0,value=4200.0,step=100.0)
        budget_b=right.number_input('方案 B 每月总支出预算（元）',min_value=0.0,value=3800.0,step=100.0)
        if st.form_submit_button('比较方案',type='primary'):
            try:
                inputs=dict(name=name,target_cents=money_to_cents(target),saved_cents=money_to_cents(saved,True),start_date=start.isoformat(),deadline=deadline.isoformat(),income_cents=money_to_cents(income,True))
                results=[]
                for label,budget in [('A',budget_a),('B',budget_b)]:
                    cents=money_to_cents(budget,True)
                    result=plan_savings(**{k:v for k,v in inputs.items() if k!='name'},budget_cents=cents)
                    results.append((label,cents,result))
                st.session_state['plan_result']=(inputs,results)
            except ValueError as exc: st.error(str(exc))
    if 'plan_result' in st.session_state:
        inputs,results=st.session_state['plan_result']
        st.caption(f"已计算目标：{inputs['name']}，{inputs['start_date']} 至 {inputs['deadline']}。修改输入后需再次点击比较。")
        table=[]
        for label,budget,r in results:
            table.append({'方案':label,'每月预算':budget/100,'可存月份':r['months'],'每月可存':r['monthly_available']/100,'每月需存':r['required_monthly']/100 if r['required_monthly'] is not None else None,'到期可存总额':r['projected']/100,'距离目标差额':r['deficit']/100,'按假设可达成':'是' if r['feasible'] else '否'})
        st.dataframe(pd.DataFrame(table),hide_index=True,width='stretch')
        for label,budget,r in results:
            if r['negative_cashflow']: st.warning(f'方案 {label} 的预算超过收入，存在现金流缺口；没有可用于新增储蓄的结余。')
            if not r['months']: st.warning('截止前没有完整的月度存入日。请延长日期，或补充一次性存款。')
        delta=results[1][2]['projected']-results[0][2]['projected']
        st.write(f'方案 B 到期可存总额比 A 多 ¥ {yuan(delta)}。计算：每月收入减预算，乘以可存月份，再加已存金额。')
        st.caption('假设收入和预算固定、无利息、无投资收益。开始日之后每个月同日存入一次；短月份取月底，不按不足月折算。已有存款视为专用于此目标。')
        with st.expander('查看每次存入日期'): st.write(results[0][2]['contribution_dates'])
        col1,col2=st.columns(2)
        for col,(label,budget,r) in zip([col1,col2],results):
            if col.button(f'保存方案 {label}'):
                store.save_goal(dict(inputs,name=inputs['name']+' · 方案 '+label,budget_cents=budget)); st.success('已保存到当前数据空间。')
    goals=store.goals()
    if not goals.empty:
        with st.expander('已保存目标'):
            goal_view=goals[['name','start_date','deadline']].rename(columns={'name':'目标','deadline':'截止日期','start_date':'开始日期'})
            for column,label in [('target_cents','目标总额'),('saved_cents','已有存款'),('income_cents','月收入假设'),('budget_cents','月预算假设')]:
                goal_view[label]=goals[column]/100
            st.dataframe(goal_view,hide_index=True,width='stretch')
elif page=='有依据问答':
    st.title('每个答案，都能回到你的记录')
    st.write('试试：上个月餐饮花了多少？上个月收入多少？本月餐饮超支多少？')
    st.caption('支持本月、上个月、YYYY-MM 和全部记录；不提供投资预测。证据显示交易 ID、日期、金额与源文件行号。')
    question=st.text_input('你想知道什么？',value='上个月餐饮花了多少？',max_chars=500)
    if st.button('查询我的记录',type='primary'):
        months=sorted(set(tx.date.str[:7])|{TODAY.strftime('%Y-%m')})
        budget_map={m:store.budgets(m) for m in months}
        with st.spinner('正在核对记录…'):
            answer=answer_question(tx,question,today=TODAY,budgets=budget_map,model=model)
        st.session_state['answer']=(workspace,answer)
    if 'answer' in st.session_state and st.session_state['answer'][0]==workspace:
        result=st.session_state['answer'][1]
        st.write(result['answer']); st.caption(result['parser'])
        if result['evidence']:
            evidence=view_records(pd.DataFrame(result['evidence']))
            st.dataframe(evidence,hide_index=True,width='stretch')
            st.download_button('下载本次回答依据',evidence.to_csv(index=False).encode('utf-8-sig'),'回答依据.csv','text/csv')
else:
    st.title('从一份账单开始')
    st.markdown('''
1. 在左侧选择“演示数据”，直接查看最近三个月的样例。
2. 到“导入与分类”上传异常演示账单，检查去重和异常行。
3. 查看财务概览，在预算页面查看餐饮超支的源记录。
4. 在储蓄方案中比较 4200 元和 3800 元预算，观察六个月 6000 元目标。
5. 询问“上个月餐饮花了多少？”，核对每条交易证据。

**使用自己的账单**：切换到“我的账单”，下载模板后填写或映射银行导出列名。仅支持人民币。转账不计入收入支出。退款、部分退款需人工核对，异常行不会计入。

**数据与隐私**：记录、预算、目标保存在本机 SQLite。默认不调用模型、不发送账单、不采集 Streamlit 使用统计。演示数据是合成记录。应用仅绑定本机地址，不含登录和多人权限管理。

**AI 分工**：规则先分类，模型仅为不确定项建议标签或理解问题。金额、占比、趋势、目标和回答正文全部由确定性代码生成。未配置模型也能走完全部演示流程。

**边界**：没有真实银行对接、多币种、OCR、投资组合或移动端。提醒为应用内提示。未导入的交易不会自动补齐。模型接口需你自行配置并主动允许发送信息。
''')
    st.caption('当前存储目录：'+str(Path(store.path).parent))
