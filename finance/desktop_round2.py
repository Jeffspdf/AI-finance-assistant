"""Everyday actions and evidence-linked insights for the desktop product."""
from .ui_scale import px, logical, point_font, set_window_geometry
from datetime import date
import re
import tkinter as tk
from tkinter import ttk,messagebox,filedialog
from .importer import money_to_cents
from .categories import CATEGORIES
from .insights import make_insights,suggested_budgets
from .analytics import yuan
from .qa import month_shift
from .ui_theme import BG,INK,MUTED,PRIMARY,DANGER
from .ui_theme import Meter,scrollable_form

class RoundTwo:
    def round2_init(self):
        self.chat_histories={'demo':[],'personal':[]};self.chat_context={};self.pending_clarification=None;self.last_batch=None
        self.bind('<Control-n>',lambda _:self.quick_entry())
        self.bind('<Control-k>',lambda _:self.command_palette())

    def home_insights(self):
        frame=self.store.transactions();goals=self.store.goals().to_dict('records')
        cards=make_insights(frame,self.selected_month,goals,{g['id']:self.store.contributions(g['id']) for g in goals})
        if not cards:return
        self.label(self.main,'本期洞察 · 仅基于已导入记录',12,bold=True)
        band=tk.Frame(self.main,bg=BG);band.pack(fill='x',pady=px((4,14)))
        boxes=[]
        for i,card in enumerate(cards):
            box=tk.Frame(band,bg='white',padx=px(14),pady=px(12),highlightbackground='#e6ece9',highlightthickness=px(1))
            boxes.append(box)
            self.label(box,card['title'],10,MUTED);self.label(box,card['value'],20,PRIMARY,True)
            why=self.label(box,card['why'],9,MUTED);why.configure(wraplength=px(210))
            box.bind('<Configure>',lambda event,label=why:label.configure(wraplength=max(px(100),event.width-px(30))))
            self.button(box,'查看依据',lambda c=card:self.show_insight_evidence(c))
        def arrange(event=None):
            columns=min(len(boxes),3 if logical(band.winfo_width())>=860 else 2)
            for col in range(3):band.columnconfigure(col,weight=1 if col<columns else 0,uniform='cards' if col<columns else '')
            for index,box in enumerate(boxes):box.grid(row=index//columns,column=index%columns,sticky='nsew',padx=px((0,8)),pady=px((0,8)))
        band.bind('<Configure>',arrange);arrange()

    def show_insight_evidence(self,card):
        frame=self.store.transactions();tx=frame[frame.id.isin(card['ids'])]
        window=tk.Toplevel(self);window.title('洞察依据');set_window_geometry(window, 760, 560);window.configure(bg=BG)
        self.label(window,card['title']+' · '+card['value'],16,bold=True)
        note=self.label(window,card['why'],10,MUTED);note.configure(wraplength=px(710))
        self.table(window,['日期','商户','分类','金额 / 元','来源','行号'],[[r.date[:10],r.merchant,r.category,yuan(r.amount_cents),r.source_file,r.source_row] for r in tx.itertuples()],8)
        if card.get('trace'):self.button(window,'查看公式、关联与参数',lambda:self.show_calculation_trace(card['trace']))
        self.button(window,'进入账本筛选',lambda:(window.destroy(),self.open_category(card['category'] or '全部分类',card['month'])))

    def quick_entry(self):
        store=self.store;mode=self.mode
        window=tk.Toplevel(self);window.title('快速记一笔');set_window_geometry(window, 460, 580);window.configure(bg=BG)
        actions=tk.Frame(window,bg=BG);actions.pack(side='bottom',fill='x',padx=px(22),pady=px(4))
        box=scrollable_form(window,padx=22,pady=16)
        self.label(box,'快速记一笔',18,bold=True)
        self.label(box,'保存到 '+('演示空间' if mode=='demo' else '我的账本'),10,MUTED)
        fields={}
        for label,key,value in [('金额 / 元','amount',''),('商户','merchant',''),('日期 YYYY-MM-DD','date',date.today().isoformat()),('备注','description','')]:
            self.label(box,label,10,MUTED);entry=ttk.Entry(box);entry.insert(0,value);entry.pack(fill='x',pady=px((0,7)));fields[key]=entry
        row=tk.Frame(box,bg=BG);row.pack(fill='x')
        kind=ttk.Combobox(row,values=['支出','收入','转账'],state='readonly',width=10);kind.set('支出');kind.pack(side='left',padx=px((0,10)))
        category=ttk.Combobox(row,values=CATEGORIES,state='readonly',width=13);category.set('餐饮');category.pack(side='left')
        def match_kind(_=None):
            permitted=['工资','生活费','其他收入'] if kind.get()=='收入' else ['转账'] if kind.get()=='转账' else [c for c in CATEGORIES if c not in ['工资','生活费','其他收入','转账']]
            category.configure(values=permitted)
            if category.get() not in permitted:category.set(permitted[0])
        kind.bind('<<ComboboxSelected>>',match_kind);match_kind()
        status=self.label(box,'Ctrl+N 打开，Ctrl+Enter 保存。',9,MUTED)
        def save():
            try:
                cents=money_to_cents(fields['amount'].get())
                store.add_manual_transaction(fields['date'].get().strip(),fields['merchant'].get().strip(),cents,kind.get(),category.get(),fields['description'].get().strip())
                window.destroy()
                if mode==self.mode:
                    self.selected_month='全部月份';self.search_text='';self.category_filter='全部分类';self.show('records');self.animate_character('success')
            except (ValueError,TypeError) as exc:status.config(text=str(exc),fg=DANGER)
        self.button(actions,'保存这一笔',save);window.bind('<Control-Return>',lambda _:save());fields['amount'].focus_set()

    def batch_correct(self,same_merchant=False):
        selection=self.tree.selection()
        if not selection:messagebox.showinfo('选择交易','请先选择交易，可按 Ctrl 或 Shift 多选。');return
        selected=[self.record_rows[self.row_index(self.tree,item)] for item in selection]
        ids=[r['id'] for r in selected]
        if same_merchant:
            pairs={(r['merchant'],r['kind']) for r in selected};all_rows=self.store.transactions()
            ids=[r.id for r in all_rows.itertuples() if (r.merchant,r.kind) in pairs]
        if not messagebox.askyesno('确认批量分类',f'将 {len(ids)} 笔改为“{self.category.get()}”？'+('覆盖当前空间相同商户的全部记录。' if same_merchant else '可撤销本次批量操作。')):return
        self.last_batch=(self.store.path,self.store.batch_category(ids,self.category.get()))
        self.show('records')

    def undo_batch(self):
        if not self.last_batch or self.last_batch[0]!=self.store.path:messagebox.showinfo('撤销','当前空间没有可撤销的批量分类。');return
        count=self.store.undo_category_batch(self.last_batch[1]);self.last_batch=None;self.show('records')
        messagebox.showinfo('已撤销',f'恢复 {count} 笔。后续已改为其他类别的记录不覆盖。')

    def copy_previous_budget(self):
        target=self.budget_month;source=month_shift(date.fromisoformat(target+'-01'),-1)
        budgets=self.store.budgets(source)
        if not budgets:messagebox.showinfo('复制预算',source+' 没有预算可复制。');return
        overwrite=False
        if set(budgets)&set(self.store.budgets(target)):
            overwrite=messagebox.askyesno('预算冲突','是否覆盖同类别的现有预算？选“否”仅补齐未设置的类别。')
        self.store.copy_budgets(source,target,overwrite);self.show('budget_page')

    def propose_budgets(self):
        try:budgets=suggested_budgets(self.store.transactions())
        except ValueError as exc:messagebox.showinfo('预算建议',str(exc));return
        text='以最近三个完整月的分类平均支出 ×90% 作为建议，不是最优预算保证。\n'+ '\n'.join(f'{c}：¥{yuan(v)}' for c,v in budgets.items())
        if messagebox.askyesno('确认应用到 '+self.budget_month,text+'\n是否应用（覆盖同类别预算）？'):
            for category,cents in budgets.items():self.store.save_budget(self.budget_month,category,cents)
            self.show('budget_page')

    def learned_rules_window(self):
        store=self.store;window=tk.Toplevel(self);window.title('商户分类记忆');set_window_geometry(window, 650, 500);window.configure(bg=BG)
        self.label(window,'只按完全相同商户与收支类型匹配；仅影响下次导入的新记录。',10,MUTED)
        holder=tk.Frame(window,bg=BG);holder.pack(fill='both',expand=True);state=[None,[]]
        def refresh():
            for child in holder.winfo_children():child.destroy()
            state[1]=store.merchant_rules().to_dict('records');state[0]=self.table(holder,['商户','类型','记忆分类'],[[r['merchant'],r['kind'],r['category']] for r in state[1]],8)
        def remove():
            tree=state[0];selected=tree.selection()
            if selected:
                row=state[1][self.row_index(tree,selected[0])];store.delete_merchant_rule(row['merchant'],row['kind']);refresh()
        self.button(window,'删除选中的记忆',remove);refresh()

    def command_palette(self):
        window=tk.Toplevel(self);window.title('搜索与快捷操作');set_window_geometry(window, 540, 390);window.configure(bg=BG)
        self.label(window,'搜索账单或输入页面名称',16,bold=True)
        self.label(window,'支持商户 / 分类 / >100 / 上月；页面：概览、账本、预算、规划、助手、设置。',10,MUTED).configure(wraplength=px(490))
        entry=ttk.Entry(window,width=45);entry.pack(fill='x',padx=px(18),pady=px(14))
        def run():
            value=entry.get().strip();window.destroy()
            pages={'概览':'home','账本':'records','预算':'budget_page','规划':'plan','助手':'assistant','设置':'settings'}
            if value in pages:self.show(pages[value]);return
            self.selected_month='全部月份';self.search_text=value;self.category_filter='全部分类'
            if value in CATEGORIES:self.category_filter=value;self.search_text=''
            if value in ['上月','上个月']:self.selected_month=month_shift(date.today(),-1);self.search_text=''
            self.show('records')
        self.button(window,'查找 / 前往',run);self.button(window,'快速记一笔',lambda:(window.destroy(),self.quick_entry()))
        entry.bind('<Return>',lambda _:run());entry.focus_set()

    def import_async(self,filename=None,adapter='generic',account_id=None):
        from pathlib import Path
        import threading,queue,csv
        from .importer import parse_statement
        from .analytics import budget_report
        filename=filename or filedialog.askopenfilename(filetypes=[('账单文件','*.csv *.xlsx')])
        if not filename:return
        store=self.store;mode=self.mode
        window=tk.Toplevel(self);window.title('导入账单');set_window_geometry(window, 800, 630);window.configure(bg=BG)
        box=tk.Frame(window,bg=BG,padx=px(18),pady=px(14));box.pack(fill='both',expand=True)
        status=self.label(box,'正在解析文件…可取消，本阶段不会写入账单。',12,PRIMARY)
        content=tk.Frame(box,bg=BG);content.pack(fill='both',expand=True)
        actions=tk.Frame(box,bg=BG);actions.pack(fill='x')
        mailbox=queue.Queue();cancel=threading.Event();state={'writing':False,'done':False}
        def close():
            if state['writing']:
                status.config(text='正在提交事务，请等待完成后关闭。');return
            cancel.set();window.destroy()
        window.protocol('WM_DELETE_WINDOW',close)
        cancel_button=self.button(actions,'取消导入',close)
        def parse_worker():
            try:
                path=Path(filename)
                if path.stat().st_size>20*1024*1024:raise ValueError('文件超过 20 MB')
                result=parse_statement(path.read_bytes(),path.name,retain_source=True,adapter=adapter,account_id=account_id)
                mailbox.put(('parsed',result))
            except Exception as exc:mailbox.put(('error',str(exc)))
        def export_issues(result):
            destination=filedialog.asksaveasfilename(parent=window,defaultextension='.csv',filetypes=[('CSV','*.csv')],initialfile='import-issues.csv')
            if destination:
                with open(destination,'w',encoding='utf-8-sig',newline='') as stream:
                    fields=list(result.issues[0]) if result.issues else ['行号','原因'];writer=csv.DictWriter(stream,fieldnames=fields);writer.writeheader();writer.writerows(result.issues)
        def preview(result):
            status.config(text=f'有效 {len(result.rows)} 行 · 异常 {len(result.issues)} 行 · 跳过 {result.skipped} 行；有效行确认后才写入。')
            if result.warnings:self.label(content,'\n'.join(result.warnings),9,DANGER).configure(wraplength=px(730))
            notebook=ttk.Notebook(content);notebook.pack(fill='both',expand=True)
            valid=tk.Frame(notebook,bg='white');issues=tk.Frame(notebook,bg='white');notebook.add(valid,text='有效记录预览（前200）');notebook.add(issues,text=f'全部异常（{len(result.issues)}）')
            self.table(valid,['日期','商户','类型','金额','分类'],[[r['date'][:10],r['merchant'],r['kind'],f"{r['amount_cents']/100:.2f}",r['category']] for r in result.rows[:200]],8)
            self.table(issues,['行号','原因','日期','金额'],[[r.get(k,'') for k in ['行号','原因','日期','金额']] for r in result.issues],8)
            if len(result.issues)>200:
                self.label(issues,'预览前 200 条，全部异常可导出 CSV。',9,MUTED)
            if result.issues:self.button(actions,'导出全部异常 CSV',lambda:export_issues(result))
            def commit():
                if state['writing']:return
                state['writing']=True;commit_button.config(state='disabled');cancel_button.config(state='disabled')
                status.config(text='正在保存有效记录…完成前请勿关闭。')
                def writer():
                    try:mailbox.put(('saved',(store.import_statement(result),result)))
                    except Exception as exc:mailbox.put(('error',str(exc)))
                threading.Thread(target=writer,daemon=True).start()
            if result.raw_rows:commit_button=self.button(actions,'确认导入并保存来源',commit)
        def poll():
            if cancel.is_set() or not window.winfo_exists():return
            try:kind,value=mailbox.get_nowait()
            except queue.Empty:window.poll=window.after(80,poll);return
            if kind=='parsed':preview(value)
            elif kind=='error':
                state['writing']=False;status.config(text='未完成导入：'+value,fg=DANGER);cancel_button.config(state='normal')
            elif kind=='saved':
                state['writing']=False;state['done']=True
                ((added,duplicates,conflicts),result)=value
                for child in content.winfo_children():child.destroy()
                for child in actions.winfo_children():child.destroy()
                status.config(text=f'已新增 {added} 笔，重复跳过 {duplicates} 笔，冲突 {len(conflicts)} 笔。')
                self.label(content,f'异常 {len(result.issues)} 行未入库；原有冲突交易未被覆盖。',11,MUTED)
                all_records=store.transactions();pending=int((all_records.category_source=='待确认').sum())
                self.label(content,f'当前空间还有 {pending} 笔待确认分类，可继续核对。',11,PRIMARY)
                for month in sorted({r['date'][:7] for r in result.rows},reverse=True)[:3]:
                    report=budget_report(all_records,month,store.budgets(month))
                    for row in report.to_dict('records'):
                        if row['超支']:self.label(content,f"{month} {row['分类']}已超预算 ¥{row['超支']:,.2f}",11,DANGER)
                def go():
                    close()
                    if mode==self.mode:
                        self.selected_month='全部月份';self.category_filter='待确认分类';self.search_text='';self.show('records');self.animate_character('success')
                self.button(actions,'核对待确认分类',go);self.button(actions,'完成',lambda:(close(),self.show(self.page)))
            if not state['done']:window.poll=window.after(80,poll)
        def cleanup(event):
            if event.widget is window:
                cancel.set()
                if hasattr(window,'poll'):window.after_cancel(window.poll)
        window.bind('<Destroy>',cleanup);threading.Thread(target=parse_worker,daemon=True).start();window.poll=window.after(80,poll)
