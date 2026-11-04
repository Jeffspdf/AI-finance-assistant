"""Streamlit refund workflow; the same services used by the desktop client."""
from .spending_presenter import BASIS_LABELS,BASIS_CHANGE_NOTICE,basis_label,spending_totals,refund_status_summary,refund_status_text
from .analytics import yuan


def render_refunds(st,store):
    view=store.spending_view();totals=spending_totals(view)
    st.title('退款与统计口径')
    st.caption(f'消费总额 ¥{yuan(totals["gross_cents"])} − 已确认抵扣 ¥{yuan(totals["refund_cents"])} = 净支出 ¥{yuan(totals["net_cents"])}')
    st.write(refund_status_text(refund_status_summary(view.attrs['refund_sources'])))
    basis=st.selectbox('净支出口径',list(BASIS_LABELS),index=list(BASIS_LABELS).index(store.spending_basis()),format_func=basis_label)
    st.info(BASIS_CHANGE_NOTICE)
    consent=st.checkbox('我确认重算当前空间净额，原始账单不变',key='basis_consent')
    if st.button('应用净支出口径',disabled=not consent):
        try:store.set_spending_basis(basis,confirmed=True);st.rerun()
        except Exception as exc:st.error('口径未保存：'+str(exc))
    events=store.refund_sources();links=store.refund_links();groups=[g for g in store.reconciliation_groups() if g['kind']=='refund_duplicate']
    sources={r['id']:r for r in store.source_transactions().to_dict('records')}
    st.caption('退款整笔关联一个原消费；有效关联必须先撤销再重新确认。原消费仅有“已退款”状态但缺独立退款流水时不抵扣。')
    if events:
        st.dataframe([dict(日期=r['date'],退款ID=r['representative_id'],状态={'pending':'待处理','needs_review':'待复核','linked':'已关联'}[r['association_status']],金额=r['amount_cents']/100,原因='；'.join(r['reasons'])) for r in events],hide_index=True)
        event=st.selectbox('退款流水',events,format_func=lambda r:r['representative_id']+' · '+r['association_status'])
        original_ids=[r['id'] for r in sources.values() if r['kind']=='支出' and r['status'] in ('normal','legacy','review')]
        if original_ids:
            original=st.selectbox('原消费来源',original_ids,format_func=lambda identifier:sources[identifier]['merchant']+' · '+yuan(sources[identifier]['amount_cents'])+' · '+identifier)
            reason=st.text_input('核对说明',value='已核对原订单、完整退款金额与来源')
            if sources[original]['status']=='review':
                role=st.checkbox('确认所选是原消费，不是退款流水')
                if st.button('确认原消费角色',disabled=not role):
                    try:store.confirm_purchase_role(original,reason=reason);st.rerun()
                    except Exception as exc:st.error(str(exc))
            confirm=st.checkbox('已核对完整退款及原消费来源')
            if st.button('确认退款关联',disabled=not confirm or event['association_status']=='linked' or sources[original]['status']=='review'):
                try:store.confirm_refund_link(event['representative_id'],original,reason=reason);st.rerun()
                except Exception as exc:st.error(str(exc))
    st.subheader('待复核与阻塞')
    work=store.refund_work_items()
    if not work:st.success('没有尚未修复的退款关联或退款组阻塞。已修复的组请在“退款来源去重”中重新核对。')
    else:
        selected=st.selectbox('选择待复核事项',work,format_func=lambda r:r['kind']+' · '+r['id'])
        st.warning('；'.join(selected['reasons']))
        if selected['kind']=='link':
            st.caption('相关关联：'+'、'.join(selected.get('related_link_ids',[])))
            if st.button('查看阻塞来源与证据'):st.json(store.refund_link_evidence(selected['id']))
        else:
            group=next(g for g in groups if g['id']==selected['id'])
            if st.button('查看阻塞来源与证据'):st.json(dict(group=group,sources=[store.transaction_source(r['id']) for r in group['members']]))
        agree=st.checkbox('确认保存所选组件的复核状态，不自动重新确认金额')
        if st.button('保存定点修复',disabled=not agree):
            try:
                if selected['kind']=='link':store.repair_refund_links(identifier=selected['id'],source='web_selected')
                else:store.repair_invalid_groups(identifier=selected['id'],source='web_selected')
                st.rerun()
            except Exception as exc:st.error('修复未保存：'+str(exc))
    if links:
        st.subheader('关联历史与撤销')
        selected=st.selectbox('关联历史',links,format_func=lambda r:r['id']+' · '+r['status'])
        st.caption('；'.join(selected['review_reasons']) or '有效关联若需修改原消费或说明，请先撤销。')
        if st.button('查看关联证据'):st.json(store.refund_link_evidence(selected['id']))
        revoke=st.checkbox('确认撤销所选关联，保留来源和审计')
        if st.button('撤销选中关联',disabled=not revoke or selected['stored_status'] not in ('confirmed','needs_review')):
            try:store.revoke_refund_link(selected['id']);st.rerun()
            except Exception as exc:st.error('撤销未保存：'+str(exc))
    st.subheader('退款来源去重')
    refund_ids=[identifier for identifier,r in sources.items() if r['status']=='refund_pending']
    if len(refund_ids)>=2:
        first=st.selectbox('退款来源一',refund_ids);second=st.selectbox('退款来源二',refund_ids,index=1)
        if st.button('提出同退款候选'):
            try:store.create_refund_group([first,second],reason='用户核对跨源退款');st.rerun()
            except Exception as exc:st.error(str(exc))
    if groups:
        group=st.selectbox('退款组',groups,format_func=lambda g:g['id']+' · '+g['status'])
        st.caption(group.get('review_reason') or group['reason'])
        same=st.checkbox('已核对为同一退款；若无钱包，另行确认并非两笔退款')
        actions=['确认'] if group['stored_status']=='candidate' else ['拆分'] if group['stored_status']=='confirmed' else ['重新核对']
        action=st.selectbox('退款组动作',actions)
        if st.button('保存退款组决定',disabled=not same):
            try:
                if action=='重新核对':store.reopen_group(group['id'])
                else:store.decide_group(group['id'],'confirmed' if action=='确认' else 'split',acknowledge_no_wallet=same)
                st.rerun()
            except Exception as exc:st.error(str(exc))
