"""User-approved cycle snapshot; independent of automatic CRM refreshes."""
import json
import re
from pathlib import Path


def load(start, end):
    path = Path(__file__).parent / 'approved-plans' / f'{start}_{end}.json'
    if not path.exists():
        return None
    plan = json.loads(path.read_text(encoding='utf-8'))
    rows = plan['records']
    assert len(rows) == len({r['id'] for r in rows})
    assert plan['cycle_start'] == str(start) and plan['cycle_end'] == str(end)
    for name, (total, debt) in plan['expected'].items():
        selected = [r for r in rows if r['curator'] == name]
        assert len(selected) == total
        assert sum(r['approved_overdue'] for r in selected) == debt
    assert len(rows) == sum(n[0] for n in plan['expected'].values())
    assert all(str(start) <= r['due'] <= str(end) for r in rows)
    return plan


def apply_document(document, plan):
    """Use approved payment classification, including overdue automatic freezes."""
    document = document.replace(
        'const isOverdue=x=>Number(x.balance||0)<0;',
        'const isOverdue=x=>x.approved_overdue===true;')
    document = document.replace(
        'const businessStatus=x=>{',
        "const businessStatus=x=>{if(x.approved_status)return x.approved_status;")
    document = re.sub(r'<button[^>]*id="debtCard"[^>]*>',
                      lambda m: m[0].replace('data-list="debt"', 'data-list="overdue"'), document)
    return document


def refresh_payments(plan, query, today):
    """Freeze membership and obligations only; read financial state on every build.

    Query all approved IDs directly, including paid students no longer returned
    by the CRM debtor list. Never publish a partially refreshed roster.
    """
    import datetime as dt
    end = dt.date.fromisoformat(plan['cycle_end']) + dt.timedelta(days=1)
    live = {}
    ids = [int(r['id']) for r in plan['records']]
    for offset in range(0, len(ids), 300):
        joined = ','.join(map(str, ids[offset:offset + 300]))
        rows = query(
            "SELECT s.ID sid,s.CURRENT_BALANCE balance,"
            "COALESCE(SUM(CASE WHEN t.ACTION_TYPE='add' THEN t.AMOUNT ELSE 0 END),0) amount,"
            "SUM(CASE WHEN t.ACTION_TYPE='add' THEN 1 ELSE 0 END) payments,"
            "MAX(CASE WHEN t.ACTION_TYPE='add' THEN t.TRANSACTION_DATE END) last_paid,"
            "COALESCE(SUM(CASE WHEN t.ACTION_TYPE='taken' THEN t.AMOUNT ELSE 0 END),0) charged "
            "FROM student_list s LEFT JOIN transaction_list t ON t.STUDENT_ID=s.ID AND t.ORG_ID=s.ORG_ID "
            f"AND t.TRANSACTION_DATE>='{plan['cycle_start']}' AND t.TRANSACTION_DATE<'{end}' "
            "AND t.TRANSACTION_DATE<=NOW() "
            f"WHERE s.ORG_ID=6 AND s.ID IN ({joined}) GROUP BY s.ID,s.CURRENT_BALANCE")
        live.update({int(r['sid']): r for r in rows})
    if set(ids) != set(live):
        raise RuntimeError('Approved roster CRM refresh incomplete; refusing stale publication')
    result = []
    for original in plan['records']:
        row = dict(original)
        state = live[int(row['id'])]
        balance = int(state['balance'])
        amount = int(state['amount'] or 0)
        required = int(row.get('required') or 0)
        arrived = row['due'] <= today
        # Zero balance before the due date alone does not mean prepaid.
        evidence = (int(state['charged'] or 0) > 0 or
                    (required > 0 and amount >= required) or original.get('paid', False))
        paid = balance >= 0 and bool(evidence) and (
            arrived or original.get('paid', False) or (required > 0 and amount >= required))
        debt = max(0, -balance) if arrived else max(0, required - max(0, balance))
        row.update(balance=balance, amount=amount, payments=int(state['payments'] or 0),
                   last_paid=str(state['last_paid'] or ''), paid=paid,
                   partial=amount > 0 and not paid, due_arrived=arrived,
                   debt_amount=0 if paid else debt,
                   approved_status='paid' if paid else 'debt',
                   approved_overdue=arrived and not paid and debt > 0,
                   approved_label='Тўланган' if paid else 'Тўлов кутилмоқда')
        # Explicit, one-off November roster amendment. Old CRM debt remains
        # untouched; this campaign collects at most one monthly instalment.
        if (plan['cycle_start'], plan['cycle_end']) == ('2026-10-25', '2026-11-23') and original.get('november_one_month'):
            if required <= 0:
                raise ValueError(f"Missing November monthly price: {row['id']}")
            settled = arrived and balance >= 0 and int(state['charged'] or 0) > 0
            paid = amount >= required or settled
            remaining = max(0, required - amount)
            if arrived and balance < 0:
                remaining = min(remaining, -balance)
            archived = bool(original.get('plan_archived_unpaid'))
            row.update(
                paid=paid and not archived,
                partial=0 < amount < required and not paid and not archived,
                debt_amount=0 if paid or archived else remaining,
                approved_status='archive' if archived else ('paid' if paid else 'debt'),
                approved_overdue=arrived and not paid and not archived and remaining > 0,
                approved_label='Тўламасдан архивланган' if archived else (
                    'Тўланган' if paid else 'Бир ойлик тўлов кутилмоқда'))
        result.append(row)
    return result


def refresh_statuses(plan, records, query, today):
    """Refresh lifecycle evidence without adding/removing approved plan members."""
    live = {}
    ids = [int(r['id']) for r in records]
    for offset in range(0, len(ids), 300):
        joined = ','.join(map(str, ids[offset:offset + 300]))
        states = query(
            "SELECT s.ID sid,sub.ID subscription_id,sub.STATUS subscription_status,"
            "sub.PRICE_TYPE tariff,sub.START_DATE subscription_start,"
            "sub.END_OF_SUBSCRIPTION subscription_end,"
            "f.TYPE freeze_type,fr.REASON freeze_reason,"
            "CASE WHEN JSON_VALID(l.COMMENT) THEN "
            "JSON_UNQUOTE(JSON_EXTRACT(l.COMMENT,'$.reason')) ELSE NULL END archive_reason "
            "FROM student_list s LEFT JOIN subscribe_list sub ON sub.ID=("
            "SELECT a.ID FROM subscribe_list a WHERE a.ORG_ID=6 AND a.STUDENT_ID=s.ID "
            "AND a.STATUS!='planned' AND a.START_DATE<=NOW() "
            "ORDER BY a.ACTIVE DESC,a.ID DESC LIMIT 1) "
            "LEFT JOIN frozen_student_list f ON f.ID=(SELECT f0.ID FROM frozen_student_list f0 "
            "WHERE f0.STUDENT_ID=s.ID AND f0.ACTIVE=1 "
            "AND EXISTS (SELECT 1 FROM subscribe_list fs WHERE fs.ID=f0.SUBSCRIBE_ID "
            "AND fs.ORG_ID=6 AND fs.GROUP_ID=sub.GROUP_ID) "
            "AND f0.START_DATE<=NOW() AND (f0.END_DATE IS NULL OR f0.END_DATE>=NOW()) "
            "ORDER BY f0.ID DESC LIMIT 1) "
            "LEFT JOIN frozen_reason fr ON fr.ID=f.REASON_ID "
            "LEFT JOIN table_log l ON l.ID=(SELECT l0.ID FROM table_log l0 "
            "WHERE l0.ITEM_ID=s.ID AND l0.TABLE_NAME IN ('student_list','students') "
            "AND l0.ACTION='delete_student' AND l0.LOG_DATE<=NOW() ORDER BY l0.ID DESC LIMIT 1) "
            f"WHERE s.ORG_ID=6 AND s.ID IN ({joined})")
        live.update({int(s['sid']): s for s in states})
    if set(live) != set(ids):
        raise RuntimeError('Approved roster status refresh incomplete; refusing stale publication')
    result = []
    for original in records:
        row = dict(original)
        state = live[row['id']]
        sub_status = str(state['subscription_status'] or '').lower()
        reason = str(state.get('archive_reason') or '').strip()
        start = str(state.get('subscription_start') or '')[:10]
        end = str(state.get('subscription_end') or '')[:10]
        tariff = str(state.get('tariff') or '').strip().lower()
        # A referral expiring on the renewal date does not pay that renewal.
        referral_covers = ('sarafan' in tariff and start <= today and
                           end > max(today, row['due']) and row['balance'] >= 0)
        auto_freeze = (str(state.get('freeze_type') or '').lower() == 'auto' or
                       str(state.get('freeze_reason') or '').lower().startswith('auto_overdue'))
        manual_freeze = (str(state.get('freeze_type') or '').lower() in {'freezing', 'juniorfreezing'}
                         and not auto_freeze)
        status = row['approved_status']
        if original.get('plan_archived_unpaid'):
            status = 'archive'
        elif sub_status in {'archive', 'cancelled'}:
            status = 'finished' if reason.lower() == 'bitirdi' else 'archive'
        elif referral_covers:
            status = 'referral'
        elif row['paid']:
            status = 'paid'
        elif sub_status == 'freezed' and manual_freeze:
            status = 'frozen'
        else:
            status = 'debt'
        row.update(subscription_status=sub_status, crm_subscription_id=state.get('subscription_id'),
                   crm_tariff=state.get('tariff'), crm_subscription_start=start,
                   crm_subscription_end=end, crm_archive_reason=reason,
                   crm_freeze_reason=state.get('freeze_reason'),
                   crm_freeze_type=state.get('freeze_type'), status_refreshed_on=today,
                   approved_status=status, is_referral=status == 'referral',
                   analytics_bucket={'finished': 'bit', 'archive': 'deleted'}.get(status, status))
        if status not in {'paid', 'debt'}:
            row.update(paid=False, partial=False, approved_overdue=False)
        if status in {'finished', 'referral', 'archive'}:
            row['debt_amount'] = 0
        row['approved_label'] = {
            'paid': 'Тўланган', 'finished': 'Битирган', 'referral': 'Сарафан',
            'frozen': 'Музлатилган', 'archive': 'Архивланган',
            'debt': 'Қарздор' if row['approved_overdue'] else 'Тўлов кутилмоқда'}[status]
        result.append(row)
    return result
