"""Small desktop entry points for accounts, import history and manual reconciliation."""
import json
import queue
import threading
import os
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import ttk,filedialog,messagebox
from .ui_scale import px,set_window_geometry
from .ui_theme import BG,MUTED,PRIMARY,DANGER,scrollable_form
from .analytics import yuan
from .migrations import inspect_database


class M1Features:
    def show_calculation_trace(self,trace):
        window=tk.Toplevel(self);window.title('计算公式与完整证据快照');set_window_geometry(window,820,600);window.configure(bg=BG)
        self.label(window,'交易、关联决定及预算/目标参数分别标记；这是计算时的快照。',10,MUTED)
        text=tk.Text(window,wrap='word',font=('Microsoft YaHei UI',10))
        scroll=ttk.Scrollbar(window,command=text.yview);scroll.pack(side='right',fill='y')
        text.configure(yscrollcommand=scroll.set);text.pack(fill='both',expand=True)
        text.insert('1.0',json.dumps(trace,ensure_ascii=False,indent=2));text.configure(state='disabled')

    def account_window(self,store=None):
        store=store or self.store
        window=tk.Toplevel(self);window.title('账户与支付绑定');set_window_geometry(window,850,740);window.configure(bg=BG)
        box=scrollable_form(window,padx=18,pady=12)
        self.label(box,'声明账户与支付绑定',18,bold=True)
        self.label(box,'仅记录名称、平台和末四位，不连接银行。相同银行的不同卡请分别建立账户。',10,MUTED).configure(wraplength=px(770))
        self.label(box,'1. 添加账户',12,bold=True)
        row=tk.Frame(box,bg=BG);row.pack(fill='x')
        label=ttk.Entry(row,width=18);label.pack(side='left',padx=px(4));label.insert(0,'我的微信')
        channel=ttk.Combobox(row,values=['微信','银行','支付宝','其他'],width=8);channel.set('微信');channel.pack(side='left',padx=px(4))
        kind=ttk.Combobox(row,values=['钱包','储蓄卡','信用卡'],state='readonly',width=9);kind.set('钱包');kind.pack(side='left',padx=px(4))
        last4=ttk.Entry(row,width=8);last4.pack(side='left',padx=px(4))
        self.label(box,'依次填写：账户名称 / 平台 / 类型 / 卡号末四位（可留空）',10,MUTED)
        state={'accounts':[],'links':[]};choices={}
        account_holder=tk.Frame(box,bg=BG);account_holder.pack(fill='x')
        status=self.label(box,'自动关联关闭；所有候选都由你确认。',10,PRIMARY)
        self.label(box,'2. 声明支付绑定及有效期',12,bold=True)
        bind=tk.Frame(box,bg=BG);bind.pack(fill='x')
        wallet=ttk.Combobox(bind,state='readonly',width=23);wallet.pack(side='left',padx=px(4))
        bank=ttk.Combobox(bind,state='readonly',width=23);bank.pack(side='left',padx=px(4))
        dates=tk.Frame(box,bg=BG);dates.pack(fill='x')
        self.label(dates,'开始 / 结束（YYYY-MM-DD，结束可空）',10,MUTED)
        start=ttk.Entry(dates,width=14);start.insert(0,'2026-01-01');start.pack(side='left',padx=px(4))
        end=ttk.Entry(dates,width=14);end.pack(side='left',padx=px(4))
        actions=tk.Frame(box,bg=BG);actions.pack(fill='x')
        link_holder=tk.Frame(box,bg=BG);link_holder.pack(fill='x')
        def refresh():
            state['accounts']=store.accounts();choices.clear()
            choices.update({r['label']+' · '+r['channel']+' · '+r['id'][-6:]:r for r in state['accounts']})
            wallet.configure(values=[k for k,r in choices.items() if r['type']=='wallet'])
            bank.configure(values=[k for k,r in choices.items() if r['type']!='wallet'])
            for holder in (account_holder,link_holder):
                for c in holder.winfo_children():c.destroy()
            self.table(account_holder,['账户名称','平台','类型','末四位'],[[r['label'],r['channel'],{'wallet':'钱包','debit':'储蓄卡','credit':'信用卡'}[r['type']],r['last4']] for r in state['accounts']],4)
            state['links']=store.funding_links();names={r['id']:r['label'] for r in state['accounts']}
            state['tree']=self.table(link_holder,['钱包','银行卡','生效','结束'],[[names[r['wallet_account_id']],names[r['bank_account_id']],r['active_from'],r['active_to'] or '长期'] for r in state['links']],4)
        def add():
            try:store.add_account(channel.get(),{'钱包':'wallet','储蓄卡':'debit','信用卡':'credit'}[kind.get()],label.get(),last4.get().strip());refresh();status.config(text='账户已保存。')
            except (ValueError,KeyError) as exc:status.config(text=str(exc),fg=DANGER)
        def declare():
            try:
                store.add_funding_link(choices[wallet.get()]['id'],choices[bank.get()]['id'],start.get().strip(),end.get().strip() or None)
                refresh();status.config(text='支付绑定已保存，仍需人工确认对账。')
            except (ValueError,KeyError) as exc:status.config(text=str(exc) if isinstance(exc,ValueError) else '请先选择钱包和银行卡',fg=DANGER)
        def finish():
            selected=state['tree'].selection()
            if not selected:return
            try:store.end_funding_link(state['links'][self.row_index(state['tree'],selected[0])]['id'],end.get().strip());refresh()
            except ValueError as exc:status.config(text=str(exc),fg=DANGER)
        self.button(row,'添加账户',add);self.button(actions,'保存支付绑定',declare);self.button(actions,'结束选中绑定',finish);refresh()

    def select_import(self):
        filename=filedialog.askopenfilename(filetypes=[('账单文件','*.csv *.xlsx')])
        if not filename:return
        window=tk.Toplevel(self);window.title('选择账单格式与账户');set_window_geometry(window,580,430);window.configure(bg=BG)
        box=scrollable_form(window,padx=20,pady=14)
        self.label(box,Path(filename).name,14,bold=True)
        self.label(box,'账单格式',10,MUTED)
        adapter=ttk.Combobox(box,values=['自动检测（推荐）','通用 CSV/XLSX','微信 CSV','银行模板 v1 XLSX'],state='readonly');adapter.current(0);adapter.pack(fill='x')
        accounts={'未指定账户（只能提供人工核对线索）':None}
        accounts.update({r['label']+' · '+r['channel']+' · '+r['id'][-6:]:r['id'] for r in self.store.accounts()})
        self.label(box,'属于哪个账户？',10,MUTED)
        account=ttk.Combobox(box,values=list(accounts),state='readonly');account.current(0);account.pack(fill='x')
        self.label(box,'不确定时明确选“未指定”。同一文件再次导入会复用来源文档，保留本次批次记录。',10,MUTED).configure(wraplength=px(510))
        row=tk.Frame(box,bg=BG);row.pack(fill='x')
        def proceed():
            choice={'自动检测（推荐）':'auto','通用 CSV/XLSX':'generic','微信 CSV':'wechat_csv','银行模板 v1 XLSX':'bank_template_v1'}[adapter.get()]
            selected=accounts[account.get()];window.destroy();self.import_async(filename,choice,selected)
        self.button(row,'开始预览',proceed);self.button(row,'管理账户',self.account_window)

    def six_row_demo(self):
        from .storage import Store
        from .m1a_demo import seed_six
        store=Store(Path(self.store.path).parent/'six-row-demo.sqlite3');seed_six(store)
        self.reconciliation_window(store,demo=True)

    def reconciliation_window(self,store=None,demo=False):
        store=store or self.store
        writable=not store.read_only
        window=tk.Toplevel(self);window.title('独立六行合成演示' if demo else '跨来源对账 · 人工确认');set_window_geometry(window,990,750);window.configure(bg=BG)
        box=scrollable_form(window,padx=18,pady=12)
        self.label(box,'同一笔消费，只统计一次',18,bold=True)
        if demo:self.label(box,'独立示例空间：两对20/40元、退款10元、转账500元。确认120→60，拆分60→120；不会写入你的账本。',10,PRIMARY).configure(wraplength=px(920))
        self.label(box,'A 有绑定且唯一 / B 描述相近，需人工确认；C 仅金额日期相近，不允许直接确认。',10,MUTED).configure(wraplength=px(930))
        total=self.label(box,'',12,PRIMARY)
        actions=tk.Frame(box,bg=BG);actions.pack(fill='x')
        status=self.label(box,'选中关联组，可查看下方全部来源。',10,MUTED)
        holder=tk.Frame(box,bg=BG);holder.pack(fill='both',expand=True)
        details=tk.Frame(box,bg=BG);details.pack(fill='both',expand=True)
        state={'rows':[],'tree':None,'scanning':False};mailbox=queue.Queue()
        labels={'candidate':'待确认','confirmed':'已确认','rejected':'已拒绝','split':'已拆分','stale':'候选已过期','needs_review':'待复核'}
        def refresh():
            f=store.transactions();pending=store.pending_refunds()
            total.config(text=f'当前消费 ¥{yuan(int(f.loc[f.kind=="支出","amount_cents"].sum()))} · 退款待处理 ¥{yuan(pending["amount_cents"])}（未扣退款，净支出待定）')
            for child in holder.winfo_children():child.destroy()
            for child in details.winfo_children():child.destroy()
            state['rows']=store.reconciliation_groups()[:200]
            state['tree']=self.table(holder,['状态','等级','金额 / 元','来源','依据'],[['待修复（已恢复计数）' if g['repair_pending'] else labels.get(g['status'],g['status']),g['confidence'],yuan(g['members'][0]['amount_cents']) if g['members'] else '', ' + '.join(r['channel'] for r in g['members']),g['reason']] for g in state['rows']],7)
            state['tree'].bind('<<TreeviewSelect>>',show_details)
            confirm_button.config(state='disabled')
            repair_button.config(state='disabled');reopen_button.config(state='disabled')
        def chosen():
            selection=state['tree'].selection()
            return state['rows'][self.row_index(state['tree'],selection[0])] if selection else None
        def show_details(_=None):
            for child in details.winfo_children():child.destroy()
            group=chosen()
            if not group:return
            active=writable and not state['scanning']
            confirm_button.config(state='normal' if active and group['status']=='candidate' and group['confidence'] in ('A','B') else 'disabled')
            repair_button.config(state='normal' if active and group['repair_pending'] else 'disabled')
            reopen_button.config(state='normal' if active and not group['repair_pending'] and group['status']!='confirmed' else 'disabled')
            self.label(details,group['reason'],10,PRIMARY).configure(wraplength=px(900))
            if group['repair_pending']:
                self.label(details,'此组存储状态仍为已确认，但当前来源已失效；统计已恢复独立计数。请先点“修复此组”保存待复核状态，再核对来源。',10,DANGER).configure(wraplength=px(900))
            diagnostics=store.group_diagnostics(group['id'])
            if group['confidence']=='C' or diagnostics['wallet_count']==0:
                self.label(details,'\n'.join('• '+g['message'] for g in diagnostics['gaps']),10,MUTED).configure(wraplength=px(900))
                guidance=tk.Frame(details,bg=BG);guidance.pack(fill='x')
                self.button(guidance,'去账户与支付绑定',lambda:self.account_window(store)).config(state='normal' if active else 'disabled')
                self.button(guidance,'重新生成候选',scan).config(state='normal' if active else 'disabled')
                if diagnostics['requires_reimport']:self.label(details,'已导入记录没有账户时，仅创建绑定不会补写旧记录。请按上面的重导步骤处理。',10,MUTED).configure(wraplength=px(900))
            self.table(details,['时间','商户 / 描述','金额 / 元','来源文件','行号','角色'],[[r['date'],r['merchant']+' / '+r['description'],yuan(r['amount_cents']),r['source_file'],r['source_row'],'消费代表' if r['role']=='purchase' else '资金来源腿'] for r in group['members']],4)
        def scan():
            if state['scanning'] or not writable:return
            state['scanning']=True
            for button in controls:button.config(state='disabled')
            status.config(text='正在核对来源并生成候选，尚未改变统计。')
            def worker():
                try:mailbox.put(('done',store.generate_candidates()))
                except Exception:mailbox.put(('error',None))
            threading.Thread(target=worker,daemon=True).start();window.poll=window.after(80,poll)
        def poll():
            try:kind,result=mailbox.get_nowait()
            except queue.Empty:window.poll=window.after(80,poll);return
            state['scanning']=False
            for button in controls:button.config(state='normal')
            refresh()
            if kind=='error':status.config(text='候选生成未完成，账单未改变，请重试。',fg=DANGER)
            else:status.config(text=f'发现 {result["candidates"]} 组候选；{result["overflow"]} 笔超过候选上限，需要缩小范围。未改变统计。')
        def decide(decision):
            group=chosen()
            if not group:return
            if decision=='confirmed' and group['confidence']=='C':
                status.config(text='C级证据不足，请补充账户、绑定或来源后重新核对。',fg=DANGER);return
            prompt={'confirmed':'确认这些来源属于同一笔消费？确认后仅统计消费代表。','rejected':'拒绝此候选？重跑会保留决定。','split':'拆分此组并恢复每条来源的独立计数？'}[decision]
            if not messagebox.askyesno('核对对账决定',prompt+'\n'+group['reason'],parent=window):return
            acknowledge=False
            if decision=='confirmed' and store.group_diagnostics(group['id'])['wallet_count']==0:
                acknowledge=messagebox.askyesno('再次核对：本组没有钱包记录','本组记录均非钱包消费，可能是两笔真实消费。请核对原始流水，确认它们只是同一消费的重复来源。仍要合并计数吗？',parent=window)
                if not acknowledge:return
            try:
                store.decide_group(group['id'],decision,acknowledge_no_wallet=acknowledge);refresh();self.show(self.page)
                status.config(text='决定已保存，所有页面使用相同统计口径。')
            except ValueError as exc:status.config(text=str(exc),fg=DANGER)
        controls=[self.button(actions,'生成 / 刷新候选',scan)]
        confirm_button=self.button(actions,'确认同笔消费',lambda:decide('confirmed'));controls.append(confirm_button)
        controls.append(self.button(actions,'拒绝候选',lambda:decide('rejected')))
        controls.append(self.button(actions,'拆分已确认组',lambda:decide('split')))
        def reopen():
            group=chosen()
            if group and messagebox.askyesno('重新核对','撤回原决定并重新列为候选？不会自动确认，也不会立即排重。',parent=window):
                try:store.reopen_group(group['id']);scan()
                except ValueError as exc:status.config(text=str(exc),fg=DANGER)
        reopen_button=self.button(actions,'重新核对',reopen);controls.append(reopen_button)
        def clean():
            count=store.clean_stale_candidates();refresh();status.config(text=f'已清理 {count} 组未决定的过期候选，人工决定与来源证据保留。')
        extra=tk.Frame(box,bg=BG);extra.pack(fill='x')
        self.button(extra,'刷新显示（只读）',refresh)
        def repair():
            group=chosen()
            if not group or not group['repair_pending']:return
            if not messagebox.askyesno('保存失效组修复','将此组保存为待复核并清空关联缓存？统计已恢复独立计数；不会修改交易或自动确认。',parent=window):return
            try:
                count=store.repair_invalid_groups(identifier=group['id'],source='user_selected')
                refresh();status.config(text='此组已保存为待复核，请核对来源后重新核对。' if count else '此组无需修复，请重新选择查看。',fg=PRIMARY)
            except (ValueError,OSError) as exc:status.config(text=str(exc),fg=DANGER)
        repair_button=self.button(extra,'修复此组',repair);controls.append(repair_button)
        controls.append(self.button(extra,'清理过期候选',clean))
        refresh()
        if writable:window.initial_scan=window.after_idle(scan)
        else:
            for control in controls:control.config(state='disabled')
            status.config(text='只读查看：未扫描或修复；操作需用可写模式打开。')
        def cleanup(event):
            if event.widget is window:
                if hasattr(window,'poll'):window.after_cancel(window.poll)
                if hasattr(window,'initial_scan'):window.after_cancel(window.initial_scan)
        window.bind('<Destroy>',cleanup)

    def import_history_window(self):
        store=self.store;window=tk.Toplevel(self);window.title('导入批次与来源');set_window_geometry(window,920,640);window.configure(bg=BG)
        box=tk.Frame(window,bg=BG,padx=px(18),pady=px(12));box.pack(fill='both',expand=True)
        self.label(box,'删除批次会从统计中移除该批次新增的交易，并使相关对账失效。',11)
        self.label(box,'采用软删除：原始行与审计继续保留。备份中也可能保留记录，此操作不等于物理清除个人数据。',10,MUTED).configure(wraplength=px(870))
        actions=tk.Frame(box,bg=BG);actions.pack(fill='x');holder=tk.Frame(box,bg=BG);holder.pack(fill='both',expand=True);state={}
        def refresh():
            for child in holder.winfo_children():child.destroy()
            state['rows']=store.import_batches()[:200]
            state['tree']=self.table(holder,['文件','时间','状态','新增','重复','冲突'],[[r['filename'],r['started_at'],'已删除' if r['status']=='deleted' else '已导入',r['added'],r['duplicates'],r['conflicts']] for r in state['rows']],9)
        def chosen():
            selection=state['tree'].selection()
            return state['rows'][self.row_index(state['tree'],selection[0])] if selection else None
        def remove():
            row=chosen()
            if row and messagebox.askyesno('删除导入批次','移除该批次新增的交易？重复跳过的交易属于原批次，不会被删除。',parent=window):
                try:store.delete_import_batch(row['id']);refresh();self.show(self.page)
                except ValueError as exc:messagebox.showerror('未删除',str(exc),parent=window)
        def export():
            row=chosen()
            if not row:return
            path=filedialog.asksaveasfilename(parent=window,defaultextension='.csv',initialfile='import-issues.csv')
            if path:
                import pandas as pd
                pd.DataFrame(json.loads(row['issues_json']),columns=['行号','原因','日期','金额','商户']).to_csv(path,index=False,encoding='utf-8-sig')
        self.button(actions,'删除选中批次',remove);self.button(actions,'导出异常与冲突',export);refresh()

    def backup_window(self):
        store=self.store;mode=self.mode;window=tk.Toplevel(self);window.title('数据库备份与恢复 · '+('演示空间' if mode=='demo' else '我的账本'));set_window_geometry(window,650,470);window.configure(bg=BG)
        box=scrollable_form(window,padx=20,pady=14)
        self.label(box,'备份当前空间',18,bold=True)
        self.label(box,'使用 SQLite 一致快照。只包含当前账本，不包含 API Key；演示和个人空间分别备份。',10,MUTED).configure(wraplength=px(590))
        status=self.label(box,'恢复会替换当前空间，恢复前会再自动备份。',10,PRIMARY);status.configure(wraplength=px(590))
        row=tk.Frame(box,bg=BG);row.pack(fill='x')
        def backup():
            try:status.config(text='备份已保存：'+str(store.backup()))
            except (OSError,ValueError) as exc:status.config(text=str(exc),fg=DANGER)
        def restore():
            path=filedialog.askopenfilename(parent=window,filetypes=[('SQLite 备份','*.sqlite3')])
            if not path:return
            try:
                info=inspect_database(path)
                text=f'备份版本：{info["version"]}；大小：{info["bytes"]:,} 字节\n记录数：{info["counts"]}\n将恢复到'+('演示空间' if mode=='demo' else '个人空间')+'。是否继续？'
                if messagebox.askyesno('预览并确认恢复',text,parent=window):
                    before=store.restore(path,confirmed=True);self.show(self.page);status.config(text='恢复完成；恢复前快照：'+str(before))
            except Exception as exc:status.config(text='恢复未完成：'+str(exc),fg=DANGER)
        self.button(row,'创建备份',backup);self.button(row,'选择备份并预览恢复',restore)

    def semantic_backups_window(self):
        store=self.store
        window=tk.Toplevel(self);window.title('修复明细与自动备份');set_window_geometry(window,940,740);window.configure(bg=BG)
        footer=tk.Frame(window,bg=BG,padx=px(18),pady=px(10));footer.pack(side='bottom',fill='x')
        box=scrollable_form(window,padx=18,pady=12)
        self.label(box,'当前空间的修复与自动备份',18,bold=True)
        self.label(box,'修复历史只记录分类变更，收支类型和金额不变。不会自动清理备份；旧版/通用快照未记录用途，不能一概视为语义修复备份。',10,MUTED).configure(wraplength=px(870))
        self.label(box,'最近200条语义修复历史（修复时分类，之后的人工修改不覆盖此记录）',11,bold=True)
        history=store.semantic_repair_history()
        self.table(box,['时间','商户','原类型','原分类','修复为'],[[r['created_at'],r['merchant'],r['detail'].get('kind',''),r['detail'].get('old_category',''),r['detail'].get('category','')] for r in history],4)
        summary=self.label(box,'',10,MUTED);summary.configure(wraplength=px(870))
        holder=tk.Frame(box,bg=BG);holder.pack(fill='both',expand=True)
        status=self.label(box,'选择快照可预览版本、记录数、大小与完整位置。',10,MUTED);status.configure(wraplength=px(870))
        state={'rows':[],'tree':None}
        def refresh():
            for child in holder.winfo_children():child.destroy()
            try:rows=store.managed_backups()
            except (ValueError,OSError) as exc:status.config(text=str(exc),fg=DANGER);rows=[]
            state['rows']=rows[:200]
            summary.config(text=f'当前空间 {len(rows)} 个自动快照，共 {sum(r["bytes"] for r in rows):,} 字节；显示最近200个。')
            state['tree']=self.table(holder,['时间（本机）','用途','大小 / KiB','文件'],[[datetime.fromtimestamp(r['modified']).strftime('%Y-%m-%d %H:%M:%S'),r['purpose'],f'{r["bytes"]/1024:,.1f}',r['name']] for r in state['rows']],7)
        def chosen():
            selected=state['tree'].selection()
            return state['rows'][self.row_index(state['tree'],selected[0])] if selected else None
        def preview():
            row=chosen()
            if not row:return
            try:
                info=store.preview_managed_backup(row['path'])
                status.config(text=f'版本 {info["version"]} · {info["bytes"]:,} 字节 · 记录数 {info["counts"]}\n位置：{info["path"]}',fg=PRIMARY)
            except (ValueError,OSError) as exc:status.config(text='预览未完成：'+str(exc),fg=DANGER)
        def remove():
            row=chosen()
            if not row:return
            if not messagebox.askyesno('删除选中快照',f'{row["name"]}\n{row["bytes"]:,} 字节；{row["purpose"]}\n这是独立备份。删除后无法用它回滚分类修复或恢复旧数据，当前账本不变。确定删除？',parent=window):return
            try:store.delete_managed_backup(row['path'],confirmed=True);refresh();status.config(text='选中快照已删除，当前账本与其他备份未变。',fg=PRIMARY)
            except (ValueError,OSError) as exc:status.config(text='未删除：'+str(exc),fg=DANGER)
        def open_folder():
            try:
                folder=store.managed_backup_folder()
                if not folder.is_dir():raise ValueError('当前空间尚无自动备份目录')
                os.startfile(str(folder))
            except (ValueError,OSError) as exc:status.config(text=str(exc),fg=DANGER)
        self.button(footer,'预览选中快照',preview);self.button(footer,'打开备份目录',open_folder)
        self.button(footer,'删除选中快照',remove).config(state='disabled' if store.read_only else 'normal')
        refresh()
