"""Local desktop prototype. Run with the project's Python environment."""
from pathlib import Path
from datetime import date
import os
import sys
import queue
import threading
import webbrowser
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from PIL import Image, ImageTk

from finance.storage import Store
from finance.demo import demo_bytes
from finance.importer import parse_statement, money_to_cents
from finance.analytics import overview, plan_savings, yuan
from finance.qa import answer_question
from finance.categories import CATEGORIES
from finance.updates import APP_VERSION, RELEASES_URL, check_latest, download_installer
from finance.llm import ModelClient
import json
from finance.animation import SpritePlayer

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
        self.model_client = None
        self.model_key = ''
        self.model_settings = {}
        try:
            self.model_settings = json.loads((DATA_DIR / 'model-settings.json').read_text(encoding='utf-8'))
        except (OSError, ValueError): pass
        self.ask_generation = 0
        self.animators=[]
        self.animation_paused=False
        self.store = Store(DATA_DIR / 'demo.sqlite3')
        if self.store.transactions().empty:
            self.store.import_rows(parse_statement(demo_bytes(), 'desktop_demo.csv').rows)
        side = tk.Frame(self, bg='white', width=238)
        side.pack(side='left', fill='y'); side.pack_propagate(False)
        tk.Label(side, text='AI理财小助手', bg='white', fg=INK, font=('Microsoft YaHei UI', 19, 'bold')).pack(anchor='w', padx=22, pady=(18, 5))
        tk.Label(side, text='FINANCE, WITH A LITTLE JOY', bg='white', fg=MUTED, font=('Segoe UI', 8)).pack(anchor='w', padx=24, pady=(0, 12))
        self.nav = {}
        for label, page in [('01    财务概览', 'home'), ('02    交易账本', 'records'), ('03    储蓄规划', 'plan'), ('04    问问助手', 'assistant')]:
            b = tk.Button(side, text=label, anchor='w', command=lambda p=page: self.show(p), bg='white', fg=MUTED, activebackground='#e6f4ec', activeforeground=GREEN, relief='flat', borderwidth=0, padx=20, pady=7, cursor='hand2')
            b.pack(fill='x', padx=14, pady=1); self.nav[page] = b
        spaces = tk.Frame(side, bg='white'); spaces.pack(side='bottom', fill='x', padx=18, pady=6)
        self.space_label = tk.Label(spaces, text='演示空间 · 合成账单', bg='white', fg=MUTED, pady=3)
        self.space_label.pack()
        tk.Button(spaces, text='模型设置 / 自定义 API', command=self.show_model_settings, relief='flat', bg='#edf5f1', fg=GREEN, pady=6).pack(fill='x', pady=(0,4))
        tk.Button(spaces, text='切换演示 / 个人空间', command=self.switch_space, relief='flat', bg='#edf5f1', fg=GREEN, pady=8).pack(fill='x')
        links = tk.Frame(spaces, bg='white'); links.pack(fill='x', pady=3)
        tk.Button(links, text='数据文件夹', command=lambda: os.startfile(str(DATA_DIR)), relief='flat', bg='white', fg=MUTED, pady=7).pack(side='left')
        tk.Button(links, text='关于与更新', command=self.show_updates, relief='flat', bg='white', fg=GREEN, pady=7).pack(side='right')
        self.update_window = None
        self.guide = tk.Frame(side, bg='#f3f9f5', padx=12, pady=6)
        self.guide.pack(side='bottom', fill='x', padx=16, pady=8)
        self.mascot_photo = None
        asset = ROOT / 'assets' / 'campus_heroine.png'
        if asset.exists():
            image = Image.open(asset).convert('RGBA'); image.thumbnail((170, 290), Image.Resampling.LANCZOS)
            self.mascot_photo = ImageTk.PhotoImage(image)
            portrait = tk.Frame(self.guide, bg='#f3f9f5', height=100)
            portrait.pack(fill='x'); portrait.pack_propagate(False)
            portrait_label=tk.Label(portrait, image=self.mascot_photo, bg='#f3f9f5',cursor='hand2')
            portrait_label.place(relx=.5, y=0, anchor='n')
            sheet=ROOT/'assets'/'campus_animation.png'
            if sheet.exists():
                player=SpritePlayer(portrait_label,sheet,(150,100));self.animators.append(player)
                portrait_label.bind('<Button-1>',lambda _:self.animate_character('wave'))
        tk.Label(self.guide, text='小财灵 · 校园理财搭子', bg='#f3f9f5', fg=GREEN, font=('Microsoft YaHei UI', 10, 'bold')).pack(pady=(4, 5))
        self.tip = tk.Label(self.guide, text='', bg='#f3f9f5', fg=MUTED, wraplength=180, height=3, justify='left', font=('Microsoft YaHei UI', 9))
        self.tip.pack(fill='x')
        self.guide_action = tk.Button(self.guide, text='开始记账 →', bg='#e0f0e7', fg=GREEN, relief='flat', pady=6, command=lambda: self.show('records'))
        self.guide_action.pack(fill='x', pady=(8, 0))
        controls = tk.Frame(spaces, bg='white'); controls.pack(fill='x')
        self.guide_toggle = tk.Button(controls, text='收起向导', command=self.toggle_guide, bg='white', fg=MUTED, relief='flat')
        self.guide_toggle.pack(side='left')
        tk.Button(controls, text='桌面小助手 ↗', command=self.show_pet, bg='white', fg=GREEN, relief='flat').pack(side='right')
        self.motion_button=tk.Button(spaces,text='暂停角色动画',command=self.toggle_animation,bg='white',fg=MUTED,relief='flat')
        self.motion_button.pack(fill='x')
        self.pet = None
        self.main = tk.Frame(self, bg=BG)
        self.main.pack(side='left', fill='both', expand=True, padx=30, pady=24)
        self.page = 'home'
        self.show('home')

    def animate_character(self,state):
        self.animators=[p for p in self.animators if not p.closed]
        for player in self.animators: player.play(state)

    def toggle_animation(self):
        self.animation_paused=not self.animation_paused
        for player in self.animators:
            if not player.closed:player.pause(self.animation_paused)
        self.motion_button.config(text='恢复角色动画' if self.animation_paused else '暂停角色动画')

    def show_model_settings(self):
        window = tk.Toplevel(self); window.title('模型设置 · 自定义 API'); window.geometry('650x610'); window.configure(bg=BG)
        box = tk.Frame(window, bg=BG, padx=24, pady=18); box.pack(fill='both', expand=True)
        self.label(box, '连接你自己的大模型', 20, bold=True)
        self.label(box, '支持兼容 Chat Completions 的接口。Base URL 填到 /v1 等根路径。', 10, MUTED)
        fields = {}
        for label, key, default in [('Base URL', 'base_url', 'https://api.deepseek.com'), ('模型名称 / Model ID', 'model', ''), ('API Key（仅本次运行有效）', 'api_key', self.model_key)]:
            self.label(box, label, 10)
            entry = ttk.Entry(box, show='•' if key == 'api_key' else '', width=65)
            entry.insert(0, default if key == 'api_key' else self.model_settings.get(key, default)); entry.pack(fill='x', pady=(0,8)); fields[key] = entry
        enabled = tk.BooleanVar(value=self.model_client is not None)
        tk.Checkbutton(box, text='启用模型，并允许向该接口发送我输入的问题文本', variable=enabled, bg=BG, fg=INK).pack(anchor='w', pady=8)
        self.label(box, '仅发送问题文本用于理解查询意图，不发送完整账单。\n金额由代码计算，回答保留源记录。测试连接可能产生少量 API 费用。\n密钥不写入配置文件，关闭应用后需重新填写并启用。', 10, MUTED)
        status = self.label(box, '填写配置后，可先测试连接。', 10, GREEN); status.config(wraplength=580)
        def client():
            base=fields['base_url'].get().strip()
            if base.endswith('/chat/completions'): raise ValueError('请填写 Base URL，不要包含 /chat/completions')
            return ModelClient(base, fields['model'].get().strip(), fields['api_key'].get().strip(), enabled=True)
        def save():
            try:
                model = client() if enabled.get() else None
                self.model_settings = {'base_url': fields['base_url'].get().strip(), 'model': fields['model'].get().strip()}
                (DATA_DIR/'model-settings.json').write_text(json.dumps(self.model_settings),encoding='utf-8')
                self.model_key = fields['api_key'].get().strip()
                self.model_client = model
                self.show(self.page); window.destroy()
            except Exception as exc: status.config(text=str(exc))
        mailbox=queue.Queue()
        def test():
            try: model=client()
            except Exception as exc: status.config(text=str(exc)); return
            test_button.config(state='disabled'); status.config(text='正在测试连接…')
            def worker():
                try: model.test_connection(); mailbox.put('连接成功，模型返回有效结果。保存并启用后生效。')
                except Exception as exc:
                    from urllib.error import HTTPError
                    if isinstance(exc, HTTPError): mailbox.put(f'连接失败：HTTP {exc.code}。请检查密钥、模型名、额度及接口地址。')
                    else: mailbox.put('连接失败：网络超时、地址不可达或返回格式不兼容。')
            threading.Thread(target=worker,daemon=True).start(); window.poll=window.after(100,poll)
        def poll():
            try: text=mailbox.get_nowait()
            except queue.Empty: window.poll=window.after(100,poll); return
            status.config(text=text); test_button.config(state='normal')
        buttons=tk.Frame(box,bg=BG); buttons.pack(fill='x',pady=8)
        test_button=self.button(buttons,'测试连接',test)
        self.button(buttons,'保存设置',save)
        def cleanup(event):
            if event.widget is window and hasattr(window,'poll'): window.after_cancel(window.poll)
        window.bind('<Destroy>',cleanup)

    def show_updates(self):
        if self.update_window is not None and self.update_window.winfo_exists():
            self.update_window.lift(); return
        window = tk.Toplevel(self); self.update_window = window
        window.title('关于与更新 · AI理财小助手')
        window.geometry('650x540'); window.minsize(580, 500)
        window.configure(bg=BG); window.transient(self)
        content = tk.Frame(window, bg=BG, padx=26, pady=22); content.pack(fill='both', expand=True)
        self.label(content, 'AI理财小助手', 21, bold=True)
        self.label(content, f'当前版本  v{APP_VERSION}  ·  Windows x64', 11, MUTED)
        self.label(content, '手动检查官方 GitHub 正式版本；不会上传账单。', 10, MUTED)
        status = self.label(content, '点击“检查更新”获取最新版本。', 11, GREEN)
        status.config(wraplength=560)
        notes = tk.Text(content, wrap='word', height=3, relief='flat', bg='white', fg=INK, padx=14, pady=12, font=('Microsoft YaHei UI', 10))
        notes.pack(fill='both', expand=True, pady=12)
        notes.insert('1.0', '发现新版本后可直接下载安装包，显示下载进度并打开下载目录。\n\n下载后请先退出本程序，再运行安装包。覆盖安装保留账单数据。\n\n如网络无法连接 GitHub，可使用浏览器下载链接。')
        notes.config(state='disabled')
        buttons = tk.Frame(content, bg=BG); buttons.pack(fill='x')
        download = self.button(buttons, '下载新版安装包 ↗', lambda: None)
        download.config(state='disabled')
        self.button(buttons, '官方发布页面 ↗', lambda: webbrowser.open(RELEASES_URL))
        mailbox = queue.Queue()
        checking = [False]
        cancel_download = threading.Event()
        download_box = queue.Queue()
        download_busy = [False]
        download_url = [None]
        progress = ttk.Progressbar(content, mode='determinate', maximum=100)
        progress.pack(fill='x',pady=4)
        browser_download = tk.Button(content,text='浏览器下载备用链接 ↗',command=lambda: webbrowser.open(download_url[0] or RELEASES_URL),bg=BG,fg=GREEN,relief='flat')
        browser_download.pack(anchor='w')

        def start_download():
            if download_busy[0] or not download_url[0]: return
            download_busy[0]=True; cancel_download.clear(); download.config(state='disabled'); check.config(state='disabled')
            status.config(text='正在连接 GitHub 下载安装包…'); progress['value']=0
            def report(received,total): download_box.put(('progress',(received,total)))
            def worker():
                try: download_box.put(('done',download_installer(download_url[0],DATA_DIR/'downloads',report,cancel_download.is_set)))
                except Exception as exc:
                    from urllib.error import HTTPError
                    message=f'下载失败：HTTP {exc.code}，请重试或使用浏览器备用链接。' if isinstance(exc,HTTPError) else '下载失败：网络不可达、超时或文件不完整。请重试或使用浏览器备用链接。'
                    download_box.put(('error',message))
            threading.Thread(target=worker,daemon=True).start(); window.download_poll=window.after(100,poll_download)

        def poll_download():
            terminal=None
            while True:
                try: kind,value=download_box.get_nowait()
                except queue.Empty: break
                if kind=='progress':
                    received,total=value
                    progress['value']=received/total*100 if total else 0
                    status.config(text=f'已下载 {received/1048576:.1f} MB'+(f' / {total/1048576:.1f} MB' if total else ''))
                else: terminal=(kind,value)
            if terminal:
                download_busy[0]=False; download.config(state='normal');check.config(state='normal')
                kind,value=terminal
                if kind=='done':
                    path,sha=value;progress['value']=100
                    status.config(text='下载完成！请先退出应用，再运行下载的安装包。')
                    notes.config(state='normal');notes.delete('1.0','end');notes.insert('1.0',f'文件：{path}\n\nSHA256：{sha}\n\n校验了传输长度及 EXE 文件头。未进行代码签名验证。');notes.config(state='disabled')
                    os.startfile(str(Path(path).parent))
                else: status.config(text=value)
            else: window.download_poll=window.after(100,poll_download)

        def start_check():
            if checking[0]: return
            checking[0] = True; check.config(state='disabled'); download.config(state='disabled')
            status.config(text='正在检查 GitHub 最新版本…')
            def worker():
                try: mailbox.put(('ok', check_latest()))
                except Exception: mailbox.put(('error', None))
            threading.Thread(target=worker, daemon=True).start()
            window.poll = window.after(100, poll)

        def poll():
            try: kind, result = mailbox.get_nowait()
            except queue.Empty:
                window.poll = window.after(100, poll); return
            checking[0] = False; check.config(state='normal')
            if kind == 'error':
                status.config(text='检查失败：网络不可用或 GitHub 限流。可重试或打开发布页面。')
                return
            status.config(text=f'发现新版本 v{result["version"]}' if result['newer'] else f'当前已是最新版本（GitHub 正式版 v{result["version"]}）。')
            notes.config(state='normal'); notes.delete('1.0', 'end'); notes.insert('1.0', result['notes']); notes.config(state='disabled')
            if result['newer'] and result['installer_url']:
                download_url[0]=result['installer_url']
                download.config(state='normal', command=start_download)
            elif result['newer']:
                status.config(text=f'发现 v{result["version"]}，请在官方发布页面查看并下载安装包。')

        check = tk.Button(content, text='检查更新', command=start_check, bg='#e0f0e7', fg=GREEN, relief='flat', pady=10)
        check.pack(fill='x', pady=(10, 0))
        def cleanup(event):
            if event.widget is window:
                cancel_download.set()
                for name in ['poll','download_poll']:
                    if hasattr(window,name): window.after_cancel(getattr(window,name))
        window.bind('<Destroy>', cleanup)

    def toggle_guide(self):
        if self.guide.winfo_manager():
            self.guide.pack_forget(); self.guide_toggle.config(text='显示向导')
        else:
            self.guide.pack(side='bottom', fill='x', padx=16, pady=8)
            self.guide_toggle.config(text='收起向导')

    def show_pet(self):
        if self.pet is not None and self.pet.winfo_exists():
            self.pet.lift(); return
        asset = ROOT / 'assets' / 'campus_heroine.png'
        if not asset.exists(): return
        pet = tk.Toplevel(self); self.pet = pet
        pet.overrideredirect(True); pet.attributes('-topmost', True)
        transparent = '#fff0fe'; pet.configure(bg=transparent)
        if sys.platform == 'win32': pet.wm_attributes('-transparentcolor', transparent)
        pet.geometry(f'240x405+{max(0,self.winfo_screenwidth()-290)}+{max(0,self.winfo_screenheight()-480)}')
        picture = Image.open(asset).convert('RGBA'); picture.thumbnail((190, 285), Image.Resampling.LANCZOS)
        pet.photo = ImageTk.PhotoImage(picture)
        character = tk.Label(pet, image=pet.photo, bg=transparent, cursor='fleur'); character.place(relx=.5, y=65, anchor='n')
        player=None
        sheet=ROOT/'assets'/'campus_animation.png'
        if sheet.exists():
            player=SpritePlayer(character,sheet,(240,160));self.animators.append(player);player.pause(self.animation_paused)
            pet.geometry(f'260x285+{max(0,self.winfo_screenwidth()-300)}+{max(0,self.winfo_screenheight()-360)}')
        tk.Label(pet, text='小财灵：聪明花，慢慢攒！\n拖动角色移动位置', bg='#e7f3ec', fg=GREEN, padx=12, pady=9, font=('Microsoft YaHei UI', 10)).place(relx=.5, y=8, anchor='n')
        tk.Button(pet, text='×', command=pet.destroy, relief='flat', bg='#e7f3ec', fg=MUTED).place(x=218, y=0)
        tk.Button(pet, text='打开小助手', command=lambda: (self.deiconify(), self.lift()), relief='flat', bg='#e7f3ec', fg=GREEN, padx=16, pady=6).place(relx=.5, y=240 if sheet.exists() else 358, anchor='n')
        def start(event):
            pet.drag = (event.x_root-pet.winfo_x(), event.y_root-pet.winfo_y())
            pet.drag_start=(event.x_root,event.y_root)
            pet.pending_position=None
            if player is not None:player.pause(True)
            character.grab_set()
        def flush_move():
            pet.move_job=None
            if pet.pending_position:
                x,y=pet.pending_position;pet.geometry(f'+{x}+{y}');pet.pending_position=None
                character.update_idletasks()
        def move(event):
            if hasattr(pet, 'drag'):
                pet.pending_position=(max(0,event.x_root-pet.drag[0]),max(0,event.y_root-pet.drag[1]))
                if not getattr(pet,'move_job',None):pet.move_job=pet.after(16,flush_move)
        character.bind('<ButtonPress-1>', start); character.bind('<B1-Motion>', move)
        def release(event):
            if getattr(pet,'move_job',None):pet.after_cancel(pet.move_job);pet.move_job=None
            if hasattr(pet,'pending_position'):flush_move()
            character.grab_release()
            if player is not None:player.pause(self.animation_paused)
            if hasattr(pet,'drag_start') and abs(event.x_root-pet.drag_start[0])+abs(event.y_root-pet.drag_start[1])<8:
                self.animate_character('wave')
            if hasattr(pet,'drag'):del pet.drag
        character.bind('<ButtonRelease-1>',release)
        def cleanup_move(event):
            if event.widget is pet and getattr(pet,'move_job',None):
                pet.after_cancel(pet.move_job);pet.move_job=None
        pet.bind('<Destroy>',cleanup_move,add='+')

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
        self.animate_character('idle')
        self.ask_generation += 1
        self.page = page
        for key, button in self.nav.items():
            button.config(bg='#e7f3ec' if key == page else 'white', fg=GREEN if key == page else MUTED)
        tips = {'home': ('让我看看钱都去哪啦！先核对收支，再给小愿望留点预算。', '一起查查账 →', 'records'), 'records': ('分类不对？选中那一笔就能改。每个数字都有源记录哦。', '安排一个小目标 →', 'plan'), 'plan': ('灵机一动！换个月预算试试，看看离目标能近多少。', '问问消费情况 →', 'assistant'), 'assistant': ('我来指路，代码算钱。当前为本地规则模式，未连接大模型。', '返回财务概览 →', 'home')}
        tip, action, target = tips[page]
        self.tip.config(text=tip)
        self.guide_action.config(text=action, command=lambda p=target: self.show(p))
        for child in self.main.winfo_children(): child.destroy()
        self.frame = self.store.transactions()
        titles = {'home': ('把生活记好，把小目标存好。', '财务概览  /  每一笔记录，都让计划更清楚一点。'), 'records': ('好好记账，心里有数。', '交易账本  /  导入、核对与修正，每笔都有来源。'), 'plan': ('给下一个愿望，留点预算。', '储蓄规划  /  试试不同的预算，找到舒服的节奏。'), 'assistant': ('关于钱的小疑问，一起理清。', '本地规则模式  /  回答来自已导入账单，不向外发送数据。')}
        self.label(self.main, titles[page][0], 25, bold=True)
        subtitle = '问题文本发送至你配置的接口；账单查询与金额由本机代码处理。' if page == 'assistant' and self.model_client else titles[page][1]
        self.label(self.main, subtitle, 10, MUTED)
        if page == 'assistant':
            self.label(self.main, f'当前：大模型理解（{self.model_client.model}）+ 代码计算' if self.model_client else '当前：本地规则模式 · 可在左下角配置模型', 10, GREEN)
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
            self.animate_character('success')
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
        self.animate_character('success')

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
        if self.model_client:
            self.animate_character('thinking')
            generation = self.ask_generation = self.ask_generation + 1
            frame=self.frame.copy(); model=self.model_client
            self.answer.config(text='正在理解问题并核对记录…')
            mailbox=queue.Queue()
            class IntentAdapter:
                def parse_question(self, text): return model.parse_question(text)
            def worker():
                try:
                    # Model is only used for phrasing outside the deterministic parser.
                    from finance.qa import parse_question, validate_model_intent
                    if parse_question(q) is not None:
                        intent=model.parse_question(q)
                        if validate_model_intent(q,intent) is None: raise ValueError('unsupported')
                        result=answer_question(frame,q)
                        result['parser']='模型理解 + 代码核对'
                    else: result=answer_question(frame,q,model=IntentAdapter())
                    mailbox.put(result)
                except Exception:
                    result=answer_question(frame,q); result['parser']='模型不可用，回退本地规则'; mailbox.put(result)
            def poll():
                if generation != self.ask_generation or self.page != 'assistant': return
                try: result=mailbox.get_nowait()
                except queue.Empty: self.after(100,poll); return
                self.render_answer(result)
            threading.Thread(target=worker,daemon=True).start(); self.after(100,poll)
            return
        result = answer_question(self.frame, q)
        self.render_answer(result)

    def render_answer(self, result):
        self.animate_character('success' if result.get('status')=='ok' else 'idle')
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
