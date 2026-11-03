"""Configurable demo generation, available only in the demo workspace."""
from datetime import date
import queue
import threading
import tkinter as tk
from tkinter import ttk,messagebox
from .analytics import yuan
from .demo_generator import DemoOptions,generate_demo
from .importer import money_to_cents
from .qa import month_shift
from .ui_scale import px,set_window_geometry
from .ui_theme import BG,MUTED,PRIMARY,DANGER,scrollable_form


class DemoGeneratorFeatures:
    def demo_generator_window(self):
        if self.mode!='demo':
            messagebox.showinfo('生成演示账单','请先切换到演示空间。',parent=self);return
        previous=getattr(self,'demo_generator_dialog',None)
        if previous is not None and previous.winfo_exists():previous.lift();return
        store=self.store
        window=tk.Toplevel(self);self.demo_generator_dialog=window
        window.title('生成演示账单');set_window_geometry(window,630,740);window.configure(bg=BG)
        footer=tk.Frame(window,bg=BG);footer.pack(side='bottom',fill='x',padx=px(20),pady=px(6))
        preview=self.label(footer,'',11,PRIMARY);preview.configure(wraplength=px(570))
        status=self.label(footer,'先预览数量，再生成；每次生成是一批独立记录。',10,MUTED);status.configure(wraplength=px(570))
        actions=tk.Frame(footer,bg=BG);actions.pack(fill='x')
        box=scrollable_form(window,padx=20,pady=14)
        self.label(box,'按你想测试的月份，生成一批账单',18,bold=True).configure(wraplength=px(570))
        self.label(box,'完全本地生成合成记录，不调用模型。每月支出笔数不包含另外添加的收入、转账或退款。',10,MUTED).configure(wraplength=px(570))
        fields={}
        defaults=[('起始月份（YYYY-MM）','start_month',month_shift(date.today(),-3)),
                  ('连续生成月数（1–12）','months','3'),('每月支出笔数（1–500）','count','30'),
                  ('单笔支出最小值 / 元','minimum','10.00'),('单笔支出最大值 / 元','maximum','200.00'),
                  ('每月收入 / 元（0 表示不生成）','income','3000.00')]
        form=tk.Frame(box,bg=BG);form.pack(fill='x')
        form.columnconfigure(0,weight=1,uniform='fields');form.columnconfigure(1,weight=1,uniform='fields')
        for index,(label,key,value) in enumerate(defaults):
            cell=tk.Frame(form,bg=BG);cell.grid(row=index//2,column=index%2,sticky='nsew',padx=px((0,8) if index%2==0 else (8,0)))
            self.label(cell,label,10,MUTED).configure(wraplength=px(260))
            entry=ttk.Entry(cell);entry.insert(0,value);entry.pack(fill='x',pady=px((0,4)));fields[key]=entry
        self.label(box,'收入类型',10,MUTED)
        income_kind=ttk.Combobox(box,values=['生活费','工资'],state='readonly');income_kind.set('生活费');income_kind.pack(fill='x',pady=px((0,4)))
        transfers=tk.BooleanVar(value=True);refunds=tk.BooleanVar(value=False);replace=tk.BooleanVar(value=False)
        for text,var in [('每月增加1笔转账（500元，不计收支）',transfers),('每月增加1笔退款（首笔消费的一半，待处理，不抵扣）',refunds),('替换所选月份的全部演示交易（默认追加）',replace)]:
            tk.Checkbutton(box,text=text,variable=var,bg=BG,anchor='w',wraplength=px(570),justify='left').pack(anchor='w',pady=px(3))
        self.label(box,'替换仅移除所选月份的演示交易，保留其他月份、预算、目标及原始证据。不会写入个人账本。',10,MUTED).configure(wraplength=px(570))
        state={'busy':False,'done':False};mailbox=queue.Queue()
        def options():
            try:months=int(fields['months'].get().strip());count=int(fields['count'].get().strip())
            except ValueError:raise ValueError('生成月数和每月支出笔数须填写整数')
            result=DemoOptions(fields['start_month'].get().strip(),months,count,
                               money_to_cents(fields['minimum'].get()),money_to_cents(fields['maximum'].get()),
                               money_to_cents(fields['income'].get(),True),income_kind.get(),transfers.get(),refunds.get(),replace.get())
            result.month_list();return result
        def show_preview():
            try:
                config=options();summary=config.summary()
                count=summary['count'];months=summary['months']
                preview.config(text=f'{months[0]} 至 {months[-1]} · {len(months)} 个月 · 共 {count} 笔\n每月 {config.expenses_per_month} 笔支出，单笔 ¥{yuan(config.expense_min_cents)}–{yuan(config.expense_max_cents)}；收入 ¥{yuan(config.income_cents)}。',fg=PRIMARY)
                return config
            except ValueError as exc:preview.config(text=str(exc),fg=DANGER);return None
        def finish_view():
            if self.mode!='demo':self.switch_space()
            self.selected_month=state['last_month'];self.category_filter='全部分类';self.search_text='';self.save_preferences()
            window.destroy();self.show('records')
        def generate():
            if state['busy']:return
            if self.mode!='demo' or self.store is not store:
                status.config(text='数据空间已切换，请回到演示空间重新打开生成窗口。',fg=DANGER);return
            config=show_preview()
            if config is None:return
            if config.replace_months:
                months=config.month_list();rows=store.source_transactions();matched=rows[rows.date.str[:7].isin(months)]
                if not messagebox.askyesno('确认替换演示账单',f'将移除 {months[0]} 至 {months[-1]} 的 {len(matched)} 笔演示交易，再生成 {config.summary()["count"]} 笔。相关对账将失效，其他月份、预算和目标保留。是否继续？',parent=window):return
            state['busy']=True;generate_button.config(state='disabled');preview_button.config(state='disabled');view_button.config(state='disabled')
            status.config(text='正在生成并保存，请稍候…',fg=PRIMARY)
            def worker():
                try:mailbox.put(('done',generate_demo(store,config,space='demo')))
                except Exception as exc:mailbox.put(('error',str(exc)))
            threading.Thread(target=worker,daemon=True).start();window.poll=window.after(80,poll)
        def poll():
            try:kind,value=mailbox.get_nowait()
            except queue.Empty:window.poll=window.after(80,poll);return
            state['busy']=False;generate_button.config(state='normal');preview_button.config(state='normal')
            if kind=='error':status.config(text='生成未完成：'+value,fg=DANGER);return
            state.update(done=True,last_month=value['months'][-1]);view_button.config(state='normal')
            status.config(text=f'已生成 {value["added"]} 笔，涉及 {len(value["months"])} 个月；替换移除 {value["removed"]} 笔。点击“查看生成账单”即可检查。',fg=PRIMARY)
            if self.mode=='demo' and self.store is store:
                self.selected_month=value['months'][-1];self.category_filter='全部分类';self.search_text='';self.save_preferences();self.show(self.page)
        def close():
            if state['busy']:status.config(text='正在保存，请等待完成后关闭。');return
            window.destroy()
        preview_button=self.button(actions,'预览数量',show_preview)
        generate_button=self.button(actions,'生成演示账单',generate)
        view_button=self.button(actions,'查看生成账单',finish_view);view_button.config(state='disabled')
        self.button(actions,'关闭',close);window.protocol('WM_DELETE_WINDOW',close)
        def cleanup(event):
            if event.widget is window and hasattr(window,'poll'):window.after_cancel(window.poll)
        window.bind('<Destroy>',cleanup);show_preview()
