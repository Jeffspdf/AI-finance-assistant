"""Session-only conversation history and confirmed deterministic actions."""
from .ui_scale import px, logical, point_font, set_window_geometry
from datetime import date
import re,queue,threading
import tkinter as tk
from tkinter import ttk,messagebox
from .qa import answer_question,month_shift
from .categories import CATEGORIES
from .importer import money_to_cents
from .ui_theme import BG,INK,MUTED,PRIMARY

class ConversationFeatures:
    def conversation_page(self):
        self.label(self.main,'查询、追问或设置预算，所有金额由程序核对。',11,MUTED)
        if not self.model_client:
            box=tk.Frame(self.main,bg='#e6f4f1',padx=px(12),pady=px(8));box.pack(fill='x',pady=px(8))
            self.label(box,'本地助手可直接使用；商户和日期区间等问题可连接自己的模型扩展。',10,PRIMARY)
            self.button(box,'连接自己的模型',self.show_model_settings)
        self.chat_messages=tk.Frame(self.main,bg=BG);self.chat_messages.pack(fill='x')
        self.draw_chat()
        self.chat_status=self.label(self.main,'会话仅保留在本次运行中，最多显示最近 20 条消息。',9,MUTED)
        chips=tk.Frame(self.main,bg=BG);chips.pack(fill='x',pady=px(8))
        for question in ['上个月餐饮花了多少？','本月结余是多少？','餐饮占比呢？']:
            self.button(chips,question,lambda q=question:self.ask(q))
        row=tk.Frame(self.main,bg=BG);row.pack(fill='x',pady=px(8))
        self.question=ttk.Entry(row,font=('Microsoft YaHei UI', 10));self.question.pack(side='left',fill='x',expand=True,ipady=px(3),padx=px((0,10)))
        self.question.bind('<Return>',lambda _:self.ask());self.button(row,'发送',self.ask)
        self.button(row,'清空会话',self.clear_chat)
        # Keep a latest-answer accessor for existing integration tests.
        self.answer=tk.Label(self.main,text='',bg='white')
        self.evidence_box=tk.Frame(self.main,bg=BG)

    def clear_chat(self):
        self.chat_histories[self.mode]=[];self.chat_context.pop(self.mode,None);self.pending_clarification=None;self.draw_chat()

    def draw_chat(self):
        for child in self.chat_messages.winfo_children():child.destroy()
        history=self.chat_histories[self.mode][-20:]
        if not history:
            self.label(self.chat_messages,'试试“上个月餐饮花了多少？”或“把餐饮预算设为 1200”。',12)
        for message in history:
            mine=message['role']=='user';bubble=tk.Frame(self.chat_messages,bg='#e6f4f1' if mine else 'white',padx=px(14),pady=px(10),highlightbackground='#e6ece9',highlightthickness=px(1))
            bubble.pack(fill='x',padx=px((60,0) if mine else (0,40)),pady=px(6))
            self.label(bubble,'你' if mine else '小财灵',9,PRIMARY)
            label=self.label(bubble,message['text'],11,INK);label.configure(wraplength=max(px(200),self.content_canvas.winfo_width()-px(100)))
            bubble.bind('<Configure>',lambda event,widget=label:widget.configure(wraplength=max(px(100),event.width-px(32))))
            evidence=message.get('evidence',[])
            if evidence:self.button(bubble,f'查看 {len(evidence)} 笔计算依据',lambda rows=evidence:self.chat_evidence(rows))
            if message.get('trace'):self.button(bubble,'公式与关联决定',lambda trace=message['trace']:self.show_calculation_trace(trace))
            if not mine:
                followups=tk.Frame(bubble,bg='white');followups.pack(fill='x',pady=px((4,0)))
                self.button(followups,'本月结余',lambda:self.ask('本月结余是多少？'))
                self.button(followups,'餐饮占比',lambda:self.ask('餐饮占比呢？'))

    def chat_evidence(self,rows):
        window=tk.Toplevel(self);window.title('回答时的计算依据');set_window_geometry(window, 850, 550);window.configure(bg=BG)
        self.label(window,'显示回答当时的源记录快照；后续修改不会重写历史答案。',10,MUTED)
        self.table(window,['日期','商户','类型','金额 / 元','分类','源文件','行号'],[[r['date'][:10],r['merchant'],r['kind'],f"{r['amount_cents']/100:,.2f}",r['category'],r['source_file'],r['source_row']] for r in rows],8)

    def _chat_reply(self,text,evidence=None,mode=None,trace=None):
        mode=mode or self.mode;self.chat_histories[mode].append(dict(role='assistant',text=text,evidence=evidence or [],trace=trace))
        self.chat_histories[mode]=self.chat_histories[mode][-20:]
        if self.mode==mode and self.page=='assistant':
            self.draw_chat();self.after_idle(lambda:self.content_canvas.yview_moveto(1))

    def conversation_ask(self,question=None):
        if getattr(self,'chat_busy',False):
            self.chat_status.config(text='正在核对上一条问题，请稍候。');return
        q=question if isinstance(question,str) else self.question.get().strip()
        if not q:return
        self.question.delete(0,'end')
        if len(q)>500:self._chat_reply('问题过长，请缩短到 500 字以内。');return
        self.chat_histories[self.mode].append(dict(role='user',text=q));self.draw_chat()
        # Exact command patterns only. No model output performs a write.
        categories='|'.join(sorted(CATEGORIES,key=len,reverse=True))
        action=re.fullmatch(r'(?:把)?(?:(本月|上个月))?('+categories+r')预算(?:设为|设置为|改为)\s*(\d+(?:\.\d{1,2})?)\s*(?:元)?[。！!]?',q)
        if action:
            period,category,value=action.groups();month=month_shift(date.today(),-1 if period=='上个月' else 0)
            try:cents=money_to_cents(value,allow_zero=True)
            except ValueError as exc:self._chat_reply(str(exc));return
            if messagebox.askyesno('确认预算修改',f'将 {month} 的{category}预算设为 ¥{cents/100:,.2f}？'+('（未指定月份，默认本月）' if period is None else '')):
                self.store.save_budget(month,category,cents)
                from .evidence import calculation
                trace=calculation('action','budget.user_confirmed',self.store.transactions().iloc[:0],
                                  inputs={'user_confirmed':True},extra=[dict(type='Budget',id=month+'/'+category,snapshot=dict(month=month,category=category,amount_cents=cents))])
                self._chat_reply(f'已设置 {month} {category}预算 ¥{cents/100:,.2f}。可在预算页修改。',trace=trace)
            else:self._chat_reply('已取消，预算没有改变。')
            return
        if self.pending_clarification and self.pending_clarification[0]==self.mode:
            period=self.pending_clarification[1]
            if q in ['全部','全部支出',*CATEGORIES]:
                q=period+('总支出多少？' if q.startswith('全部') else q+'支出多少？');self.pending_clarification=None
            else:self.pending_clarification=None
        if q.rstrip('？?') in ['上个月花了多少','本月花了多少','这个月花了多少']:
            period='上个月' if '上个月' in q else '本月';self.pending_clarification=(self.mode,period)
            self._chat_reply(f'你想看{period}全部支出，还是某个分类？回复“全部”或“餐饮”等类别。');return
        context=self.chat_context.get(self.mode,'本月')
        if q in ['餐饮占比呢？','餐饮占比呢','餐饮呢？','餐饮呢']:
            q=context+('餐饮占比多少？' if '占比' in q else '餐饮支出多少？')
        if '上个月' in q or '上月' in q:self.chat_context[self.mode]='上个月'
        elif '本月' in q or '这个月' in q:self.chat_context[self.mode]='本月'
        else:
            explicit_month=re.search(r'20\d{2}-(0[1-9]|1[0-2])(?![-\d])',q)
            if explicit_month:self.chat_context[self.mode]=explicit_month.group(0)
        mode=self.mode;frame=self.frame.copy();budgets=self.qa_budgets(frame);model=self.model_client
        if model:
            self.chat_busy=True;self.chat_status.config(text='正在核对问题与账单…');self.animate_character('thinking');mailbox=queue.Queue()
            def worker():
                try:result=answer_question(frame,q,budgets=budgets,model=model)
                except Exception:result=dict(answer='本次查询失败，请重试或切回本地模式。',evidence=[],status='unsupported',parser='本地处理')
                mailbox.put(result)
            def poll():
                try:result=mailbox.get_nowait()
                except queue.Empty:self.after(100,poll);return
                self.chat_busy=False;self.finish_chat(result,mode)
            threading.Thread(target=worker,daemon=True).start();self.after(100,poll)
        else:self.finish_chat(answer_question(frame,q,budgets=budgets),mode)

    def finish_chat(self,result,mode):
        self._chat_reply(result['answer'],result.get('evidence'),mode,trace=result.get('trace'))
        if self.page=='assistant' and self.mode==mode:
            self.answer.config(text=result['answer']);self.chat_status.config(text='已按本机记录核对。' if result['status']=='ok' else '可以补充条件，或尝试下方问题。')
            self.animate_character('success' if result['status']=='ok' else 'idle')
