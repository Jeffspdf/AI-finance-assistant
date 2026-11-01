"""Desktop features added after the November review; no network on import."""
from datetime import date
from decimal import Decimal
import queue
import threading
import tkinter as tk
from tkinter import ttk, messagebox
from .analytics import budget_report, estimate_from_records, goal_progress, plan_savings, yuan
from .importer import money_to_cents
from .categories import CATEGORIES
BG='#f5f8f7'; GREEN='#168b73'; MUTED='#718681'

class ReviewFeatures:
    def review_initialize(self):
        self.dismissed_alerts=set()
        records=self.store.transactions()
        if 'month' not in self.preferences and not records.empty:
            self.selected_month=max(records.date.str[:7])
        if self.mode=='demo' and not records.empty:
            latest=max(records.date.str[:7])
            if not self.store.budgets(latest):
                self.store.save_budget(latest,'餐饮',10000);self.store.save_budget(latest,'交通',15000)

    def open_category(self,category,month=None):
        self.category_filter=category;self.search_text=''
        if month:self.selected_month=month
        self.show('records')

    def budget_page(self):
        month=self.selected_month if self.selected_month!='全部月份' else date.today().strftime('%Y-%m')
        self.budget_month=month
        form=tk.Frame(self.main,bg='white',padx=16,pady=12);form.pack(fill='x',pady=8)
        self.label(form,'设置分类预算（元） · '+month,13,bold=True)
        row=tk.Frame(form,bg='white');row.pack(fill='x')
        categories=[c for c in CATEGORIES if c not in ['工资','生活费','其他收入','转账']]
        self.budget_category=ttk.Combobox(row,values=categories,state='readonly',width=12);self.budget_category.set('餐饮');self.budget_category.pack(side='left',padx=(0,10))
        self.budget_amount=ttk.Entry(row,width=15);self.budget_amount.pack(side='left',padx=(0,10))
        def fill(_=None):
            value=self.store.budgets(month).get(self.budget_category.get(),0)
            self.budget_amount.delete(0,'end');self.budget_amount.insert(0,f'{value/100:.2f}')
        self.budget_category.bind('<<ComboboxSelected>>',fill);fill()
        self.button(row,'保存预算',self.save_desktop_budget)
        self.label(form,'零预算仍可保存；没有记录不表示没有消费。提醒仅在应用内显示。',9,MUTED)
        report=budget_report(self.store.transactions(),month,self.store.budgets(month))
        self.budget_rows=report.to_dict('records')
        if report.empty:self.label(self.main,'先设置一项分类预算。',12)
        self.budget_tree=self.table(self.main,['分类','预算','已支出','剩余','使用率','超支'],[[r['分类'],f"{r['预算']:,.2f}",f"{r['已支出']:,.2f}",f"{r['剩余']:,.2f}",f"{r['使用率']:.1f}%" if r['使用率'] is not None else '零预算',f"{r['超支']:,.2f}"] for r in self.budget_rows],5)
        self.budget_tree.bind('<<TreeviewSelect>>',self.budget_evidence)
        self.budget_details=tk.Frame(self.main,bg=BG);self.budget_details.pack(fill='both',expand=True)
        if self.budget_rows:self.budget_tree.selection_set(self.budget_tree.get_children()[0]);self.budget_evidence()
        self.show_goal_gaps(self.main)

    def save_desktop_budget(self):
        try:
            cents=money_to_cents(self.budget_amount.get(),allow_zero=True)
            self.store.save_budget(self.budget_month,self.budget_category.get(),cents)
            self.show('budget_page')
        except ValueError as exc:messagebox.showerror('预算输入错误',str(exc))

    def budget_evidence(self,event=None):
        selected=self.budget_tree.selection()
        if not selected:return
        category=self.budget_rows[self.budget_tree.index(selected[0])]['分类']
        for child in self.budget_details.winfo_children():child.destroy()
        self.label(self.budget_details,category+' · 支出依据（点击下方按钮进入账本追溯）',12,bold=True)
        controls=tk.Frame(self.budget_details,bg=BG);controls.pack(fill='x')
        self.button(controls,'查看对应账单与源记录',lambda:self.open_category(category,self.budget_month))
        frame=self.store.transactions();tx=frame[(frame.date.str.startswith(self.budget_month))&(frame.kind=='支出')&(frame.category==category)]
        self.table(self.budget_details,['日期','商户','金额','源文件','行号'],[[r.date[:10],r.merchant,yuan(r.amount_cents),r.source_file,r.source_row] for r in tx.itertuples()],4)

    def show_goal_gaps(self,parent):
        gaps=[]
        for goal in self.store.goals().to_dict('records'):
            progress=goal_progress(goal,self.store.contributions(goal['id']))
            if not progress['remaining']:continue
            if progress['remaining'] and progress['behind_cents']:
                gaps.append(f"{goal['name']}：实际进度落后 ¥{yuan(progress['behind_cents'])}")
            elif not progress['plan']['feasible']:
                gaps.append(f"{goal['name']}：原计划到期缺口 ¥{yuan(progress['plan']['deficit'])}")
        if gaps:
            self.label(parent,'目标提醒（计划假设与实际存入独立统计）',11,bold=True)
            for text in gaps[:5]:self.label(parent,text,10,'#ae6450')

    def home_budget_alerts(self):
        month=self.selected_month if self.selected_month!='全部月份' else date.today().strftime('%Y-%m')
        report=budget_report(self.store.transactions(),month,self.store.budgets(month))
        if not report.empty:
            self.label(self.main,'分类预算使用 / '+month,12,bold=True)
            for r in report.to_dict('records'):
                key=(self.mode,month,r['分类'],r['预算'],r['已支出'])
                line=tk.Frame(self.main,bg='#fff1e7' if r['超支'] else '#e8f4ec',padx=10,pady=4);line.pack(fill='x',pady=3)
                text=f"{r['分类']} · ¥{r['已支出']:,.2f} / ¥{r['预算']:,.2f}"+(f" · 超支 ¥{r['超支']:,.2f}" if r['超支'] else '')
                if r['超支'] and key in self.dismissed_alerts:line.destroy();continue
                tk.Button(line,text=text,command=lambda c=r['分类']:self.open_category(c,month),relief='flat',bg=line['bg'],fg=GREEN).pack(side='left')
                if r['超支']:
                    def dismiss(k=key,w=line):self.dismissed_alerts.add(k);w.destroy()
                    tk.Button(line,text='关闭',command=dismiss,relief='flat',bg=line['bg'],fg=MUTED).pack(side='right')
                bar=ttk.Progressbar(line,length=160,value=min(100,r['使用率'] or (100 if r['已支出'] else 0)));bar.pack(side='right',padx=10)
        self.show_goal_gaps(self.main)

    def draw_trend(self):
        from .analytics import overview
        monthly,_=overview(self.store.transactions())
        monthly=monthly.tail(12)
        if monthly.empty:return
        self.label(self.main,'月度支出趋势（最近有记录月份，空白月份未视为零）',12,bold=True)
        chart=tk.Canvas(self.main,height=145,bg='white',highlightthickness=0);chart.pack(fill='x',pady=8)
        values=monthly['支出'].tolist();scale=max(max(values),1);points=[]
        for i,(_,r) in enumerate(monthly.iterrows()):
            x=50+i*min(90,680/max(len(values)-1,1));y=105-r['支出']/scale*70;points.extend([x,y])
            chart.create_oval(x-3,y-3,x+3,y+3,fill=GREEN,outline='')
            chart.create_text(x,125,text=r['月份'],fill=MUTED,font=('Segoe UI',8))
            chart.create_text(x,y-12,text=f"{r['支出']:,.0f}",fill=GREEN,font=('Segoe UI',8))
        if len(points)>=4:chart.create_line(*points,fill=GREEN,width=2)

    def estimate_plan(self):
        try:r=estimate_from_records(self.store.transactions())
        except ValueError as exc:messagebox.showinfo('账单估算',str(exc));return
        income=r['average_income_cents'];expense=r['average_expense_cents']
        for name,value in [('每月收入 / 元',income),('方案 A 月预算 / 元',expense),('方案 B 月预算 / 元',int((Decimal(expense)*Decimal('.9')).quantize(Decimal('1'))))]:
            self.inputs[name].delete(0,'end');self.inputs[name].insert(0,f'{value/100:.2f}')
        self.calculate()
        text='使用完整月份：'+ '、'.join(r['months'])+f"；{r['record_count']} 笔。\n"+'\n'.join(f"{x['month']}：收入 ¥{yuan(x['income_cents'])}，支出 ¥{yuan(x['expense_cents'])}" for x in r['rows'])
        text+=f"\n平均收入 ¥{yuan(income)}；平均支出 ¥{yuan(expense)}。B 为平均支出的 90%，仅是假设情景。"
        text+='\n缺失月份：'+('、'.join(r['missing_months']) or '无')+'；不能证明各月账单完整。'
        self.estimate_note.config(text=text)

    def contribution_window(self):
        goal=self.chosen_goal()
        if not goal:return
        store=self.store
        window=tk.Toplevel(self);window.title('目标实际存入 · '+goal['name']);window.geometry('750x650');window.configure(bg=BG)
        box=tk.Frame(window,bg=BG,padx=18,pady=14);box.pack(fill='both',expand=True)
        status=self.label(box,'',12, GREEN)
        bar=ttk.Progressbar(box,maximum=100);bar.pack(fill='x',pady=8)
        self.label(box,'实际存入由用户记录，不会自动生成交易，也不修改原始计划的已有储蓄。',10,MUTED)
        form=tk.Frame(box,bg=BG);form.pack(fill='x')
        day=ttk.Entry(form,width=14);day.insert(0,date.today().isoformat());day.pack(side='left',padx=5)
        amount=ttk.Entry(form,width=13);amount.insert(0,'100');amount.pack(side='left',padx=5)
        self.label(box,'日期 YYYY-MM-DD / 金额（元）；请勿重复记录同一笔存入。',9,MUTED)
        tree_box=tk.Frame(box,bg=BG);tree_box.pack(fill='both',expand=True)
        current=[None,[]]
        def refresh():
            for child in tree_box.winfo_children():child.destroy()
            entries=store.contributions(goal['id']);r=goal_progress(goal,entries)
            status.config(text=f"实际已存 ¥{yuan(r['actual_saved'])} / 目标 ¥{yuan(goal['target_cents'])}\n按计划应存 ¥{yuan(r['expected_saved'])}；落后 ¥{yuan(r['behind_cents'])}；剩余 ¥{yuan(r['remaining'])}")
            bar['value']=r['ratio'];current[1]=entries.to_dict('records')
            current[0]=self.table(tree_box,['日期','存入金额','记录时间'],[[x['date'],yuan(x['amount_cents']),x['created_at']] for x in current[1]],7)
        def save():
            try:
                store.add_contribution(goal['id'],day.get().strip(),money_to_cents(amount.get()));refresh()
            except (ValueError,TypeError) as exc:messagebox.showerror('存入失败',str(exc),parent=window)
        def remove():
            tree=current[0];selected=tree.selection()
            if selected and messagebox.askyesno('删除存入记录','删除这条实际存入？',parent=window):
                store.remove_contribution(goal['id'],current[1][tree.index(selected[0])]['id']);refresh()
        self.button(form,'记录实际存入',save);self.button(form,'删除选中存入',remove);refresh()

    def suggest_categories_ui(self):
        if not self.model_client:messagebox.showinfo('AI 分类','请先在设置中启用模型。');return
        store=self.store;model=self.model_client
        candidates=self.frame[self.frame.category_source=='待确认'].head(20).to_dict('records')
        if not candidates:messagebox.showinfo('AI 分类','当前筛选范围内没有待确认分类。');return
        if not messagebox.askyesno('发送分类信息',f'将发送 {len(candidates)} 笔的商户、描述和收支类型给当前模型，不发送金额。是否继续？'):return
        window=tk.Toplevel(self);window.title('AI 分类建议 · 人工核对');window.geometry('780x500');window.configure(bg=BG)
        self.label(window,'正在获取建议，关闭此窗口可取消；规则分类与人工修正不会被覆盖。',10,MUTED)
        progress=ttk.Progressbar(window,maximum=len(candidates));progress.pack(fill='x',padx=15,pady=8)
        mailbox=queue.Queue();cancel=threading.Event();results=[];failures=[0];tree_holder=[None]
        def worker():
            for row in candidates:
                if cancel.is_set():return
                try:category=model.suggest_category(row['merchant'],row['description'],row['kind']);mailbox.put(('item',(row,category)))
                except Exception:mailbox.put(('failed',None))
            mailbox.put(('done',None))
        def apply():
            tree=tree_holder[0];selection=tree.selection()
            if not selection:messagebox.showinfo('选择建议','请选中想接受的建议，可按 Ctrl 多选。',parent=window);return
            count=0
            for item in selection:
                row,category=results[tree.index(item)]
                try:count+=int(store.apply_model_category(row['id'],category))
                except ValueError:pass
            messagebox.showinfo('已接受建议',f'接受 {count} 笔。来源标记为模型建议（需核对）；再次手动修正后标记用户修正。',parent=window)
            window.destroy();self.show('records')
        def poll():
            while True:
                try:kind,value=mailbox.get_nowait()
                except queue.Empty:break
                if kind=='item':results.append(value);progress['value']=len(results)+failures[0]
                elif kind=='failed':failures[0]+=1;progress['value']=len(results)+failures[0]
                else:
                    self.label(window,f'成功 {len(results)} 笔，失败 {failures[0]} 笔；仅接受选中项，其余不保存。',10,MUTED)
                    tree_holder[0]=self.table(window,['商户','描述','原分类','模型建议'],[[r['merchant'],r['description'],r['category'],c] for r,c in results],7)
                    self.button(window,'接受选中建议',apply);return
            window.poll=window.after(100,poll)
        def cleanup(event):
            if event.widget is window:
                cancel.set()
                if hasattr(window,'poll'):window.after_cancel(window.poll)
        window.bind('<Destroy>',cleanup);threading.Thread(target=worker,daemon=True).start();window.poll=window.after(100,poll)
