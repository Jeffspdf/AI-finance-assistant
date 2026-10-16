"""Local desktop prototype. Run with the project's Python environment."""
from pathlib import Path
from datetime import date
import os
import sys
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from PIL import Image, ImageTk

from finance.storage import Store
from finance.demo import demo_bytes
from finance.importer import parse_statement, money_to_cents
from finance.analytics import overview, plan_savings, yuan
from finance.qa import answer_question
from finance.categories import CATEGORIES

ROOT = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get('FINANCE_DESKTOP_DATA_DIR', str(Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'ShiguangLedger')))
BG = '#f5f8f7'
INK = '#243c38'
MUTED = '#718681'
BLUE = '#269580'
GREEN = '#168b73'


class Desktop(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('AI理财小助手')
        self.geometry('1280x860')
        self.minsize(1140, 800)
        self.configure(bg=BG)
        self.option_add('*Font', ('Microsoft YaHei UI', 10))
        style = ttk.Style(self)
        style.theme_use('clam')
        style.configure('Treeview', rowheight=35, background='white', fieldbackground='white', foreground=INK, borderwidth=0, font=('Microsoft YaHei UI', 10))
        style.configure('Treeview.Heading', background='#edf5f1', foreground=INK, padding=10, relief='flat')
        style.map('Treeview', background=[('selected', '#dcefe7')], foreground=[('selected', INK)])
        style.configure('TEntry', padding=7, fieldbackground='white', bordercolor='#dce7e2')
        style.configure('TCombobox', padding=6)
        self.mode = 'demo'
        self.store = Store(DATA_DIR / 'demo.sqlite3')
        if self.store.transactions().empty:
            self.store.import_rows(parse_statement(demo_bytes(), 'desktop_demo.csv').rows)
        side = tk.Frame(self, bg='white', width=238)
        side.pack(side='left', fill='y'); side.pack_propagate(False)
        tk.Label(side, text='AI理财小助手', bg='white', fg=INK, font=('Microsoft YaHei UI', 19, 'bold')).pack(anchor='w', padx=22, pady=(30, 5))
        tk.Label(side, text='FINANCE, WITH A LITTLE JOY', bg='white', fg=MUTED, font=('Segoe UI', 8)).pack(anchor='w', padx=24, pady=(0, 30))
        self.nav = {}
        for label, page in [('01    财务概览', 'home'), ('02    交易账本', 'records'), ('03    储蓄规划', 'plan'), ('04    问问助手', 'assistant')]:
            b = tk.Button(side, text=label, anchor='w', command=lambda p=page: self.show(p), bg='white', fg=MUTED, activebackground='#e6f4ec', activeforeground=GREEN, relief='flat', borderwidth=0, padx=20, pady=13, cursor='hand2')
            b.pack(fill='x', padx=14, pady=3); self.nav[page] = b
        spaces = tk.Frame(side, bg='white'); spaces.pack(side='bottom', fill='x', padx=18, pady=14)
        self.space_label = tk.Label(spaces, text='演示空间 · 合成账单', bg='white', fg=MUTED, pady=8)
        self.space_label.pack()
        tk.Button(spaces, text='切换演示 / 个人空间', command=self.switch_space, relief='flat', bg='#edf5f1', fg=GREEN, pady=8).pack(fill='x')
        tk.Button(spaces, text='打开数据文件夹', command=lambda: os.startfile(str(DATA_DIR)), relief='flat', bg='white', fg=MUTED, pady=7).pack(fill='x', pady=3)
        self.guide = tk.Frame(side, bg='#f3f9f5', padx=12, pady=12)
        self.guide.pack(side='bottom', fill='x', padx=16, pady=8)
        self.mascot_photo = None
        asset = ROOT / 'assets' / 'caishen.png'
        if asset.exists():
            image = Image.open(asset).convert('RGBA'); image.thumbnail((125, 125), Image.Resampling.LANCZOS)
            self.mascot_photo = ImageTk.PhotoImage(image)
            tk.Label(self.guide, image=self.mascot_photo, bg='#f3f9f5').pack()
        tk.Label(self.guide, text='小财神 · 你的记账搭子', bg='#f3f9f5', fg=GREEN, font=('Microsoft YaHei UI', 10, 'bold')).pack(pady=(4, 5))
        self.tip = tk.Label(self.guide, text='', bg='#f3f9f5', fg=MUTED, wraplength=180, height=3, justify='left', font=('Microsoft YaHei UI', 9))
        self.tip.pack(fill='x')
        self.guide_action = tk.Button(self.guide, text='开始记账 →', bg='#e0f0e7', fg=GREEN, relief='flat', pady=6, command=lambda: self.show('records'))
        self.guide_action.pack(fill='x', pady=(8, 0))
        controls = tk.Frame(spaces, bg='white'); controls.pack(fill='x')
        self.guide_toggle = tk.Button(controls, text='收起向导', command=self.toggle_guide, bg='white', fg=MUTED, relief='flat')
        self.guide_toggle.pack(side='left')
        tk.Button(controls, text='桌面小财神 ↗', command=self.show_pet, bg='white', fg=GREEN, relief='flat').pack(side='right')
        self.pet = None
        self.main = tk.Frame(self, bg=BG)
        self.main.pack(side='left', fill='both', expand=True, padx=30, pady=24)
        self.page = 'home'
        self.show('home')

    def toggle_guide(self):
        if self.guide.winfo_manager():
            self.guide.pack_forget(); self.guide_toggle.config(text='显示向导')
        else:
            self.guide.pack(side='bottom', fill='x', padx=16, pady=8)
            self.guide_toggle.config(text='收起向导')

    def show_pet(self):
        if self.pet is not None and self.pet.winfo_exists():
            self.pet.lift(); return
        asset = ROOT / 'assets' / 'caishen.png'
        if not asset.exists(): return
        pet = tk.Toplevel(self); self.pet = pet
        pet.overrideredirect(True); pet.attributes('-topmost', True)
        transparent = '#fff0fe'; pet.configure(bg=transparent)
        if sys.platform == 'win32': pet.wm_attributes('-transparentcolor', transparent)
        pet.geometry(f'240x325+{max(0,self.winfo_screenwidth()-290)}+{max(0,self.winfo_screenheight()-420)}')
        picture = Image.open(asset).convert('RGBA'); picture.thumbnail((175, 210), Image.Resampling.LANCZOS)
        pet.photo = ImageTk.PhotoImage(picture)
        character = tk.Label(pet, image=pet.photo, bg=transparent, cursor='fleur'); character.place(relx=.5, y=65, anchor='n')
        tk.Label(pet, text='小财神陪你慢慢攒。\n拖动角色移动位置', bg='#e7f3ec', fg=GREEN, padx=12, pady=9, font=('Microsoft YaHei UI', 10)).place(relx=.5, y=8, anchor='n')
        tk.Button(pet, text='×', command=pet.destroy, relief='flat', bg='#e7f3ec', fg=MUTED).place(x=218, y=0)
        tk.Button(pet, text='打开小助手', command=lambda: (self.deiconify(), self.lift()), relief='flat', bg='#e7f3ec', fg=GREEN, padx=16, pady=6).place(relx=.5, y=282, anchor='n')
        def start(event): pet.drag = (event.x_root-pet.winfo_x(), event.y_root-pet.winfo_y())
        def move(event):
            if hasattr(pet, 'drag'): pet.geometry(f'+{max(0,event.x_root-pet.drag[0])}+{max(0,event.y_root-pet.drag[1])}')
        character.bind('<ButtonPress-1>', start); character.bind('<B1-Motion>', move)
        import math
        def animate(tick=0):
            if not pet.winfo_exists(): return
            character.place_configure(y=66+int(3*math.sin(tick/10)))
            pet.animation = pet.after(80, lambda: animate(tick+1))
        def cleanup(event):
            if event.widget is pet and hasattr(pet, 'animation'):
                pet.after_cancel(pet.animation)
        pet.bind('<Destroy>', cleanup); animate()

    def switch_space(self):
        self.mode = 'personal' if self.mode == 'demo' else 'demo'
        self.store = Store(DATA_DIR / (self.mode + '.sqlite3'))
        self.space_label.config(text='个人空间 · 本机账单' if self.mode == 'personal' else '演示空间 · 合成账单')
        self.show(self.page)

    def label(self, parent, text, size=11, color=INK, bold=False):
        w = tk.Label(parent, text=text, bg=parent['bg'], fg=color, font=('Microsoft YaHei UI', size, 'bold' if bold else 'normal'), anchor='w', justify='left')
        w.pack(anchor='w', pady=4)
        return w

    def button(self, parent, text, command):
        b = tk.Button(parent, text=text, command=command, bg=BLUE, fg='white', activebackground='#207c6b', activeforeground='white', relief='flat', borderwidth=0, padx=17, pady=10, cursor='hand2')
        b.pack(side='left', padx=(0, 10), pady=10)
        return b

    def show(self, page):
        self.page = page
        for key, button in self.nav.items():
            button.config(bg='#e7f3ec' if key == page else 'white', fg=GREEN if key == page else MUTED)
        tips = {'home': ('看清收支，留出小目标的预算。演示账单不代表真实资产。', '去看看账单 →', 'records'), 'records': ('先核对分类，点选交易可查看原始文件和行号。', '规划一个小目标 →', 'plan'), 'plan': ('试着调整月预算，看看两种方案差在哪里。', '问问消费情况 →', 'assistant'), 'assistant': ('我来指路，代码算钱。当前为本地规则模式，未连接大模型。', '返回财务概览 →', 'home')}
        tip, action, target = tips[page]
        self.tip.config(text=tip)
        self.guide_action.config(text=action, command=lambda p=target: self.show(p))
        for child in self.main.winfo_children(): child.destroy()
        self.frame = self.store.transactions()
        titles = {'home': ('把生活记好，把小目标存好。', '财务概览  /  每一笔记录，都让计划更清楚一点。'), 'records': ('好好记账，心里有数。', '交易账本  /  导入、核对与修正，每笔都有来源。'), 'plan': ('给下一个愿望，留点预算。', '储蓄规划  /  试试不同的预算，找到舒服的节奏。'), 'assistant': ('关于钱的小疑问，一起理清。', '本地规则模式  /  回答来自已导入账单，不向外发送数据。')}
        self.label(self.main, titles[page][0], 25, bold=True)
        self.label(self.main, titles[page][1], 10, MUTED)
        getattr(self, page)()

    def table(self, parent, columns, rows, height=9):
        box = tk.Frame(parent, bg='white'); box.pack(fill='both', expand=True, pady=12)
        tree = ttk.Treeview(box, columns=list(range(len(columns))), show='headings', height=height)
        for i, name in enumerate(columns):
            tree.heading(i, text=name); tree.column(i, width=105, minwidth=55, anchor='w')
        scroll = ttk.Scrollbar(box, orient='vertical', command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y'); tree.pack(fill='both', expand=True)
        tree.tag_configure('alternate', background='#f8fbf9')
        for index, row in enumerate(rows): tree.insert('', 'end', values=row, tags=('alternate',) if index % 2 else ())
        return tree

    def home(self):
        f = self.frame
        income = int(f.loc[f.kind == '收入', 'amount_cents'].sum())
        expense = int(f.loc[f.kind == '支出', 'amount_cents'].sum())
        band = tk.Frame(self.main, bg=BG); band.pack(fill='x', pady=20)
        for title, value, color in [('累计收入', income, INK), ('累计支出', expense, INK), ('累计结余', income-expense, GREEN)]:
            card = tk.Frame(band, bg='#e8f4ec' if title == '累计结余' else 'white', padx=20, pady=20); card.pack(side='left', fill='x', expand=True, padx=(0, 12))
            self.label(card, title, 10, MUTED)
            self.label(card, '¥ '+yuan(value), 23, color, True)
        monthly, categories = overview(f)
        self.label(self.main, '消费结构 / 全部已导入记录', 14, bold=True)
        canvas = tk.Canvas(self.main, height=180, bg='white', highlightthickness=0)
        canvas.pack(fill='x', pady=10)
        top = categories.head(5)
        maxval = max(top['支出'].max(), 1) if len(top) else 1
        for i, (_, row) in enumerate(top.iterrows()):
            y = 24+i*32
            canvas.create_text(20, y, text=row['分类'], anchor='w', fill=INK, font=('Microsoft YaHei UI', 10))
            canvas.create_rectangle(110, y-6, 500, y+6, fill='#f0f5f2', outline='')
            canvas.create_rectangle(110, y-6, 110+float(row['支出'])/maxval*390, y+6, fill=[BLUE, '#75b9a2', '#9ac6b4', '#e6c997', '#b1b6d8'][i], outline='')
            canvas.create_text(530, y, text=f"¥ {row['支出']:,.2f}   ·   {row['占比']}%", anchor='w', fill=MUTED)
        self.label(self.main, '月度收支', 14, bold=True)
        self.table(self.main, ['月份', '收入 / 元', '支出 / 元', '结余 / 元'], [[r['月份'], f"{r['收入']:,.2f}", f"{r['支出']:,.2f}", f"{r['结余']:,.2f}"] for _, r in monthly.iterrows()], 4)
        self.label(self.main, f'{"演示空间" if self.mode == "demo" else "个人空间"} · {len(f)} 笔记录 · 转账不计入收支 · 结余不等于账户余额', 9, MUTED)

    def records(self):
        bar = tk.Frame(self.main, bg=BG); bar.pack(fill='x')
        self.button(bar, '＋ 导入账单', self.import_file)
        self.button(bar, '查看源记录', self.source)
        self.category = ttk.Combobox(bar, values=CATEGORIES, state='readonly', width=12)
        self.category.set('餐饮'); self.category.pack(side='left', padx=8)
        self.button(bar, '保存分类', self.correct)
        self.record_rows = self.frame.sort_values('date', ascending=False).to_dict('records')
        self.tree = self.table(self.main, ['日期', '商户', '类型', '金额 / 元', '分类', '分类来源'], [[r['date'][:10], r['merchant'], r['kind'], yuan(r['amount_cents']), r['category'], r['category_source']] for r in self.record_rows], 16)
        self.label(self.main, '选中一笔交易可修正分类或查看文件名、原始行号和交易 ID。', 10, MUTED)

    def selected(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo('选择记录', '请先选择一条交易。'); return None
        return self.record_rows[self.tree.index(sel[0])]

    def source(self):
        r = self.selected()
        if r:
            messagebox.showinfo('源记录', f"交易 ID：{r['external_id']}\n文件：{r['source_file']}\n原始行号：{r['source_row']}\n时间：{r['date']}\n描述：{r['description']}\n分类来源：{r['category_source']}")

    def correct(self):
        r = self.selected()
        if r:
            self.store.correct_categories([(r['id'], self.category.get())]); self.show('records')

    def import_file(self):
        filename = filedialog.askopenfilename(filetypes=[('账单文件', '*.csv *.xlsx')])
        if not filename: return
        try:
            result = parse_statement(Path(filename).read_bytes(), Path(filename).name)
            detail = '\n'.join(str(x) for x in result.issues[:6])
            text = f'有效 {len(result.rows)} 行；异常 {len(result.issues)} 行；跳过 {result.skipped} 行。\n'+detail
            if result.warnings: text += '\n'+'\n'.join(result.warnings)
            if not result.rows:
                messagebox.showinfo('导入检查', text); return
            if not messagebox.askyesno('确认导入至'+('演示空间' if self.mode == 'demo' else '个人空间'), text+'\n\n是否导入有效记录？'): return
            added, duplicate, conflicts = self.store.import_rows(result.rows)
            messagebox.showinfo('导入结果', f'新增 {added} 笔，重复 {duplicate} 笔，冲突 {len(conflicts)} 笔。\n'+'\n'.join(str(x) for x in conflicts[:5]))
            self.show('records')
        except Exception as exc: messagebox.showerror('导入失败', str(exc))

    def plan(self):
        form = tk.Frame(self.main, bg='white', padx=20, pady=14); form.pack(fill='x', pady=20)
        self.inputs = {}
        today = date.today()
        index = today.year*12+today.month-1+6
        y, m = divmod(index, 12)
        from calendar import monthrange
        deadline = date(y, m+1, min(today.day, monthrange(y, m+1)[1]))
        values = [('目标金额 / 元', '6000'), ('已有储蓄 / 元', '0'), ('每月收入 / 元', '5000'), ('方案 A 月预算 / 元', '4200'), ('方案 B 月预算 / 元', '3800'), ('截止日期 YYYY-MM-DD', deadline.isoformat())]
        for i, (name, value) in enumerate(values):
            r, c = divmod(i, 3)
            field = tk.Frame(form, bg='white'); field.grid(row=r, column=c, padx=12, pady=10, sticky='ew')
            self.label(field, name, 10, MUTED)
            entry = ttk.Entry(field, width=23); entry.insert(0, value); entry.pack(fill='x')
            self.inputs[name] = entry
        buttons = tk.Frame(self.main, bg=BG); buttons.pack(fill='x')
        self.button(buttons, '比较两个方案', self.calculate)
        self.button(buttons, '保存方案 B', self.save_plan)
        self.plan_result = tk.Label(self.main, text='', bg='white', fg=INK, justify='left', anchor='nw', padx=24, pady=22, font=('Microsoft YaHei UI', 12), wraplength=780)
        self.plan_result.pack(fill='both', expand=True, pady=15)
        self.label(self.main, '假设收入、预算固定；每月同日存入，不计利息。保存仅记录计划，不代表已存入资金。', 9, MUTED)
        self.calculate()

    def plan_values(self):
        v = [e.get() for e in self.inputs.values()]
        cents = [money_to_cents(x, allow_zero=True) for x in v[:5]]
        return cents, v[5]

    def calculate(self):
        try:
            (target, saved, income, a, b), deadline = self.plan_values()
            lines = []
            for name, budget in [('A', a), ('B', b)]:
                p = plan_savings(target, saved, date.today(), deadline, income, budget)
                need = yuan(p['required_monthly']) if p['required_monthly'] is not None else '无完整存入日'
                lines.append(f"方案 {name}  ·  {'预计可达成' if p['feasible'] else '存在目标缺口'}\n月预算 ¥ {yuan(budget)}   /   月可存 ¥ {yuan(p['monthly_available'])}\n{p['months']} 次存入后预计 ¥ {yuan(p['projected'])}，距离目标差 ¥ {yuan(p['deficit'])}\n每月需存：{need}" + ('\n注意：收入小于预算，当前存在负现金流。' if p['negative_cashflow'] else ''))
            self.plan_result.config(text='\n\n'.join(lines))
            return True
        except Exception as exc:
            self.plan_result.config(text='请检查输入：'+str(exc)); return False

    def save_plan(self):
        if not self.calculate(): return
        (target, saved, income, a, b), deadline = self.plan_values()
        self.store.save_goal(dict(name='桌面储蓄计划 B', target_cents=target, saved_cents=saved, start_date=date.today().isoformat(), deadline=deadline, income_cents=income, budget_cents=b))
        messagebox.showinfo('计划已保存', '方案 B 已保存至当前空间的数据库。')

    def assistant(self):
        self.label(self.main, '有依据的回答，从一个好问题开始。', 16, bold=True)
        quick = tk.Frame(self.main, bg=BG); quick.pack(fill='x', pady=8)
        for text in ['上个月餐饮花了多少？', '支出最多的分类是哪个？', '累计结余是多少？']:
            self.button(quick, text, lambda q=text: self.ask(q))
        row = tk.Frame(self.main, bg=BG); row.pack(fill='x')
        self.question = ttk.Entry(row, font=('Microsoft YaHei UI', 12)); self.question.pack(side='left', fill='x', expand=True, ipady=10, padx=(0, 10))
        self.question.bind('<Return>', lambda _: self.ask())
        self.button(row, '分析记录', self.ask)
        self.answer = tk.Label(self.main, text='本地演示模式\n\n支持月份、收入、支出、结余、分类占比等问题。\n回答中的金额均由代码汇总，下方显示全部依据。', bg='white', fg=INK, wraplength=780, anchor='nw', justify='left', padx=22, pady=20, font=('Microsoft YaHei UI', 12))
        self.answer.pack(fill='x', pady=14)
        self.evidence_box = tk.Frame(self.main, bg=BG); self.evidence_box.pack(fill='both', expand=True)

    def ask(self, question=None):
        q = question if isinstance(question, str) else self.question.get()
        self.question.delete(0, 'end'); self.question.insert(0, q)
        result = answer_question(self.frame, q)
        self.answer.config(text=result['answer'])
        for child in self.evidence_box.winfo_children(): child.destroy()
        self.label(self.evidence_box, f"来源依据 · {len(result['evidence'])} 笔 · {result['parser']}", 10, MUTED)
        self.table(self.evidence_box, ['日期', '商户', '金额 / 元', '类别', '源文件', '行号'], [[r['date'][:10], r['merchant'], yuan(r['amount_cents']), r['category'], r['source_file'], r['source_row']] for r in result['evidence']], 6)


if __name__ == '__main__':
    import traceback
    try:
        app = Desktop()
        if '--self-test' in sys.argv:
            app.update()
            assert len(app.frame) >= 100
            for page in ['records', 'plan', 'assistant', 'home']:
                app.show(page); app.update()
            app.switch_space(); app.update()
            assert app.mode == 'personal'
            app.switch_space(); app.update()
            app.show('assistant'); app.ask('累计结余是多少？')
            assert '结余为' in app.answer.cget('text')
            (DATA_DIR / 'self-test.ok').write_text('Desktop smoke test passed', encoding='utf-8')
            app.destroy()
        else:
            app.mainloop()
    except Exception:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        (DATA_DIR / 'startup-error.log').write_text(traceback.format_exc(), encoding='utf-8')
        if '--self-test' not in sys.argv:
            messagebox.showerror('启动失败', '错误记录已保存至：'+str(DATA_DIR / 'startup-error.log'))
        raise
