"""Refund processing, actionable review queue and per-space spending basis."""
import tkinter as tk
from tkinter import ttk,messagebox,filedialog
from pathlib import Path
from .ui_scale import px,set_window_geometry
from .ui_theme import BG,MUTED,PRIMARY,scrollable_form
from .analytics import yuan
from .refunds import RefundReviewRequired
from .spending_presenter import (BASIS_LABELS,BASIS_CHANGE_NOTICE,basis_label,spending_totals,
                                 refund_status_summary,refund_status_text,net_export_csv)

STATUS={'linked':'已关联','pending':'待处理','needs_review':'待复核','confirmed':'已确认',
        'candidate':'待确认','rejected':'已撤销/拒绝','split':'已拆分','stale':'已过期'}


class RefundFeatures:
    def export_spending(self):
        frame=self.store.spending_view()
        if self.selected_month!='全部月份':frame=frame[frame.date.str.startswith(self.selected_month)].copy()
        path=filedialog.asksaveasfilename(parent=self,defaultextension='.csv',initialfile='净额明细_'+frame.attrs['spending_basis']+'.csv')
        if path:Path(path).write_bytes(net_export_csv(frame))

    def refund_evidence_window(self,store,*,link_id=None,group=None,source_ids=None):
        if link_id:
            proof=store.refund_link_evidence(link_id);sources=proof['sources'];reasons=proof['reasons']
            caption='退款关联 '+link_id+' · '+STATUS.get(proof['effective_status'],proof['effective_status'])
        elif group:
            sources=[store.transaction_source(r['id']) for r in group['members']]
            reasons=[group.get('review_reason') or group['reason']];proof=dict(group=group,sources=sources)
            caption='退款来源组 · '+STATUS.get(group['status'],group['status'])
        else:
            sources=[store.transaction_source(i) for i in source_ids or []];reasons=[];proof=dict(sources=sources)
            caption='退款原始来源'
        window=tk.Toplevel(self);window.title('退款来源与证据');set_window_geometry(window,940,650);window.configure(bg=BG)
        box=scrollable_form(window,padx=18,pady=14)
        self.label(box,caption,13,bold=True).configure(wraplength=px(870))
        self.label(box,'；'.join(reasons) or '原始来源与确认快照均保留；查看不修改账本。',10,MUTED).configure(wraplength=px(870))
        inventory={identifier:event['association_status'] for event in store.refund_sources() for identifier in event['member_ids']}
        source_tree=self.table(box,['时间','商户','金额/元','类型','状态','文件','行号'],
            [[r.get('date','缺失'),r.get('merchant',''),yuan(r.get('amount_cents',0)),r.get('kind',''),
              STATUS[inventory[r['id']]] if r.get('status')=='refund_pending' and r['id'] in inventory else r.get('status','缺失'),r.get('source_file',''),r.get('source_row','')] for r in sources],7)
        def locate():
            selected=source_tree.selection()
            if selected:self.locate_transaction(sources[int(selected[0])]['id'],expected_path=store.path)
        self.button(box,'定位选中原始记录',locate)
        if link_id:
            saved=proof['saved_context'] or {}
            original=saved.get('purchase') if isinstance(saved,dict) else None
            refund=saved.get('refund') if isinstance(saved,dict) else None
            self.label(box,f'原确认金额 ¥{yuan(proof["link"]["amount_cents"])}；下表为当前来源，完整快照保留确认当时的日期、分类及成员。',10,MUTED).configure(wraplength=px(870))
            self.label(box,f'原消费代表：{original.get("representative_id", "记录损坏") if isinstance(original,dict) else "记录损坏"}\n退款代表：{refund.get("representative_id", "记录损坏") if isinstance(refund,dict) else "记录损坏"}',9,MUTED).configure(wraplength=px(870))
        self.button(box,'查看完整确认与审计快照',lambda:self.show_calculation_trace(proof))
        return window

    def refund_window(self):
        store=self.store;path=store.path
        window=tk.Toplevel(self);window.title('退款与统计口径 · '+('演示空间' if self.mode=='demo' else '我的账本'))
        set_window_geometry(window,1020,820);window.configure(bg=BG)
        footer=tk.Frame(window,bg=BG,padx=px(16),pady=px(8));footer.pack(side='bottom',fill='x')
        status=self.label(footer,'查看和刷新不写库；有效关联必须先撤销再重新确认。',10,MUTED);status.configure(wraplength=px(950))
        box=scrollable_form(window,padx=16,pady=12)
        self.label(box,'退款只抵扣一次，每一步都有来源',17,bold=True)
        self.label(box,'一笔退款整笔关联一个原消费，不拆合单退款。原消费只有“已退款”状态而缺退款流水时，无法确认净额。',10,MUTED).configure(wraplength=px(940))
        basis_row=tk.Frame(box,bg=BG);basis_row.pack(fill='x')
        picker=ttk.Combobox(basis_row,values=list(BASIS_LABELS.values()),state='readonly',width=35);picker.pack(side='left',padx=px((0,8)))
        summary=self.label(box,'',11,PRIMARY);summary.configure(wraplength=px(920))
        notebook=ttk.Notebook(box);notebook.pack(fill='both',expand=True,pady=px(8))
        tabs=[]
        for title in ('退款流水与关联','待复核与阻塞','退款来源去重'):
            tab=tk.Frame(notebook,bg=BG,padx=px(10),pady=px(8));notebook.add(tab,text=title);tabs.append(tab)
        flow,review,dedup=tabs
        state={'events':[],'links':[],'work':[],'groups':[],'sources':{},'refund_id':None,'link_id':None}
        window.refund_state=state;window.controls={};controls=window.controls
        def writable():
            if store.read_only:status.config(text='只读账本不能修改退款或口径。');return False
            if Path(self.store.path).resolve()!=Path(path).resolve():status.config(text='空间已切换，请返回本窗口原空间后再操作。');return False
            return True
        def perform(action,notice):
            if not writable():return
            try:action()
            except Exception as exc:
                refresh()
                if isinstance(exc,RefundReviewRequired) or state['work']:
                    notebook.select(review);status.config(text='请在待复核页查看具体ID和原因，再定点修复或撤销；未保存本次确认。')
                else:status.config(text='未完成：'+str(exc))
                return
            refresh();status.config(text=notice)
            if Path(self.store.path).resolve()==Path(path).resolve():self.show(self.page)
        def change_basis():
            chosen=next(k for k,v in BASIS_LABELS.items() if v==picker.get())
            if chosen==store.spending_basis():return
            if not writable():return
            if not messagebox.askyesno('切换净支出口径',BASIS_CHANGE_NOTICE+'\n历史回答保留当时口径，新查询使用新设置。',parent=window):
                picker.set(basis_label(store.spending_basis()));return
            perform(lambda:store.set_spending_basis(chosen,confirmed=True),'已切换口径，当前空间金额已重算；原始账单未改。')
        controls['basis']=picker;controls['apply_basis']=self.button(basis_row,'应用口径',change_basis)
        controls['apply_basis'].config(bg='white',fg=PRIMARY)
        events_box=tk.Frame(flow,bg=BG);events_box.pack(fill='x')
        source_controls=tk.Frame(flow,bg=BG);source_controls.pack(fill='x')
        purchase=ttk.Combobox(source_controls,state='readonly',width=62);purchase.pack(side='left',fill='x',expand=True)
        note=ttk.Entry(flow);note.insert(0,'已核对原订单、完整退款金额与来源凭证');note.pack(fill='x',pady=px(4))
        self.label(flow,'上方选择原消费来源；资金腿会由服务解析为同一消费。下方选择关联历史可查看证据或撤销。',9,MUTED).configure(wraplength=px(890))
        flow_actions=tk.Frame(flow,bg=BG);flow_actions.pack(fill='x')
        history_actions=tk.Frame(flow,bg=BG);history_actions.pack(fill='x')
        links_box=tk.Frame(flow,bg=BG);links_box.pack(fill='x')
        def picked_refund(_=None):
            selection=state['event_tree'].selection()
            event=state['events'][int(selection[0])] if selection else None
            state['refund_id']=event['representative_id'] if event else None
            matches=[]
            for index,link in enumerate(state['links']):
                members={link['refund_txn_id']}
                try:
                    import json
                    context=json.loads(link['context_json']);saved=context.get('refund') if isinstance(context,dict) else None
                    if isinstance(saved,dict) and isinstance(saved.get('member_ids'),list):members.update(v for v in saved['member_ids'] if isinstance(v,str))
                except (TypeError,ValueError):pass
                if event and members.intersection(event['member_ids']):matches.append((link['stored_status']=='confirmed',link['stored_status']=='needs_review',index))
            state['link_tree'].selection_remove(*state['link_tree'].selection())
            state['link_id']=None
            if matches:
                index=max(matches)[2];state['link_tree'].selection_set(str(index));state['link_id']=state['links'][index]['id']
            refresh_buttons()
        def picked_link(_=None):
            selection=state['link_tree'].selection()
            state['link_id']=state['links'][int(selection[0])]['id'] if selection else None
            refresh_buttons()
        def current_purchase():return state.get('purchase_choices',{}).get(purchase.get())
        def confirm():
            refund_id=state['refund_id'];original=current_purchase()
            if not refund_id or not original:status.config(text='请选择一笔退款和原消费。');return
            if messagebox.askyesno('确认退款关联','确认这笔完整退款只抵扣所选原消费？金额与日期不会改写。',parent=window):
                perform(lambda:store.confirm_refund_link(refund_id,original,reason=note.get()),'退款已关联；净额已更新。')
        def role():
            original=current_purchase()
            if original and messagebox.askyesno('确认来源角色','确认所选记录是原消费，而非退款流水？确认后计入消费总额；只有独立退款另行关联后才抵扣。',parent=window):
                perform(lambda:store.confirm_purchase_role(original,reason=note.get()),'已确认原消费角色，请核对退款后关联。')
        def inspect_refund():
            event=next((r for r in state['events'] if r['representative_id']==state['refund_id']),None)
            if event:self.refund_evidence_window(store,source_ids=event['member_ids'])
        controls['purchase']=purchase;controls['reason']=note
        controls['confirm']=self.button(flow_actions,'确认退款关联',confirm)
        controls['purchase_role']=self.button(flow_actions,'确认选中为原消费',role)
        controls['inspect_refund']=self.button(flow_actions,'查看退款来源',inspect_refund)
        controls['locate_purchase']=self.button(flow_actions,'定位原消费',lambda:self.locate_transaction(current_purchase(),expected_path=store.path) if current_purchase() else None)
        controls['inspect_link']=self.button(history_actions,'查看关联证据',lambda:self.refund_evidence_window(store,link_id=state['link_id']) if state['link_id'] else None)
        def revoke_link():
            identifier=state['link_id']
            if identifier and messagebox.askyesno('撤销退款关联','撤销后恢复未抵扣；来源和审计保留。需要修改原消费或说明时，请撤销后重新确认。',parent=window):
                perform(lambda:store.revoke_refund_link(identifier),'已撤销，退款可重新核对关联。')
        controls['revoke']=self.button(history_actions,'撤销选中关联',revoke_link)
        self.label(review,'全局异常会阻止新确认。先检查具体来源，再保存所选组件修复或撤销。无效退款组也列在这里。',10,MUTED).configure(wraplength=px(890))
        work_box=tk.Frame(review,bg=BG);work_box.pack(fill='x');work_actions=tk.Frame(review,bg=BG);work_actions.pack(fill='x')
        work_reason=self.label(review,'选中事项可查看完整原因与相关关联。',10,MUTED);work_reason.configure(wraplength=px(880))
        def chosen_work():
            selection=state['work_tree'].selection()
            return state['work'][int(selection[0])] if selection else None
        def inspect_work():
            item=chosen_work()
            if not item:return
            if item['kind']=='link':self.refund_evidence_window(store,link_id=item['id'])
            else:self.refund_evidence_window(store,group=next(g for g in store.reconciliation_groups() if g['id']==item['id']))
        def repair_work():
            item=chosen_work()
            if not item:return
            if messagebox.askyesno('保存定点修复','将所选失效决定及其相关组件保存为待复核，保留确认快照；不会重新确认金额。',parent=window):
                action=(lambda:store.repair_refund_links(identifier=item['id'],source='refund_ui')) if item['kind']=='link' else (lambda:store.repair_invalid_groups(identifier=item['id'],source='refund_ui'))
                perform(action,'已保存复核状态。请修正来源，按需要重新核对组，再明确确认退款。')
        def revoke_work():
            item=chosen_work()
            if not item:return
            if messagebox.askyesno('撤销关联 / 拆分组','撤销所选关联或拆分所选已确认组？原始来源和证据保留。',parent=window):
                perform((lambda:store.revoke_refund_link(item['id'])) if item['kind']=='link' else (lambda:store.decide_group(item['id'],'split')),'已撤销关联或拆分组，未自动重新确认。')
        controls['inspect_work']=self.button(work_actions,'查看阻塞来源',inspect_work)
        controls['repair_work']=self.button(work_actions,'定点修复',repair_work)
        controls['revoke_work']=self.button(work_actions,'撤销关联 / 拆分组',revoke_work)
        self.label(review,'待复核表示尚未抵扣；保存修复后，已不再是有效confirmed阻塞。返回“退款流水与关联”重新核对。',9,MUTED).configure(wraplength=px(890))
        self.label(dedup,'仅手工核对两条来源为同一退款；不会自动匹配或自动抵扣。',10,MUTED)
        left=ttk.Combobox(dedup,state='readonly',width=75);left.pack(fill='x',pady=px(4))
        right=ttk.Combobox(dedup,state='readonly',width=75);right.pack(fill='x',pady=px(4))
        acknowledge=tk.BooleanVar();ttk.Checkbutton(dedup,text='我已核对，两条来源确为同一笔退款',variable=acknowledge).pack(anchor='w')
        non_wallet=tk.BooleanVar();ttk.Checkbutton(dedup,text='若两条均非钱包，我已另行核对并非两笔不同退款',variable=non_wallet).pack(anchor='w')
        group_actions=tk.Frame(dedup,bg=BG);group_actions.pack(fill='x');groups_box=tk.Frame(dedup,bg=BG);groups_box.pack(fill='x')
        def propose():
            ids=[state.get('refund_choices',{}).get(widget.get()) for widget in (left,right)]
            if not all(ids):status.config(text='请选择两条退款来源。');return
            perform(lambda:store.create_refund_group(ids,reason=note.get()),'已提出候选，尚未确认去重或抵扣。')
        def group_decide(decision):
            selection=state['group_tree'].selection()
            if not selection:return
            group=state['groups'][int(selection[0])]
            if decision=='confirmed' and not acknowledge.get():status.config(text='请先勾选已核对两条来源为同一退款。');return
            if messagebox.askyesno('退款组决定','此操作会使相关旧退款决定失效；组确认后仍需另外确认退款关联。',parent=window):
                action=(lambda:store.reopen_group(group['id'])) if decision=='reopen' else (lambda:store.decide_group(group['id'],decision,acknowledge_no_wallet=non_wallet.get()))
                perform(action,'组决定已保存；请另外核对资金关联。')
        controls.update(refund_left=left,refund_right=right,acknowledge=acknowledge,non_wallet=non_wallet)
        controls['propose_group']=self.button(group_actions,'提出同退款候选',propose)
        controls['confirm_group']=self.button(group_actions,'确认退款组',lambda:group_decide('confirmed'))
        controls['split_group']=self.button(group_actions,'拆分退款组',lambda:group_decide('split'))
        controls['reopen_group']=self.button(group_actions,'重新核对组',lambda:group_decide('reopen'))
        def refresh_buttons(_=None):
            enabled=not store.read_only
            event=next((r for r in state['events'] if r['representative_id']==state['refund_id']),None)
            original=state['sources'].get(current_purchase(),{})
            controls['confirm'].config(state='normal' if enabled and event and event['association_status']!='linked' and original.get('status') in ('normal','legacy') else 'disabled')
            controls['confirm'].config(bg=PRIMARY if str(controls['confirm']['state'])=='normal' else '#edf2f0',fg='white',disabledforeground=MUTED)
            controls['purchase_role'].config(state='normal' if enabled and original.get('status')=='review' else 'disabled')
            link=next((r for r in state['links'] if r['id']==state['link_id']),None)
            controls['revoke'].config(state='normal' if enabled and link and link['stored_status'] in ('confirmed','needs_review') else 'disabled')
            for key in ('apply_basis','propose_group'):
                controls[key].config(state='normal' if enabled else 'disabled')
            item=chosen_work()
            work_group=next((g for g in state['groups'] if item and g['id']==item['id']),None)
            controls['repair_work'].config(state='normal' if enabled and item and (item['kind']=='link' or item.get('repair_pending')) else 'disabled')
            controls['revoke_work'].config(state='normal' if enabled and item and (item['kind']=='link' or work_group and work_group['stored_status']=='confirmed') else 'disabled')
            if item:work_reason.config(text='；'.join(item['reasons'])+'\n相关关联：'+'、'.join(item.get('related_link_ids',[])))
            group_selection=state['group_tree'].selection()
            group=state['groups'][int(group_selection[0])] if group_selection else None
            for key,allowed in [('confirm_group',group and group['stored_status']=='candidate'),('split_group',group and group['stored_status']=='confirmed'),
                                ('reopen_group',group and group['stored_status'] in ('needs_review','split','rejected','stale'))]:
                controls[key].config(state='normal' if enabled and allowed else 'disabled')
        purchase.bind('<<ComboboxSelected>>',refresh_buttons)
        def refresh():
            view=store.spending_view();totals=spending_totals(view);state['totals']=totals
            picker.set(basis_label(view.attrs['spending_basis']))
            summary.config(text=f'消费总额 ¥{yuan(totals["gross_cents"])} − 已确认抵扣 ¥{yuan(totals["refund_cents"])} = 净支出 ¥{yuan(totals["net_cents"])}\n'+refund_status_text(refund_status_summary(view.attrs['refund_sources'])))
            state['events']=store.refund_sources();state['links']=store.refund_links();state['work']=store.refund_work_items()
            state['groups']=[g for g in store.reconciliation_groups() if g['kind']=='refund_duplicate']
            state['sources']={r['id']:r for r in store.source_transactions().to_dict('records')}
            def source_label(r):return f"{r['date'][:10]} · {r['merchant'][:16]} · ¥{yuan(r['amount_cents'])} · {r['id']} · {r['counting_note']}"
            state['purchase_choices']={source_label(r):r['id'] for r in state['sources'].values() if r['kind']=='支出' and r['status'] in ('normal','legacy','review')}
            state['refund_choices']={source_label(r):r['id'] for r in state['sources'].values() if r['status']=='refund_pending'}
            for widget,choices in ((purchase,state['purchase_choices']),(left,state['refund_choices']),(right,state['refund_choices'])):
                old=widget.get();options=list(choices);default=options[1] if widget is right and len(options)>1 else next(iter(choices),'')
                widget.configure(values=options);widget.set(old if old in choices else default)
            for holder in (events_box,links_box,work_box,groups_box):
                for child in holder.winfo_children():child.destroy()
            state['event_tree']=self.table(events_box,['退款时间','状态','金额/元','来源数','原因'],[[r['date'][:10],STATUS[r['association_status']],yuan(r['amount_cents']),len(r['member_ids']),'；'.join(r['reasons'])] for r in state['events']],5)
            state['link_tree']=self.table(links_box,['关联状态','金额/元','关联ID','退款ID','原消费ID'],[[STATUS.get(r['status'],r['status']),yuan(r['amount_cents']),r['id'],r['refund_txn_id'],r['original_txn_id']] for r in state['links']],4)
            state['work_tree']=self.table(work_box,['类型','ID','原因'],[['退款关联' if r['kind']=='link' else '退款组',r['id'],'；'.join(r['reasons'])] for r in state['work']],7)
            state['group_tree']=self.table(groups_box,['状态','组ID','来源','说明'],[[STATUS.get(g['status'],g['status']),g['id'],' / '.join(r['id'] for r in g['members']),g.get('review_reason') or g['reason']] for g in state['groups']],5)
            state['event_tree'].bind('<<TreeviewSelect>>',picked_refund);state['link_tree'].bind('<<TreeviewSelect>>',picked_link)
            state['work_tree'].bind('<<TreeviewSelect>>',refresh_buttons);state['group_tree'].bind('<<TreeviewSelect>>',refresh_buttons)
            for key in ('event_tree','link_tree','work_tree','group_tree'):
                if state[key].get_children():state[key].selection_set(state[key].get_children()[0])
            picked_refund();picked_link();refresh_buttons()
        controls['refresh']=self.button(footer,'刷新（只读）',refresh);controls['status']=status;controls['summary']=summary;controls['notebook']=notebook
        window.refresh_refunds=refresh;refresh()
        return window
