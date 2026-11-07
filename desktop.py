"""Local desktop prototype. Run with the project's Python environment."""
from finance.ui_scale import px, logical, point_font, init_scale, fit_main_window, set_window_geometry, work_area
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
from finance.demo import demo_bytes,initialize_demo
from finance.importer import parse_statement, money_to_cents
from finance.analytics import overview, plan_savings, yuan
from finance.qa import answer_question
from finance.categories import CATEGORIES,category_options
from finance.updates import APP_VERSION, RELEASES_URL, check_latest, download_installer,update_error_message,version_status,can_download,UpdateDiagnostics
from finance.llm import ModelClient,connection_error_message,model_status_text
from finance.model_settings import ModelSettings
import json
from finance.animation import SpritePlayer
from finance.desktop_review import ReviewFeatures
from finance.desktop_round2 import RoundTwo
from finance.conversation import ConversationFeatures
from finance.desktop_m1a import M1Features
from finance.desktop_demo import DemoGeneratorFeatures
from finance.desktop_refunds import RefundFeatures
from finance.spending_presenter import spending_totals,basis_label,refund_status_summary,refund_status_text
from finance.evidence import count_records
from finance.ui_theme import apply_theme,enable_dpi_awareness,Meter,scrollable_form,BG,INK,MUTED,PRIMARY
enable_dpi_awareness()

ROOT = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get('FINANCE_DESKTOP_DATA_DIR', str(Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'ShiguangLedger')))
BLUE = PRIMARY
GREEN = PRIMARY


class Desktop(RefundFeatures,DemoGeneratorFeatures,M1Features,RoundTwo,ConversationFeatures,ReviewFeatures,tk.Tk):
    def __init__(self):
        super().__init__()
        init_scale(self)
        self.title('AI理财小助手')
        fit_main_window(self)
        self.configure(bg=BG)
        self.option_add('*Font', point_font())
        apply_theme(self)
        self.round2_init()
        self.preferences={}
        try:self.preferences=json.loads((DATA_DIR/'preferences.json').read_text(encoding='utf-8'))
        except (OSError,ValueError):pass
        self.mode=self.preferences.get('space','demo')
        if self.mode not in ('demo','personal'):self.mode='demo'
        self.selected_month=self.preferences.get('month',date.today().strftime('%Y-%m'))
        self.search_text='';self.category_filter='全部分类' 
        self.model_repository = ModelSettings(DATA_DIR)
        loaded = self.model_repository.load()
        self.model_settings = {'base_url': loaded.base_url, 'model': loaded.model,
                               'remember_key': loaded.remember_key, 'enabled': loaded.enabled}
        self.model_key = loaded.api_key
        self.model_notice = loaded.notice
        self.model_client = ModelClient(loaded.base_url, loaded.model, loaded.api_key, enabled=True) if loaded.enabled else None
        self.ask_generation = 0
        self.animators=[]
        self.animation_paused=False
        self.store = Store(DATA_DIR / (self.mode+'.sqlite3'))
        if self.mode=='demo':initialize_demo(self.store)
        self.review_initialize()
        side = tk.Frame(self, bg='white', width=px(220))
        side.pack(side='left', fill='y'); side.pack_propagate(False)
        tk.Label(side, text='AI理财小助手', bg='white', fg=INK, font=('Microsoft YaHei UI', 15, 'bold')).pack(anchor='w', padx=px(16), pady=px((18, 5)))
        tk.Label(side, text='PERSONAL FINANCE', bg='white', fg=MUTED, font=('Segoe UI', 10)).pack(anchor='w', padx=px(18), pady=px((0, 12)))
        self.nav = {}
        for label, page in [('财务概览', 'home'), ('交易账本', 'records'), ('预算与提醒', 'budget_page'), ('储蓄规划', 'plan'), ('问问助手', 'assistant')]:
            b = tk.Button(side, text=label, anchor='w', command=lambda p=page: self.show(p), bg='white', fg=MUTED, activebackground='#e6f4ec', activeforeground=GREEN, relief='flat', borderwidth=px(0), padx=px(20), pady=px(7), cursor='hand2')
            b.pack(fill='x', padx=px(14), pady=px(1)); self.nav[page] = b
        spaces = tk.Frame(side, bg='white'); spaces.pack(side='bottom', fill='x', padx=px(18), pady=px(6))
        self.space_label = tk.Label(spaces, text='演示空间 · 合成账单', bg='white', fg=MUTED, pady=px(3))
        self.space_label.pack()
        settings_button=tk.Button(spaces,text='设置与连接',command=lambda:self.show('settings'),relief='flat',bg='#edf5f1',fg=GREEN,pady=px(8))
        settings_button.pack(fill='x',pady=px(5));self.nav['settings']=settings_button
        self.update_window = None
        self.guide = tk.Frame(side, bg='#f3f9f5', padx=px(12), pady=px(6))
        self.guide.pack(side='bottom', fill='x', padx=px(16), pady=px(8))
        self.mascot_photo = None
        asset = ROOT / 'assets' / 'campus_heroine.png'
        if asset.exists():
            image = Image.open(asset).convert('RGBA'); image.thumbnail(px((170, 290)), Image.Resampling.LANCZOS)
            self.mascot_photo = ImageTk.PhotoImage(image)
            portrait = tk.Frame(self.guide, bg='#f3f9f5', height=px(80))
            portrait.pack(fill='x'); portrait.pack_propagate(False)
            portrait_label=tk.Label(portrait, image=self.mascot_photo, bg='#f3f9f5',cursor='hand2')
            portrait_label.place(relx=.5, y=px(0), anchor='n')
            sheet=ROOT/'assets'/'campus_animation.png'
            if sheet.exists():
                player=SpritePlayer(portrait_label,sheet,px((120,80)));self.animators.append(player)
                portrait_label.bind('<Button-1>',lambda _:self.animate_character('wave'))
        tk.Label(self.guide, text='小财灵 · 校园理财搭子', bg='#f3f9f5', fg=GREEN, font=('Microsoft YaHei UI', 10, 'bold')).pack(pady=px((4, 5)))
        self.tip = tk.Label(self.guide, text='', bg='#f3f9f5', fg=MUTED, wraplength=px(150), height=2, justify='left', font=('Microsoft YaHei UI', 10))
        # Keep the sidebar compact; detailed contextual hints live in the page.
        self.guide_action = tk.Button(self.guide, text='开始记账 →', bg='#e0f0e7', fg=GREEN, relief='flat', pady=px(6), command=lambda: self.show('records'))
        self.guide_action.pack(fill='x', pady=px((8, 0)))
        controls = tk.Frame(spaces, bg='white'); controls.pack(fill='x')
        self.guide_toggle = tk.Button(controls, text='收起向导', command=self.toggle_guide, bg='white', fg=MUTED, relief='flat')
        self.guide_toggle.pack(side='left')
        tk.Button(controls, text='桌面小助手', command=self.show_pet, bg='white', fg=GREEN, relief='flat').pack(side='right')
        self.motion_button=tk.Button(spaces,text='暂停角色动画',command=self.toggle_animation,bg='white',fg=MUTED,relief='flat')
        self.motion_button.pack(fill='x')
        self.pet = None
        body=tk.Frame(self,bg=BG);body.pack(side='left',fill='both',expand=True,padx=px(24),pady=px(18))
        self.content_canvas=tk.Canvas(body,bg=BG,highlightthickness=px(0))
        content_scroll=ttk.Scrollbar(body,orient='vertical',command=self.content_canvas.yview)
        self.content_canvas.configure(yscrollcommand=content_scroll.set)
        content_scroll.pack(side='right',fill='y');self.content_canvas.pack(side='left',fill='both',expand=True)
        self.main=tk.Frame(self.content_canvas,bg=BG)
        self.content_window=self.content_canvas.create_window((0,0),window=self.main,anchor='nw')
        def resize_content(event=None):
            width=self.content_canvas.winfo_width()
            self.content_canvas.itemconfigure(self.content_window,width=width)
            self.content_canvas.configure(scrollregion=self.content_canvas.bbox('all'))
        self.main.bind('<Configure>',resize_content);self.content_canvas.bind('<Configure>',resize_content)
        def scroll_content(event):
            if event.widget.winfo_class() in ('Text','TCombobox'):return
            widget=event.widget
            while widget is not None:
                if widget is self.main:self.content_canvas.yview_scroll(-int(event.delta/120),'units');return
                widget=getattr(widget,'master',None)
        self.bind('<MouseWheel>',scroll_content,add='+')
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
        window = tk.Toplevel(self); window.title('模型设置 · 自定义 API'); set_window_geometry(window, 680, 680); window.configure(bg=BG)
        buttons = tk.Frame(window, bg=BG); buttons.pack(side='bottom',fill='x',padx=px(24),pady=px(4))
        box = scrollable_form(window)
        self.label(box, '连接你自己的大模型', 20, bold=True)
        self.label(box, '支持兼容 Chat Completions 的接口。Base URL 填到 /v1 等根路径。', 10, MUTED)
        fields = {}
        for label, key, default in [('Base URL', 'base_url', 'https://api.deepseek.com'), ('模型名称 / Model ID', 'model', ''), ('API Key', 'api_key', self.model_key)]:
            self.label(box, label, 10)
            entry = ttk.Entry(box, show='•' if key == 'api_key' else '', width=65)
            entry.insert(0, default if key == 'api_key' else (self.model_settings.get(key) or default)); entry.pack(fill='x', pady=px((0,8))); fields[key] = entry
        enabled = tk.BooleanVar(value=self.model_client is not None)
        tk.Checkbutton(box, text='启用模型，并允许向该接口发送我输入的问题文本', variable=enabled, bg=BG, fg=INK).pack(anchor='w', pady=px(8))
        remember = tk.BooleanVar(value=self.model_settings.get('remember_key',True))
        tk.Checkbutton(box,text='记住密钥和启用状态（本机加密，重启/更新后恢复）',variable=remember,bg=BG,fg=INK).pack(anchor='w',pady=px(4))
        self.label(box, '密钥使用当前 Windows 用户加密保存；取消记住则仅本次有效。\n问答发送问题文本；分类另行确认发送商户、描述和收支类型。\n启动恢复不会请求接口，测试连接及实际模型调用可能产生费用。', 10, MUTED).configure(wraplength=px(590))
        status = self.label(box, self.model_notice or model_status_text(self.model_client), 10, GREEN); status.config(wraplength=px(580))
        endpoint_var=tk.StringVar(value=fields['base_url'].get());fields['base_url'].configure(textvariable=endpoint_var)
        previous_endpoint=[endpoint_var.get().strip().rstrip('/')]
        def endpoint_changed(*_):
            base=endpoint_var.get().strip().rstrip('/')
            if base!=previous_endpoint[0]:
                fields['api_key'].delete(0,'end')
                status.config(text='接口地址已更改，请填写该接口的密钥；本地免密接口可留空。')
                previous_endpoint[0]=base
        endpoint_var.trace_add('write',endpoint_changed)
        tested_client=[None]
        def client():
            base=fields['base_url'].get().strip()
            if base.endswith('/chat/completions'): raise ValueError('请填写 Base URL，不要包含 /chat/completions')
            name=fields['model'].get().strip();key=fields['api_key'].get().strip()
            for candidate in (tested_client[0],self.model_client):
                if candidate and (candidate.endpoint,candidate.model,candidate.api_key)==(base.rstrip('/')+'/chat/completions',name,key):return candidate
            return ModelClient(base,name,key,enabled=True)
        def save():
            try:
                model = client() if enabled.get() else None
                saved=self.model_repository.save(fields['base_url'].get(),fields['model'].get(),fields['api_key'].get(),enabled.get(),remember.get())
                self.model_settings = {'base_url':saved.base_url,'model':saved.model,'remember_key':saved.remember_key,'enabled':saved.enabled}
                self.model_key = saved.api_key
                self.model_notice = ''
                self.model_client = model
                self.show(self.page); window.destroy()
            except Exception as exc: status.config(text=str(exc))
        mailbox=queue.Queue()
        def test():
            try: model=client()
            except Exception as exc: status.config(text=str(exc)); return
            tested_client[0]=model
            test_button.config(state='disabled'); status.config(text='正在测试连接…最长约 60 秒，只发送一条测试消息。')
            def worker():
                try: model.test_connection(); mailbox.put('连接成功，模型返回有效结果。保存并启用后生效。')
                except Exception as exc:
                    mailbox.put(connection_error_message(exc))
            threading.Thread(target=worker,daemon=True).start(); window.poll=window.after(100,poll)
        def poll():
            try: text=mailbox.get_nowait()
            except queue.Empty: window.poll=window.after(100,poll); return
            status.config(text=text); test_button.config(state='normal')
            self.refresh_model_state()
        test_button=self.button(buttons,'测试连接',test)
        self.button(buttons,'保存设置',save)
        def clear():
            try:
                cleared=self.model_repository.clear_key()
                self.model_key='';self.model_client=None;self.model_notice=cleared.notice
                self.model_settings.update(remember_key=False,enabled=False)
                fields['api_key'].delete(0,'end');enabled.set(False);remember.set(False)
                status.config(text=cleared.notice)
                self.show(self.page)
            except OSError:status.config(text='清除失败，请检查本机配置目录是否可写。')
        self.button(buttons,'清除密钥',clear)
        def cleanup(event):
            if event.widget is window and hasattr(window,'poll'): window.after_cancel(window.poll)
        window.bind('<Destroy>',cleanup)

    def refresh_model_state(self):
        state=model_status_text(self.model_client)
        if self.page=='assistant':
            self.tip.config(text=state)
            self.model_status_label.config(text=state)
        elif self.page=='settings':
            self.settings_model_status_label.config(text=state)

    def show_updates(self):
        if self.update_window is not None and self.update_window.winfo_exists():
            self.update_window.lift(); return
        window = tk.Toplevel(self); self.update_window = window
        window.title('关于与更新 · AI理财小助手')
        set_window_geometry(window, 650, 540, minimum=(580,500))
        window.configure(bg=BG); window.transient(self)
        content = tk.Frame(window, bg=BG, padx=px(26), pady=px(22)); content.pack(fill='both', expand=True)
        self.label(content, 'AI理财小助手', 21, bold=True)
        self.label(content, f'当前版本  v{APP_VERSION}  ·  Windows x64', 11, MUTED)
        self.label(content, '手动检查官方 GitHub 正式版本；不会上传账单。', 10, MUTED)
        status = self.label(content, '点击“检查更新”获取最新版本。', 11, GREEN)
        status.config(wraplength=px(560))
        notes = tk.Text(content, wrap='word', height=3, relief='flat', bg='white', fg=INK, padx=px(14), pady=px(12), font=('Microsoft YaHei UI', 10))
        notes.pack(fill='both', expand=True, pady=px(12))
        notes.insert('1.0', '发现新版本后可直接下载安装包，显示下载进度并打开下载目录。\n\n下载后请先退出本程序，再运行安装包。覆盖安装保留账单数据。\n\n如网络无法连接 GitHub，可使用浏览器下载链接。')
        notes.config(state='disabled')
        buttons = tk.Frame(content, bg=BG); buttons.pack(fill='x')
        download = self.button(buttons, '下载最新安装包', lambda: None)
        download.config(state='disabled')
        self.button(buttons, '官方发布页面 ↗', lambda: webbrowser.open(RELEASES_URL))
        diagnostic=UpdateDiagnostics(DATA_DIR)
        def view_diagnostics():
            if diagnostic.path.exists():os.startfile(str(diagnostic.path))
            else:messagebox.showinfo('更新诊断','请先执行一次检查更新或下载。',parent=window)
        self.button(buttons,'查看更新诊断',view_diagnostics)
        mailbox = queue.Queue()
        checking = [False]
        cancel_download = threading.Event()
        download_box = queue.Queue()
        download_busy = [False]
        download_url = [None]
        download_sha=[None]
        progress = Meter(content, mode='determinate', maximum=100)
        progress.pack(fill='x',pady=px(4))
        browser_download = tk.Button(content,text='浏览器下载备用链接 ↗',command=lambda: webbrowser.open(download_url[0] or RELEASES_URL),bg=BG,fg=GREEN,relief='flat')
        browser_download.pack(anchor='w')

        def start_download():
            if download_busy[0] or not download_url[0]: return
            download_busy[0]=True; cancel_download.clear(); download.config(state='disabled'); check.config(state='disabled')
            status.config(text='正在连接 GitHub 下载安装包…'); progress['value']=0
            def report(received,total): download_box.put(('progress',(received,total)))
            def worker():
                try: download_box.put(('done',download_installer(download_url[0],DATA_DIR/'downloads',report,cancel_download.is_set,expected_sha256=download_sha[0],verify_release=True,diagnostic=diagnostic,phase=lambda message:download_box.put(('phase',message)))))
                except Exception as exc:
                    download_box.put(('error',update_error_message(exc)))
            threading.Thread(target=worker,daemon=True).start(); window.download_poll=window.after(100,poll_download)

        def poll_download():
            terminal=None
            while True:
                try: kind,value=download_box.get_nowait()
                except queue.Empty: break
                if kind=='phase':status.config(text=value)
                elif kind=='progress':
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
                    notes.config(state='normal');notes.delete('1.0','end');notes.insert('1.0',f'文件：{path}\n\nSHA256：{sha}\n\n已与官方发布校验值比对。未进行代码签名验证。');notes.config(state='disabled')
                    os.startfile(str(Path(path).parent))
                else: status.config(text=value)
            else: window.download_poll=window.after(100,poll_download)

        def start_check():
            if checking[0]: return
            checking[0] = True; check.config(state='disabled'); download.config(state='disabled')
            download_url[0]=None;download_sha[0]=None
            status.config(text='正在检查 GitHub 最新版本…')
            def worker():
                try: mailbox.put(('ok', check_latest(diagnostic=diagnostic)))
                except Exception as exc: mailbox.put(('error',update_error_message(exc,'检查更新')))
            threading.Thread(target=worker, daemon=True).start()
            window.poll = window.after(100, poll)

        def poll():
            try: kind, result = mailbox.get_nowait()
            except queue.Empty:
                window.poll = window.after(100, poll); return
            checking[0] = False; check.config(state='normal')
            if kind == 'error':
                status.config(text=result)
                return
            status.config(text=version_status(result))
            if result.get('source')=='official_release_page':
                status.config(text=status.cget('text')+'\nGitHub API 受限，已从官方发布页确认，仍可在应用内下载。')
            notes.config(state='normal'); notes.delete('1.0', 'end'); notes.insert('1.0', result['notes']); notes.config(state='disabled')
            if can_download(result):
                download_url[0]=result['installer_url']
                download_sha[0]=result.get('sha256')
                download.config(state='normal', command=start_download)
            elif result['newer']:
                status.config(text=f'发现 v{result["version"]}，请在官方发布页面查看并下载安装包。')

        check = tk.Button(content, text='检查更新', command=start_check, bg='#e0f0e7', fg=GREEN, relief='flat', pady=px(10))
        check.pack(fill='x', pady=px((10, 0)))
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
            self.guide.pack(side='bottom', fill='x', padx=px(16), pady=px(8))
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
        set_window_geometry(pet,260,405)
        picture = Image.open(asset).convert('RGBA'); picture.thumbnail(px((190, 285)), Image.Resampling.LANCZOS)
        pet.photo = ImageTk.PhotoImage(picture)
        character = tk.Label(pet, image=pet.photo, bg=transparent, cursor='fleur'); character.place(relx=.5, y=px(65), anchor='n')
        player=None
        sheet=ROOT/'assets'/'campus_animation.png'
        if sheet.exists():
            player=SpritePlayer(character,sheet,px((240,160)));self.animators.append(player);player.pause(self.animation_paused)
            set_window_geometry(pet,280,285)
        area=work_area(self)
        pet.update_idletasks()
        pet.geometry(f'+{max(area[0],area[2]-pet.winfo_width()-px(24))}+{max(area[1],area[3]-pet.winfo_height()-px(24))}')
        tk.Label(pet, text='小财灵：聪明花，慢慢攒！\n拖动角色移动位置', bg='#e7f3ec', fg=GREEN, padx=px(12), pady=px(9), font=('Microsoft YaHei UI', 10)).place(relx=.5, y=px(8), anchor='n')
        tk.Button(pet, text='关闭', command=pet.destroy, relief='flat', bg='#e7f3ec', fg=MUTED).place(relx=1,x=-px(4),y=px(0),anchor='ne')
        tk.Button(pet, text='打开小助手', command=lambda: (self.deiconify(), self.lift()), relief='flat', bg='#e7f3ec', fg=GREEN, padx=px(16), pady=px(6)).place(relx=.5, y=px(240 if sheet.exists() else 358), anchor='n')
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
            if hasattr(pet,'drag_start') and abs(event.x_root-pet.drag_start[0])+abs(event.y_root-pet.drag_start[1])<px(8):
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
        if self.mode=='demo' and initialize_demo(self.store):self.selected_month=max(self.store.spending_view().date.str[:7])
        self.space_label.config(text='我的账本 · 本机数据' if self.mode == 'personal' else '演示空间 · 合成账单')
        self.search_text='';self.category_filter='全部分类'
        self.save_preferences()
        self.show(self.page)

    def label(self, parent, text, size=10, color=INK, bold=False):
        w = tk.Label(parent, text=text, bg=parent['bg'], fg=color, font=point_font(size,bold), anchor='w', justify='left')
        w.pack(anchor='w', pady=px(4))
        return w

    def button(self,parent,text,command):
        widget=parent
        in_page=False
        while widget is not None:
            if widget is self.main:in_page=True;break
            widget=getattr(widget,'master',None)
        primary=not getattr(self,'primary_claimed',False) if in_page else not getattr(parent.winfo_toplevel(),'primary_claimed',False)
        if in_page and primary:self.primary_claimed=True
        elif primary:parent.winfo_toplevel().primary_claimed=True
        danger=text.startswith('删除')
        bg=BLUE if primary and not danger else 'white';fg='white' if bg==BLUE else '#dc2626' if danger else BLUE
        b=tk.Button(parent,text=text,command=command,bg=bg,fg=fg,activebackground='#e6f4f1',activeforeground=BLUE,relief='flat',borderwidth=px(0),highlightbackground='#e6ece9',highlightthickness=px(1),padx=px(12),pady=px(7),cursor='hand2')
        b.pack(side='left',padx=px((0,8)),pady=px(7))
        return b

    def show(self, page):
        self.primary_claimed=False
        self.animate_character('idle')
        self.ask_generation += 1
        self.page = page
        for key, button in self.nav.items():
            button.config(bg='#e7f3ec' if key == page else 'white', fg=GREEN if key == page else MUTED)
        tips = {'home': ('让我看看钱都去哪啦！先核对收支，再给小愿望留点预算。', '一起查查账 →', 'records'), 'records': ('分类不对？选中那一笔就能改。每个数字都有源记录哦。', '安排一个小目标 →', 'plan'), 'plan': ('灵机一动！换个月预算试试，看看离目标能近多少。', '问问消费情况 →', 'assistant'), 'assistant': ('我来指路，代码算钱。当前为本地规则模式，未连接大模型。', '返回财务概览 →', 'home')}
        tips['budget_page']=('先看预算，再核对对应的账单。','查看概览 →','home')
        tips['settings']=('在这里管理模型、版本和本机数据。','返回概览 →','home')
        tip, action, target = tips[page]
        if page=='assistant':tip=model_status_text(self.model_client)
        self.tip.config(text=tip)
        self.guide_action.config(text=action, command=lambda p=target: self.show(p))
        for child in self.main.winfo_children(): child.destroy()
        self.content_canvas.yview_moveto(0)
        self.frame = self.store.spending_view()
        titles = {'home': ('把生活记好，把小目标存好。', '财务概览  /  每一笔记录，都让计划更清楚一点。'), 'records': ('好好记账，心里有数。', '交易账本  /  导入、核对与修正，每笔都有来源。'), 'plan': ('给下一个愿望，留点预算。', '储蓄规划  /  试试不同的预算，找到舒服的节奏。'), 'assistant': ('关于钱的小疑问，一起理清。', '本地规则模式  /  回答来自已导入账单，不向外发送数据。')}
        titles['budget_page']=('预算有边界，花钱更安心。','设置分类预算，查看超支和目标缺口。')
        titles['settings']=('按你的习惯，设置小助手。','模型、更新、数据和角色，集中管理。')
        header=tk.Frame(self.main,bg=BG);header.pack(fill='x',pady=px((0,12)))
        self.space_picker=ttk.Combobox(header,values=['演示空间','我的账本'],state='readonly',width=12)
        self.space_picker.set('演示空间' if self.mode=='demo' else '我的账本');self.space_picker.pack(side='left',padx=px((0,12)))
        self.space_picker.bind('<<ComboboxSelected>>',lambda _:self.switch_space() if (self.space_picker.get()=='我的账本')!=(self.mode=='personal') else None)
        months=sorted(set(self.frame.attrs.get('source_months',[]))|set(self.frame.date.str[:7])|{date.today().strftime('%Y-%m')},reverse=True)
        if self.selected_month not in ['全部月份',*months]:self.selected_month=months[0]
        if page in ('home','records','budget_page'):
            self.month_picker=ttk.Combobox(header,values=([*months] if page=='budget_page' else ['全部月份',*months]),state='readonly',width=13)
            if page=='budget_page' and self.selected_month=='全部月份':self.selected_month=months[0]
            self.month_picker.set(self.selected_month);self.month_picker.pack(side='left')
            def select_month(_):
                self.selected_month=self.month_picker.get();self.save_preferences();self.show(self.page)
            self.month_picker.bind('<<ComboboxSelected>>',select_month)
            if self.selected_month!='全部月份':self.frame=self.frame[self.frame.date.str.startswith(self.selected_month)].copy()
        self.space_label.config(text='我的账本 · 本机数据' if self.mode=='personal' else '演示空间 · 合成账单')
        tk.Button(header,text='搜索 Ctrl+K',command=self.command_palette,bg='white',fg=MUTED,relief='flat',padx=px(10),pady=px(8)).pack(side='right',padx=px(8))
        tk.Button(header,text='+ 记一笔',command=self.quick_entry,bg='white',fg=BLUE,relief='flat',padx=px(12),pady=px(8)).pack(side='right',padx=px(4))
        short_titles={'home':'财务概览','records':'交易账本','budget_page':'预算与提醒','plan':'储蓄规划','assistant':'小财灵助手','settings':'设置'}
        self.label(self.main,short_titles[page],20,bold=True)
        subtitle = '问题文本发送至你配置的接口；账单查询与金额由本机代码处理。' if page == 'assistant' and self.model_client else titles[page][1]
        self.label(self.main, subtitle, 10, MUTED)
        self.label(self.main,basis_label(self.frame.attrs['spending_basis'])+' · 预算按净支出，消费总额同时保留',10,BLUE)
        if self.store.semantic_backup:
            self.label(self.main,'旧记录的转账类型/分类冲突已按原收支类型修正，金额不变，分类标为待确认；修复前已保存备份。',10,'#ae6450').configure(wraplength=px(760))
            self.button(self.main,'查看修复明细与备份',self.semantic_backups_window)
        if self.mode=='demo' and page=='settings':
            demo_actions=tk.Frame(self.main,bg=BG);demo_actions.pack(fill='x',pady=px((0,6)))
            self.button(demo_actions,'生成演示账单',self.demo_generator_window)
            self.label(demo_actions,'自选月份和金额 · 本地生成合成数据',10,MUTED)
        if page in ('budget_page','plan'):
            summary=refund_status_summary(self.frame.attrs['refund_sources'],self.selected_month if page in ('home','records','budget_page') else None)
            self.refund_status_label=self.label(self.main,refund_status_text(summary)+'（按实际退款月列流水状态）',10,'#ae6450')
            self.refund_status_label.configure(wraplength=px(720))
            actions=tk.Frame(self.main,bg=BG);actions.pack(fill='x')
            self.button(actions,'退款与待复核',self.refund_window)
        if page == 'assistant':
            self.model_status_label=self.label(self.main,model_status_text(self.model_client),10,GREEN)
            self.model_status_label.configure(wraplength=px(720))
        getattr(self, page)()

    def table(self,parent,columns,rows,height=9):
        box=tk.Frame(parent,bg='white');box.pack(fill='both',expand=True,pady=px(10))
        visible=rows[:200]
        if len(rows)>200:self.label(box,f'共 {len(rows)} 笔，当前展示前 200 笔。请进入账本分页查看。',9,MUTED)
        ancestor=parent;in_page=False
        while ancestor is not None:
            if ancestor is self.main:in_page=True;break
            ancestor=getattr(ancestor,'master',None)
        tree=ttk.Treeview(box,columns=list(range(len(columns))),show='headings',height=max(2,len(visible) if in_page else min(height,len(visible))))
        if not in_page:
            scroll=ttk.Scrollbar(box,orient='vertical',command=tree.yview);scroll.pack(side='right',fill='y');tree.configure(yscrollcommand=scroll.set)
        for i,name in enumerate(columns):
            numeric=any(k in name for k in ['金额','收入','支出','结余','预算','剩余','使用率','超支','行号','储蓄','来源数','笔数'])
            tree.heading(i,text=name,command=lambda c=i:self.sort_table(tree,c))
            tree.column(i,width=px(110),minwidth=px(70),anchor='e' if numeric else 'w',stretch=True)
        tree.pack(fill='both',expand=True)
        tree.tag_configure('alternate',background='#f8fbf9')
        for index,row in enumerate(visible):tree.insert('','end',iid=str(index),values=row,tags=('alternate',) if index%2 else ())
        return tree

    def sort_table(self,tree,column):
        reverse=getattr(tree,'sort_order',{}).get(column,False)
        def value(item):
            text=tree.set(item,column)
            try:return (0,float(str(text).replace(',','').replace('%','').replace('¥','')))
            except ValueError:return (1,str(text))
        for index,item in enumerate(sorted(tree.get_children(),key=value,reverse=reverse)):tree.move(item,'',index)
        tree.sort_order={column:not reverse}

    def row_index(self,tree,item):
        return int(item) if str(item).isdigit() else tree.index(item)

    def home(self):
        f = self.frame
        totals=spending_totals(f);self.home_totals=totals
        income=totals['income_cents'];expense=totals['net_cents']
        band = tk.Frame(self.main, bg=BG); band.pack(fill='x', pady=px(20))
        for title, value, color in [('期间收入', income, INK), ('净支出', expense, INK), ('期间结余', income-expense, GREEN)]:
            card = tk.Frame(band, bg='#e8f4ec' if title == '期间结余' else 'white', padx=px(20), pady=px(20)); card.pack(side='left', fill='x', expand=True, padx=px((0, 12)))
            self.label(card, title, 10, MUTED)
            self.label(card, '¥ '+yuan(value), 23, color, True)
        self.label(self.main,f'消费总额 ¥{yuan(totals["gross_cents"])} − 已确认抵扣 ¥{yuan(totals["refund_cents"])} = 净支出 ¥{yuan(expense)}',11,BLUE)
        self.home_insights()
        if f.empty:
            empty=tk.Frame(self.main,bg='white',padx=px(20),pady=px(12));empty.pack(fill='x')
            self.label(empty,'当前口径本期无计入明细；已导入退款可能待处理、待复核或归入原消费月。' if self.selected_month in f.attrs.get('source_months',[]) else '这个时间范围还没有账单。导入记录后即可分析。',12).configure(wraplength=px(800))
            self.button(empty,'导入账单',self.import_file)
            self.button(empty,'查看全部月份',self.select_all_months)
        self.home_budget_alerts()
        self.draw_trend()
        monthly, categories = overview(f)
        self.label(self.main, '净支出结构 / '+self.selected_month+' · '+basis_label(f.attrs['spending_basis']), 14, bold=True)
        canvas = tk.Canvas(self.main, height=px(180), bg='white', highlightthickness=px(0))
        canvas.pack(fill='x', pady=px(10))
        top = categories.head(5)
        maxval = max(top['支出'].abs().max(), 1) if len(top) else 1
        def draw_categories(event=None):
            canvas.delete('all')
            width=max(500,logical(canvas.winfo_width()));bar_end=width-245
            for i, (_, row) in enumerate(top.iterrows()):
                y=24+i*32
                canvas.create_text(px(20),px(y),text=row['分类'],anchor='w',fill=INK,font=point_font())
                zero=(110+bar_end)/2;span=(bar_end-110)/2
                canvas.create_line(px(zero),px(y-12),px(zero),px(y+12),fill='#b9c6c0')
                canvas.create_rectangle(px(zero),px(y-6),px(zero+float(row['支出'])/maxval*span),px(y+6),fill=BLUE if row['支出']>=0 else '#b5816b',outline='')
                share='占比不适用' if __import__('pandas').isna(row['占比']) else f"{row['占比']}%"
                canvas.create_text(px(bar_end+20),px(y),text=f"¥ {row['支出']:,.2f} · {share}",anchor='w',fill=MUTED,font=point_font())
                hit=canvas.create_rectangle(px(5),px(y-13),px(width-5),px(y+13),fill='',outline='')
                canvas.tag_bind(hit,'<Button-1>',lambda event,c=row['分类']:self.open_category(c))
                canvas.tag_bind(hit,'<Enter>',lambda event:canvas.config(cursor='hand2'))
        canvas.bind('<Configure>',draw_categories);draw_categories()
        self.label(self.main, '月度收支', 14, bold=True)
        self.table(self.main, ['月份', '收入 / 元', '支出 / 元', '结余 / 元'], [[r['月份'], f"{r['收入']:,.2f}", f"{r['支出']:,.2f}", f"{r['结余']:,.2f}"] for _, r in monthly.iterrows()], 4)
        self.label(self.main, f'{"演示空间" if self.mode == "demo" else "个人空间"} · {count_records(f)} 笔记录 · 退款调整不增加消费笔数 · 结余不等于账户余额', 9, MUTED)

    def records(self):
        self.source_frame=self.store.source_transactions()
        if self.selected_month!='全部月份':self.source_frame=self.source_frame[self.source_frame.date.str.startswith(self.selected_month)].copy()
        bar = tk.Frame(self.main, bg=BG); bar.pack(fill='x')
        self.button(bar,'导入账单',self.import_file)
        self.search_var=tk.StringVar(value=self.search_text)
        search=ttk.Entry(bar,textvariable=self.search_var,width=20);search.pack(side='left',padx=px((0,8)))
        search.bind('<Return>',lambda _:self.reset_record_filter())
        self.button(bar,'搜索',self.reset_record_filter)
        filters=tk.Frame(self.main,bg=BG)
        def toggle_filters():
            if filters.winfo_manager():filters.pack_forget()
            else:filters.pack(fill='x',after=bar,pady=px(6))
        self.button(bar,'筛选',toggle_filters)
        more=tk.Menubutton(bar,text='更多 ▾',bg='white',fg=BLUE,relief='flat',padx=px(12),pady=px(7))
        more.pack(side='left',padx=px(4));menu=tk.Menu(more,tearoff=False);more.configure(menu=menu)
        for title,command in [('查看源记录',self.source),('跨来源对账',self.reconciliation_window),('退款与待复核',self.refund_window),('导入批次',self.import_history_window),('历史分类待修正',self.category_conflicts_window),('导出净额明细',self.export_spending),('撤销上次批量',self.undo_batch),('AI 分类建议',self.suggest_categories_ui)]:
            menu.add_command(label=title,command=command,state='disabled' if title=='AI 分类建议' and not self.model_client else 'normal')
        self.filter_picker=ttk.Combobox(filters,values=['全部分类','待确认分类',*CATEGORIES],state='readonly',width=12)
        self.filter_picker.set(self.category_filter);self.filter_picker.pack(side='left')
        self.selection_bar=tk.Frame(self.main,bg='#e6f4f1')
        self.selection_label=self.label(self.selection_bar,'已选 0 笔',10,BLUE)
        self.selection_label.pack_forget();self.selection_label.pack(side='left',padx=px(8))
        self.category=ttk.Combobox(self.selection_bar,values=category_options('支出'),state='readonly',width=8)
        self.category.set('餐饮');self.category.pack(side='left',padx=px(4))
        self.button(self.selection_bar,'改分类',self.correct)
        self.button(self.selection_bar,'编辑',self.edit_selected)
        self.button(self.selection_bar,'同商户应用',lambda:self.batch_correct(True))
        def clear_selection():
            chosen=self.tree.selection()
            if chosen:self.tree.selection_remove(*chosen)
        self.button(self.selection_bar,'取消选择',clear_selection)
        self.result_count=tk.Label(self.main,bg=BG,fg=MUTED,justify='left',anchor='w',wraplength=px(720));self.result_count.pack(fill='x')
        self.result_count.bind('<Configure>',lambda event:self.result_count.configure(wraplength=max(px(240),event.width-px(8))))
        self.tree=self.table(self.main,['日期','商户','类型','金额 / 元','分类','分类来源','统计口径'],[],12)
        self.tree.bind('<Double-1>',lambda _:self.edit_selected())
        self.tree.bind('<<TreeviewSelect>>',self.update_category_choices)
        self.search_var.trace_add('write',lambda *_:self.reset_record_filter())
        self.filter_picker.bind('<<ComboboxSelected>>',lambda _:self.reset_record_filter())
        self.record_page=0
        paging=tk.Frame(self.main,bg=BG);paging.pack(fill='x')
        self.button(paging,'上一页',lambda:self.change_record_page(-1))
        self.button(paging,'下一页',lambda:self.change_record_page(1))
        self.page_info=tk.Label(paging,bg=BG,fg=MUTED);self.page_info.pack(side='left')
        self.refresh_records()
        self.label(self.main,'搜索商户 / 描述 / 交易编号；双击交易可编辑。修改前的内容保存在审计记录中。',9,MUTED)

    def select_all_months(self):
        self.selected_month='全部月份';self.save_preferences();self.show(self.page)

    def refresh_records(self):
        self.search_text=self.search_var.get().strip();self.category_filter=self.filter_picker.get()
        frame=self.source_frame
        amount_match=__import__('re').fullmatch(r'([<>]=?)(\d+(?:\.\d{1,2})?)',self.search_text)
        if amount_match:
            op,value=amount_match.groups()
            try:cents=money_to_cents(value,allow_zero=True)
            except ValueError:cents=10**12+1
            frame=frame[{'>' :frame.amount_cents>cents,'>=':frame.amount_cents>=cents,'<':frame.amount_cents<cents,'<=':frame.amount_cents<=cents}[op]]
        elif self.search_text:
            mask=frame.merchant.str.contains(self.search_text,case=False,regex=False)|frame.description.str.contains(self.search_text,case=False,regex=False)|frame.external_id.str.contains(self.search_text,case=False,regex=False)
            frame=frame[mask]
        if self.category_filter=='待确认分类':frame=frame[frame.category_source.isin(['待确认','模型建议（需核对）'])]
        elif self.category_filter!='全部分类':frame=frame[frame.category==self.category_filter]
        filtered=frame.sort_values('date',ascending=False).to_dict('records')
        total=len(filtered);pages=max(1,(total+199)//200)
        self.record_page=min(getattr(self,'record_page',0),pages-1)
        self.record_rows=filtered[self.record_page*200:(self.record_page+1)*200]
        self.tree.configure(height=max(2,len(self.record_rows)))
        self.page_info.config(text=f'第 {self.record_page+1} / {pages} 页 · 每页 200 笔')
        for item in self.tree.get_children():self.tree.delete(item)
        for i,r in enumerate(self.record_rows):self.tree.insert('','end',iid=str(i),values=[r['date'][:10],r['merchant'],r['kind'],yuan(r['amount_cents']),r['category'],r['category_source'],r['counting_note']],tags=('alternate',) if i%2 else ())
        net=self.store.spending_view()
        if self.selected_month!='全部月份':net=net[net.date.str.startswith(self.selected_month)].copy()
        totals=spending_totals(net);self.record_totals=totals
        self.result_count.config(text=f'{total} 笔来源 · 本期净支出 ¥{yuan(totals["net_cents"])} · 消费总额 ¥{yuan(totals["gross_cents"])}（摘要不随源记录搜索改变）')
        self.update_category_choices()

    def update_category_choices(self,event=None):
        selected=[self.record_rows[self.row_index(self.tree,item)] for item in self.tree.selection()]
        self.selection_label.config(text=f'已选 {len(selected)} 笔')
        if selected:self.selection_bar.pack(fill='x',before=self.result_count,pady=px(4))
        else:self.selection_bar.pack_forget()
        kinds={r['kind'] for r in selected}
        choices=category_options(next(iter(kinds))) if len(kinds)==1 else [] if kinds else category_options('支出')
        self.category.configure(values=choices)
        if self.category.get() not in choices:self.category.set(choices[0] if choices else '')

    def change_record_page(self,delta):
        self.record_page=max(0,self.record_page+delta);self.refresh_records()

    def reset_record_filter(self):
        self.record_page=0
        self.refresh_records()

    def edit_selected(self):
        record=self.selected()
        if not record:return
        store=self.store
        window=tk.Toplevel(self);window.title('编辑交易');set_window_geometry(window, 520, 650);window.configure(bg=BG)
        actions=tk.Frame(window,bg=BG);actions.pack(side='bottom',fill='x',padx=px(24),pady=px(4))
        box=scrollable_form(window)
        self.label(box,'修正这笔交易',18,bold=True)
        legacy_conflict=record['category'] not in category_options(record['kind'])
        if legacy_conflict:
            self.label(box,f'原记录为“{record["kind"]} / {record["category"]}”，分类不在该类型范围内。请选择正确分类；保存前不会修改记录。',10,'#ae6450').configure(wraplength=px(455))
        fields={}
        for title,key,value in [('日期时间 YYYY-MM-DD HH:MM:SS','date',record['date']),('商户','merchant',record['merchant']),('描述','description',record['description']),('金额 / 元','amount_cents',f"{record['amount_cents']/100:.2f}"),('收支类型','kind',record['kind']),('分类','category',record['category'])]:
            self.label(box,title,10,MUTED)
            if key in ('kind','category'):
                entry=ttk.Combobox(box,state='readonly',values=['收入','支出','转账'] if key=='kind' else category_options(record['kind']));entry.set(value)
            else:entry=ttk.Entry(box);entry.insert(0,value)
            entry.pack(fill='x',pady=px((0,7)));fields[key]=entry
        remember=tk.BooleanVar(value=False)
        tk.Checkbutton(box,text='记住此商户分类，用于以后新导入的同类收支',variable=remember,bg=BG).pack(anchor='w')
        def match_category(_=None):
            choices=category_options(fields['kind'].get());fields['category'].configure(values=choices)
            if fields['category'].get() not in choices:fields['category'].set(choices[0])
        fields['kind'].bind('<<ComboboxSelected>>',match_category)
        if legacy_conflict:fields['category'].set('')
        else:match_category()
        status=self.label(box,'收入、支出、转账各有对应分类。修改类型或分类后，关联将待复核；旧错分类仅在你保存后更正。',9,MUTED);status.configure(wraplength=px(455))
        def save():
            try:
                if not fields['category'].get().strip():raise ValueError('请先选择与收支类型对应的正确分类，再保存修改。')
                values={k:e.get().strip() for k,e in fields.items()};values['amount_cents']=money_to_cents(values['amount_cents'])
                store.edit_transaction(record['id'],values,learn=remember.get())
                window.destroy();self.show('records');self.animate_character('success')
            except Exception as exc:status.config(text=str(exc),fg='#b45f50')
        def remove():
            text=f"{record['date']}\n{record['merchant']} · {record['kind']} · ¥{yuan(record['amount_cents'])} · {record['category']}\n\n删除后不再进入概览、预算和问答统计；原始来源和审计保留。相关对账/退款关系会转为待复核。是否删除这笔交易？"
            if not messagebox.askyesno('确认删除这笔交易',text,parent=window):return
            try:
                store.soft_delete_transaction(record['id'],confirmed=True)
            except Exception as exc:status.config(text=str(exc),fg='#b45f50');return
            window.destroy();self.show('records')
        self.button(actions,'删除这笔交易',remove)
        saving=self.button(actions,'保存修改',save);saving.pack_configure(side='right');saving.configure(bg=BLUE,fg='white')
        self.button(actions,'取消',window.destroy).pack_configure(side='right')

    def locate_transaction(self,identifier,*,expected_path=None):
        if expected_path and Path(expected_path).resolve()!=Path(self.store.path).resolve():
            messagebox.showinfo('空间已切换','请先回到打开待修正列表时的空间，再定位或编辑。');return False
        self.selected_month='全部月份';self.search_text='';self.category_filter='全部分类';self.show('records')
        ordered=self.source_frame.sort_values('date',ascending=False).id.tolist()
        if identifier not in ordered:
            messagebox.showinfo('记录已变化','该来源已删除或不在当前空间，请刷新待修正列表。');return False
        self.record_page=ordered.index(identifier)//200;self.refresh_records()
        index=next(i for i,r in enumerate(self.record_rows) if r['id']==identifier);item=str(index)
        self.tree.selection_set(item);self.tree.focus(item);self.tree.see(item);self.update_idletasks()
        bounds=self.tree.bbox(item);region=self.content_canvas.bbox('all')
        if bounds and region:
            position=self.tree.winfo_rooty()-self.main.winfo_rooty()+bounds[1]-px(60)
            self.content_canvas.yview_moveto(max(0,position/max(1,region[3]-region[1])))
        return True

    def selected(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo('选择记录', '请先选择一条交易。'); return None
        return self.record_rows[self.row_index(self.tree,sel[0])]

    def source(self):
        r = self.selected()
        if r:
            source=self.store.transaction_source(r['id'])
            window=tk.Toplevel(self);window.title('源记录与导入证据');set_window_geometry(window,750,580);window.configure(bg=BG)
            text=tk.Text(window,wrap='word',font=point_font());text.pack(fill='both',expand=True,padx=px(14),pady=px(14))
            evidence={k:source.get(k) for k in ('external_id','source_file','source_row','date','description','category_source','account_id','status','source_document_id','raw_row_id','sha256','parser_version','parse_status')}
            evidence['原始必要字段']=json.loads(source['raw_json']) if source.get('raw_json') else '旧数据：没有原文件，不伪造原始行或哈希'
            text.insert('1.0',json.dumps(evidence,ensure_ascii=False,indent=2));text.config(state='disabled')

    def correct(self):
        self.batch_correct()

    def import_file(self):
        return self.select_import()

    def plan(self):
        form = tk.Frame(self.main, bg='white', padx=px(16), pady=px(8)); form.pack(fill='x', pady=px(10))
        for col in range(3):form.columnconfigure(col,weight=1,uniform='form')
        self.inputs = {}
        today = date.today()
        self.plan_start=today.isoformat()
        index = today.year*12+today.month-1+6
        y, m = divmod(index, 12)
        from calendar import monthrange
        deadline = date(y, m+1, min(today.day, monthrange(y, m+1)[1]))
        values = [('目标金额 / 元', '6000'), ('已有储蓄 / 元', '0'), ('每月收入 / 元', '5000'), ('方案 A 月预算 / 元', '4200'), ('方案 B 月预算 / 元', '3800'), ('截止日期 YYYY-MM-DD', deadline.isoformat())]
        for i, (name, value) in enumerate(values):
            r, c = divmod(i, 3)
            field = tk.Frame(form, bg='white'); field.grid(row=r, column=c, padx=px(10), pady=px(5), sticky='ew')
            self.label(field, name, 10, MUTED)
            entry = ttk.Entry(field, width=16); entry.insert(0, value); entry.pack(fill='x')
            self.inputs[name] = entry
        buttons = tk.Frame(self.main, bg=BG); buttons.pack(fill='x')
        self.button(buttons, '比较两个方案', self.calculate)
        self.button(buttons, '保存方案 B', self.save_plan)
        self.button(buttons,'保存方案 A',lambda:self.save_plan('A'))
        self.button(buttons,'用近三个月账单估算',self.estimate_plan)
        self.button(buttons, '新建目标', lambda:self.show('plan'))
        self.estimate_note=self.label(self.main,'估算仅使用最近三个完整月份，点击按钮查看月份依据。',9,MUTED)
        self.estimate_note.config(wraplength=px(850))
        self.goal_status=self.label(self.main,'新目标 · 保存方案 B 后可在下方查看',9,MUTED)
        self.plan_result = tk.Label(self.main, text='', bg='white', fg=INK, justify='left', anchor='nw', padx=px(24), pady=px(22), font=('Microsoft YaHei UI', 11), wraplength=px(780))
        self.plan_cards=tk.Frame(self.main,bg=BG);self.plan_cards.pack(fill='x',pady=px(8))
        self.plan_card_labels=[]
        for name in ('A','B'):
            card=tk.Frame(self.plan_cards,bg='white',padx=px(16),pady=px(12));card.pack(side='left',fill='both',expand=True,padx=px((0,10)))
            label=tk.Label(card,bg='white',fg=INK,justify='left',anchor='nw',font=('Microsoft YaHei UI', 10),wraplength=px(340))
            label.pack(fill='both',expand=True);self.plan_card_labels.append(label)
            card.bind('<Configure>',lambda event,widget=label:widget.configure(wraplength=max(px(180),event.width-px(36))))
        self.editing_goal_id=None
        history_bar=tk.Frame(self.main,bg=BG);history_bar.pack(fill='x')
        self.label(history_bar,'已保存目标 / 当前空间',12,bold=True)
        self.button(history_bar,'载入选中目标',self.load_goal)
        self.button(history_bar,'删除选中目标',self.remove_goal)
        self.button(history_bar,'实际存入与进度',self.contribution_window)
        self.goal_rows=self.store.goals().to_dict('records')
        self.goal_tree=self.table(self.main,['目标','目标金额','已有储蓄','截止日期','月预算'],[[r['name'],yuan(r['target_cents']),yuan(r['saved_cents']),r['deadline'],yuan(r['budget_cents'])] for r in self.goal_rows],3)
        self.goal_tree.bind('<Double-1>',lambda _:self.load_goal())
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
                p = plan_savings(target, saved, self.plan_start, deadline, income, budget)
                need = yuan(p['required_monthly']) if p['required_monthly'] is not None else '无完整存入日'
                lines.append(f"方案 {name}  ·  {'预计可达成' if p['feasible'] else '存在目标缺口'}\n月预算 ¥ {yuan(budget)}   /   月可存 ¥ {yuan(p['monthly_available'])}\n{p['months']} 次存入后预计 ¥ {yuan(p['projected'])}，距离目标差 ¥ {yuan(p['deficit'])}\n每月需存：{need}" + ('\n注意：收入小于预算，当前存在负现金流。' if p['negative_cashflow'] else ''))
            self.plan_result.config(text='\n\n'.join(lines))
            for label,text in zip(self.plan_card_labels,lines):label.config(text=text)
            self.plan_card_labels[1].config(fg=GREEN if '预计可达成' in lines[1] else INK)
            return True
        except Exception as exc:
            self.plan_result.config(text='请检查输入：'+str(exc))
            for label in self.plan_card_labels:label.config(text='请检查输入：'+str(exc),fg='#b45f50')
            return False

    def save_plan(self,scenario='B'):
        if not self.calculate(): return
        (target, saved, income, a, b), deadline = self.plan_values()
        if scenario=='A':b=a
        name=f'储蓄目标 {scenario}'
        if self.editing_goal_id:
            current=self.store.goals();matched=current[current.id==self.editing_goal_id]
            if not matched.empty:name=matched.iloc[0]['name']
        values=dict(name=name,target_cents=target,saved_cents=saved,start_date=self.plan_start,deadline=deadline,income_cents=income,budget_cents=b)
        if self.editing_goal_id:self.store.update_goal(self.editing_goal_id,values)
        else:self.store.save_goal(values)
        self.show('plan')
        messagebox.showinfo('计划已保存',f'方案 {scenario} 已保存至当前空间。实际存入请在历史目标中单独记录。')
        self.animate_character('success')

    def chosen_goal(self):
        selection=self.goal_tree.selection()
        if not selection:messagebox.showinfo('选择目标','请先选择一个已保存目标。');return None
        return self.goal_rows[self.row_index(self.goal_tree,selection[0])]

    def load_goal(self):
        goal=self.chosen_goal()
        if not goal:return
        self.editing_goal_id=goal['id']
        self.plan_start=goal['start_date']
        self.goal_status.config(text=f'正在编辑目标 #{goal["id"]} · 开始日期 {self.plan_start} · 保存方案 B 将更新此目标')
        values=[f"{goal[k]/100:.2f}" for k in ['target_cents','saved_cents','income_cents','budget_cents','budget_cents']]+[goal['deadline']]
        for entry,value in zip(self.inputs.values(),values):entry.delete(0,'end');entry.insert(0,value)
        self.calculate()

    def remove_goal(self):
        goal=self.chosen_goal()
        if goal and messagebox.askyesno('删除目标','删除该计划？账单记录不会被删除。'):
            self.store.delete_goal(goal['id']);self.show('plan')

    def save_preferences(self):
        self.preferences.update(space=self.mode,month=self.selected_month)
        (DATA_DIR/'preferences.json').write_text(json.dumps(self.preferences),encoding='utf-8')

    def settings(self):
        self.settings_model_status_label=self.label(self.main,model_status_text(self.model_client),10,GREEN)
        self.settings_model_status_label.configure(wraplength=px(720))
        if self.model_notice:self.label(self.main,self.model_notice,10,'#ae6450').configure(wraplength=px(760))
        items=[('大模型接口','可记住本机加密密钥及启用状态，重启和更新后自动恢复。',self.show_model_settings,'配置模型'),
               ('退款与统计口径','管理退款、待复核及全局口径；预算使用净支出。',self.refund_window,'退款与统计口径'),
               ('版本与更新',f'当前 v{APP_VERSION} · 检查新版与下载安装包。',self.show_updates,'检查更新'),
               ('账户与支付绑定','声明钱包与银行卡账户，建立带有效期的支付绑定；不会自动合并交易。',self.account_window,'管理账户与绑定'),
               ('备份与恢复','当前空间一致备份；恢复前预览并再次快照，保留旧数据身份。',self.backup_window,'备份与恢复'),
               ('修复明细与自动备份','查看当前空间的分类修复历史、备份位置和大小；清理前确认，不自动删除。',self.semantic_backups_window,'查看修复与备份'),
               ('历史分类待修正','查看收入/支出等旧错分类，定位原交易后由你明确修正，不自动改金额或类型。',self.category_conflicts_window,'历史分类待修正'),
               ('本机数据','账单、原始必要字段和审计留在本机；演示与个人空间独立。',lambda:os.startfile(str(DATA_DIR)),'打开数据目录'),
               ('角色与动画','点击角色挥手，可暂停动画或打开桌面小助手。',self.show_pet,'打开桌面小助手')]
        items.append(('分类记忆',f'当前空间已记住 {len(self.store.merchant_rules())} 条商户规则，可查看并删除。',self.learned_rules_window,'管理分类记忆'))
        items.append(('六行对账示例','独立合成空间，体验确认后120→60元、拆分后恢复120元。退款另列待处理。',self.six_row_demo,'打开六行示例'))
        for title,description,action,caption in items:
            card=tk.Frame(self.main,bg='white',padx=px(18),pady=px(8));card.pack(fill='x',pady=px(6))
            self.label(card,title,13,bold=True);self.label(card,description,10,MUTED);self.button(card,caption,action)

    def assistant(self):
        self.conversation_page()

    def ask(self,question=None):
        self.conversation_ask(question)

    def qa_budgets(self,frame):
        months=set(frame.date.str[:7])|{date.today().strftime('%Y-%m')}
        return {m:self.store.budgets(m) for m in months}

    def render_answer(self, result):
        self.animate_character('success' if result.get('status')=='ok' else 'idle')
        self.answer.config(text=result['answer'])
        for child in self.evidence_box.winfo_children(): child.destroy()
        self.label(self.evidence_box, f"来源依据 · {len(result['evidence'])} 笔 · {result['parser']}", 10, MUTED)
        self.table(self.evidence_box, ['日期', '商户', '金额 / 元', '类别', '源文件', '行号'], [[r['date'][:10], r['merchant'], yuan(r['amount_cents']), r['category'], r['source_file'], r['source_row']] for r in result['evidence']], 6)


if __name__ == '__main__':
    if '--self-test-m2' in sys.argv or '--self-test-m2-migration' in sys.argv:
        # Explicit engineering mode; never opens a default personal data directory.
        if not os.environ.get('FINANCE_DESKTOP_DATA_DIR'):sys.exit('M2 acceptance requires FINANCE_DESKTOP_DATA_DIR')
        try:
            if '--self-test-m2-migration' in sys.argv:
                from finance.m2_release_acceptance import verify_m2_migration
                verify_m2_migration(DATA_DIR)
            else:
                from scripts.verify_m2_4_ui import verify
                verify(DATA_DIR/'m2-ui',desktop_class=Desktop,data_dir=DATA_DIR)
        except Exception:
            import traceback
            DATA_DIR.mkdir(parents=True,exist_ok=True)
            (DATA_DIR/'m2-acceptance-error.log').write_text(traceback.format_exc(),encoding='utf-8')
            sys.exit(1)
        sys.exit(0)
    if '--self-test-round8' in sys.argv:
        if not os.environ.get('FINANCE_DESKTOP_DATA_DIR'):raise ValueError('自检须指定隔离数据目录 FINANCE_DESKTOP_DATA_DIR')
        from finance.round8_acceptance import verify_round8
        verify_round8(DATA_DIR)
        sys.exit(0)
    if '--self-test-round7' in sys.argv:
        if not os.environ.get('FINANCE_DESKTOP_DATA_DIR'):raise ValueError('自检须指定隔离数据目录 FINANCE_DESKTOP_DATA_DIR')
        from finance.round7_acceptance import verify_round7
        verify_round7(DATA_DIR)
        sys.exit(0)
    if '--self-test-round6' in sys.argv:
        if not os.environ.get('FINANCE_DESKTOP_DATA_DIR'):raise ValueError('自检须指定隔离数据目录 FINANCE_DESKTOP_DATA_DIR')
        from finance.round6_acceptance import verify_round6
        verify_round6(DATA_DIR)
        sys.exit(0)
    if '--self-test-round5' in sys.argv:
        if not os.environ.get('FINANCE_DESKTOP_DATA_DIR'):raise ValueError('自检须指定隔离数据目录 FINANCE_DESKTOP_DATA_DIR')
        from finance.round5_acceptance import verify_round5
        verify_round5(DATA_DIR)
        sys.exit(0)
    if '--self-test-m1a' in sys.argv:
        if not os.environ.get('FINANCE_DESKTOP_DATA_DIR'):raise ValueError('自检须指定隔离数据目录 FINANCE_DESKTOP_DATA_DIR')
        from finance.m1a_demo import verify_six
        verify_six(DATA_DIR)
        sys.exit(0)
    if '--diagnose-updates' in sys.argv:
        from finance.updates import diagnose_updates
        result=diagnose_updates(DATA_DIR,download='--download-latest' in sys.argv)
        sys.exit(0 if result['check_ok'] and ('--download-latest' not in sys.argv or result['download_ok']) else 1)
    import traceback
    try:
        app = Desktop()
        if '--self-test' in sys.argv:
            app.update()
            import ssl
            tls_context=ssl.create_default_context()
            assert tls_context.check_hostname and tls_context.verify_mode==ssl.CERT_REQUIRED
            restored=app.model_repository.load()
            assert (app.model_client is not None)==restored.enabled
            assert app.model_key==restored.api_key
            assert len(app.store.spending_view()) >= 100
            for page in ['records','budget_page','plan','assistant','settings','home']:
                app.show(page); app.update()
            app.switch_space(); app.update()
            assert app.mode == 'personal'
            app.switch_space(); app.update()
            app.show('assistant'); app.ask('累计结余是多少？')
            # A restored model enables the async UI path even for local queries.
            import time
            deadline=time.monotonic()+5
            while getattr(app,'chat_busy',False) and time.monotonic()<deadline:
                app.update();time.sleep(.01)
            assert not getattr(app,'chat_busy',False)
            assert '结余为' in app.answer.cget('text')
            from finance.demo_generator import verify_generator
            generator_report=verify_generator(DATA_DIR)
            (DATA_DIR/'demo-generator-self-test.json').write_text(json.dumps(generator_report,ensure_ascii=False,indent=2),encoding='utf-8')
            app.show('home');app.demo_generator_window();app.update()
            assert app.demo_generator_dialog.winfo_exists()
            app.demo_generator_dialog.destroy()
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
