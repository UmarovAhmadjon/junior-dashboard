"""Open collection cycles from CRM obligations, without the debtor HTML report.

Membership is saved separately from live finance/lifecycle evidence. Old cycle
payments and the one-off November campaign never become a new month's fact.
"""
import calendar
import datetime as dt
import json
from pathlib import Path
from zoneinfo import ZoneInfo

# Fotima's CRM account migrated to 24241; retain the old ID for her existing groups.
ADMINS = {13799, 24241, 14974, 14241, 14451, 16386, 16005, 21463}
PLAN_FIELDS = ('id', 'name', 'admin_id', 'curator', 'admin_full', 'team', 'group',
               'due', 'required', 'price', 'tariff', 'is_package',
               'package_months', 'package_until', 'obligation_source')


def date(value):
    try:
        return dt.date.fromisoformat(str(value or '')[:10])
    except ValueError:
        return None


def number(value):
    return int(float(value or 0))


def monthly_due(day, start, end):
    day = number(day)
    if not 1 <= day <= 31:
        return None
    month = start if day >= start.day else end
    return month.replace(day=min(day, calendar.monthrange(month.year, month.month)[1]))


def obligation(sub, charges, start, end):
    """Actual debit > scheduled renewal > package expiry > monthly payment day."""
    from mcp_dashboard_build import package_months, add_months
    price = number(sub.get('price'))
    months = package_months(str(sub.get('tariff') or ''), price)
    began = date(sub.get('subscription_start'))
    until = date(sub.get('subscription_end'))
    if months and began and (not until or (until - began).days > months * 32):
        until = add_months(began, months)
    if charges:
        latest = max(date(c['taken_at']) for c in charges)
        amount = sum(number(c['charge_amount']) for c in charges if date(c['taken_at']) == latest)
        return latest, amount, 'crm_debit', months, until
    renewal = date(sub.get('renewal_date'))
    if renewal and start <= renewal <= end:
        return renewal, number(sub.get('renewal_price')) or price, 'planned_renewal', months, until
    if months:
        return until, price, 'package_renewal', months, until
    return monthly_due(sub.get('pay_day'), start, end), price, 'monthly_day', 0, until


def financial_state(required, due, today, balance, charges, payments):
    """Cash receipts are visible, but old-debt receipts cannot prepay a renewal."""
    arrived = due <= today
    charged = sum(number(c['charge_amount']) for c in charges if date(c['taken_at']) == due)
    amount = sum(number(p['amount']) for p in payments)
    # A zero balance is settlement only after this cycle's debit exists.
    # Before that, require unconsumed credit covering the next obligation.
    paid = required > 0 and ((charged > 0 and balance >= 0) or
                             (charged == 0 and balance >= required))
    remaining = max(0, -balance) if charged else max(0, required - max(0, balance))
    if arrived and balance < 0:
        remaining = max(remaining, -balance)
    return dict(balance=balance, amount=amount, paid=paid,
                partial=0 < remaining < required, debt_amount=0 if paid else remaining,
                due_arrived=arrived, approved_overdue=arrived and not paid and remaining > 0,
                approved_status='paid' if paid else 'debt',
                payments=len(payments), last_paid=max((str(p['paid_date']) for p in payments), default=''),
                cycle_charge_amount=charged,
                current_cycle_taken=max((str(c['taken_at']) for c in charges if date(c['taken_at']) == due), default=''))


def merge_plan(live, baseline):
    """Keep members/obligations once enrolled, including subsequent archives."""
    by_id = {r['id']: dict(r) for r in live}
    for original in baseline:
        if original['id'] not in by_id:
            raise RuntimeError(f"CRM member refresh incomplete: {original['id']}")
        row = by_id[original['id']]
        actual_debit = row.get('obligation_source') == 'crm_debit'
        for field in PLAN_FIELDS:
            # A newly posted debit is authoritative when CRM moves the renewal
            # or upgrades it to a package. Keep the member and curator, while
            # applying the payment to the actual obligation in this cycle.
            if actual_debit and field in {'due', 'required', 'obligation_source'}:
                continue
            if field in original:
                row[field] = original[field]
    return sorted(by_id.values(), key=lambda r: (r['curator'], r['due'], r['id']))


def load_cycle(query, start, end, snapshot_dir):
    from mcp_dashboard_build import chunks, sql_ids, short_name
    from approved_cycle_plan import refresh_statuses
    today = min(end, dt.datetime.now(ZoneInfo('Asia/Tashkent')).date())
    after = end + dt.timedelta(days=1)
    path = Path(snapshot_dir) / f'{start}_{end}.json'
    baseline = json.loads(path.read_text())['records'] if path.exists() else []
    baseline_ids = {number(r['id']) for r in baseline}
    transfers = json.loads((Path(__file__).parent / 'approved-plans/cycle-transfers.json').read_text())
    seeds = {t['record']['id']: t['record'] for t in transfers
             if (t['to_start'], t['to_end']) == (str(start), str(end))}
    retained = baseline_ids | set(seeds)
    joined = sql_ids(sorted(retained)) if retained else '0'
    # Read all current curators, not the stale calendar-month debtors_plan.
    source = query(
        "SELECT s.ID sid,s.NAME student_name,s.ACTIVE student_active,s.CURRENT_BALANCE balance,"
        "sub.ID subscription_id,sub.STATUS subscription_status,sub.TYPE subscription_type,"
        "sub.START_DATE subscription_start,sub.END_OF_SUBSCRIPTION subscription_end,sub.DAY pay_day,"
        "sub.PRICE_TYPE tariff,COALESCE(NULLIF(sub.SPECIAL_PRICE,0),NULLIF(sub.STANDARD_PRICE_SNAPSHOT,0),"
        "CASE WHEN LOWER(sub.PRICE_TYPE) LIKE '%sarafan%' THEN "
        "(SELECT COALESCE(NULLIF(old.SPECIAL_PRICE,0),old.STANDARD_PRICE_SNAPSHOT) FROM subscribe_list old "
        "WHERE old.ORG_ID=6 AND old.STUDENT_ID=s.ID AND old.START_DATE<=NOW() "
        "AND (old.SPECIAL_PRICE>0 OR old.STANDARD_PRICE_SNAPSHOT>0) "
        "ORDER BY old.ID DESC LIMIT 1) END,0) price,"
        "sub.PAYMENT_STATUS payment_status,g.NAME group_name,g.ADMIN_ID admin_id,"
        "u.NAME admin_name,u.SURNAME admin_surname,u.TEAM_ID team_id,"
        "(SELECT MIN(f.START_DATE) FROM subscribe_list f WHERE f.ORG_ID=6 AND f.STUDENT_ID=s.ID "
        "AND f.STATUS<>'planned' AND f.TYPE<>'demo') first_subscription,"
        "renewal.START_DATE renewal_date,COALESCE(NULLIF(renewal.SPECIAL_PRICE,0),renewal.STANDARD_PRICE_SNAPSHOT,0) renewal_price,"
        "(SELECT MAX(l.LOG_DATE) FROM table_log l WHERE l.ITEM_ID=s.ID "
        "AND l.TABLE_NAME IN ('student_list','students') AND l.ACTION='delete_student' "
        "AND l.LOG_DATE<=NOW()) archived_at "
        "FROM student_list s JOIN subscribe_list sub ON sub.ID=(SELECT x.ID FROM subscribe_list x "
        "WHERE x.ORG_ID=6 AND x.STUDENT_ID=s.ID AND x.STATUS<>'planned' AND x.START_DATE<=NOW() "
        "ORDER BY x.ACTIVE DESC,x.ID DESC LIMIT 1) "
        "LEFT JOIN subscribe_list renewal ON renewal.ID=(SELECT p.ID FROM subscribe_list p "
        "WHERE p.ORG_ID=6 AND p.STUDENT_ID=s.ID AND p.STATUS='planned' "
        f"AND p.START_DATE>='{start}' AND p.START_DATE<'{after}' ORDER BY p.START_DATE,p.ID LIMIT 1) "
        "LEFT JOIN group_list g ON g.ID=sub.GROUP_ID LEFT JOIN gl_sys_users u ON u.ID=g.ADMIN_ID "
        f"WHERE s.ORG_ID=6 AND (g.ADMIN_ID IN ({sql_ids(sorted(ADMINS))}) OR s.ID IN ({joined})) "
        "HAVING subscription_status IN ('active','freezed') "
        f"OR archived_at>='{start}' OR sid IN ({joined}) "
        "OR EXISTS (SELECT 1 FROM transaction_list t WHERE t.STUDENT_ID=sid AND t.ORG_ID=6 "
        f"AND t.ACTION_TYPE='taken' AND t.TRANSACTION_DATE>='{start}' "
        f"AND t.TRANSACTION_DATE<'{after}' AND t.TRANSACTION_DATE<=NOW())")
    source_by_id = {number(s['sid']): s for s in source}
    if not source or not retained <= set(source_by_id):
        raise RuntimeError('CRM collection roster is empty/incomplete; refusing publication')
    charges, payments = {}, {}
    for part in chunks(source_by_id):
        for t in query(
            "SELECT ID,STUDENT_ID sid,ACTION_TYPE action,AMOUNT amount,TRANSACTION_DATE occurred "
            "FROM transaction_list WHERE ORG_ID=6 "
            f"AND STUDENT_ID IN ({sql_ids(part)}) AND TRANSACTION_DATE>='{start}' "
            f"AND TRANSACTION_DATE<'{after}' AND TRANSACTION_DATE<=NOW() ORDER BY TRANSACTION_DATE,ID"):
            sid = number(t['sid'])
            if t['action'] == 'taken' and number(t['amount']) > 0:
                charges.setdefault(sid, []).append(dict(taken_at=t['occurred'], charge_amount=t['amount'], id=t['ID']))
            elif t['action'] == 'add' and number(t['amount']) > 0:
                payments.setdefault(sid, []).append(dict(paid_date=t['occurred'], amount=t['amount'], id=t['ID']))
    rows = []
    excluded = dict(demo=0, first_month=0, outside_cycle=0)
    for sid, sub in source_by_id.items():
        if sid not in retained and (sub['subscription_status'] == 'demo' or sub['subscription_type'] == 'demo'):
            excluded['demo'] += 1
            continue
        due, required, evidence, months, until = obligation(sub, charges.get(sid, []), start, end)
        first = date(sub['first_subscription'])
        if sid not in retained:
            if not due or not start <= due <= end or required <= 0:
                excluded['outside_cycle'] += 1
                continue
            if first and (due - first).days < 20:
                excluded['first_month'] += 1
                continue
        full_admin = (str(sub.get('admin_name') or '').strip() + ' ' + str(sub.get('admin_surname') or '').strip()).strip()
        row = dict(id=sid, name=sub['student_name'], admin_id=number(sub['admin_id']),
                   curator=short_name(full_admin), admin_full=full_admin,
                   team='A' if number(sub['team_id']) == 2 or number(sub['admin_id']) == 24241 else 'B',
                   group=sub['group_name'] or 'Guruhsiz', due=str(due or start), required=required,
                   price=number(sub['price']), tariff=sub.get('tariff') or '', upsell='',
                   is_package=months > 0, package_months=months, package_until=str(until or ''),
                   subscription_status=sub['subscription_status'], payment_status=sub['payment_status'],
                   student_active=bool(number(sub['student_active'])), has_subscription=True,
                   subscription_start=str(sub['subscription_start'])[:10], first_subscription_start=str(first or ''),
                   is_referral=False, delayed=False, obligation='current', obligation_source=evidence)
        if sid in seeds and sid not in baseline_ids:
            for field in PLAN_FIELDS:
                if field in seeds[sid]:
                    row[field] = seeds[sid][field]
        rows.append(row)
    rows = merge_plan(rows, baseline)
    for row in rows:
        sid = row['id']
        row.update(financial_state(row['required'], date(row['due']), today,
                                   number(source_by_id[sid]['balance']), charges.get(sid, []), payments.get(sid, [])))
    plan = dict(cycle_start=str(start), cycle_end=str(end), records=rows)
    rows = refresh_statuses(plan, rows, query, str(today))
    upsells = load_package_sales(query, start, end)
    collections = [dict(date=str(r['paid_date'])[:10], amount=number(r['amount'])) for r in query(
        "SELECT DATE(TRANSACTION_DATE) paid_date,SUM(AMOUNT) amount FROM transaction_list "
        "WHERE ORG_ID=6 AND ACTION_TYPE='add' AND STUDENT_TYPE='student' "
        f"AND TRANSACTION_DATE>='{start}' AND TRANSACTION_DATE<'{after}' AND TRANSACTION_DATE<=NOW() "
        "GROUP BY DATE(TRANSACTION_DATE) ORDER BY paid_date")]
    now = dt.datetime.now(dt.timezone.utc)
    audit = dict(source='crm_cycle', source_students=len(source), plan_locked_students=len(rows),
                 excluded=excluded, generated=now.strftime('%d.%m.%Y %H:%M UTC'), generated_iso=now.isoformat(timespec='seconds'),
                 today=str(today), collected_total=sum(r['amount'] for r in collections), upsell_sales=len(upsells))
    # Save only after every CRM query/financial classification succeeds.
    path.parent.mkdir(exist_ok=True)
    payload = dict(cycle_start=str(start), cycle_end=str(end), created_at=now.isoformat(), records=rows)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(payload, ensure_ascii=False, separators=(',', ':')) + '\n')
    temp.replace(path)
    return rows, audit, upsells, collections


def package_completed(charge, receipts):
    """Return the receipt date completing a specific package, including splits."""
    from upsell_installments import completion_date
    price, credit = number(charge['package_price']), number(charge['prior_balance'])
    charged = str(charge['charge_date'])
    # Prepayment does not become another sale when CRM later creates its debit.
    if credit >= price:
        prior = [p for p in receipts if number(p['amount']) > 0 and (
            str(p['paid_date']) < charged or (str(p['paid_date']) == charged and
            number(p.get('ID')) < number(charge.get('charge_id'))))]
        return max((str(p['paid_date']) for p in prior), default='')
    if credit < 0 and number(charge['live_balance']) >= 0:
        credit = 0  # Legacy ledger omitted a reversal; authoritative balance is settled.
    after_charge = [p for p in receipts if str(p['paid_date']) > charged or (
        str(p['paid_date']) == charged and number(p.get('ID')) >= number(charge.get('charge_id')))]
    return completion_date(price, credit, charged, after_charge)


def load_package_sales(query, start, end):
    """A large receipt alone is not a package: require a package debit as evidence."""
    from mcp_dashboard_build import chunks, sql_ids, short_name, package_months
    after = end + dt.timedelta(days=1)
    candidates = query(
        "SELECT t.ID charge_id,t.STUDENT_ID sid,s.NAME student_name,s.CURRENT_BALANCE live_balance,"
        "t.AMOUNT package_price,t.TRANSACTION_DATE charge_date,sub.PRICE_TYPE tariff,"
        "u.NAME admin_name,u.SURNAME admin_surname,"
        "s.CURRENT_BALANCE-(SELECT COALESCE(SUM(CASE WHEN h.ACTION_TYPE='add' THEN h.AMOUNT "
        "WHEN h.ACTION_TYPE='taken' THEN -h.AMOUNT ELSE 0 END),0) FROM transaction_list h "
        "WHERE h.ORG_ID=6 AND h.STUDENT_ID=t.STUDENT_ID AND h.TRANSACTION_DATE<=NOW() AND "
        "(h.TRANSACTION_DATE>t.TRANSACTION_DATE OR "
        "(h.TRANSACTION_DATE=t.TRANSACTION_DATE AND h.ID>=t.ID))) prior_balance "
        "FROM transaction_list t JOIN student_list s ON s.ID=t.STUDENT_ID AND s.ORG_ID=t.ORG_ID "
        "LEFT JOIN subscribe_list sub ON sub.ID=(SELECT x.ID FROM subscribe_list x "
        "WHERE x.ORG_ID=6 AND x.STUDENT_ID=s.ID AND x.STATUS<>'planned' AND x.START_DATE<=NOW() "
        "ORDER BY x.ACTIVE DESC,x.ID DESC LIMIT 1) "
        "LEFT JOIN group_list g ON g.ID=sub.GROUP_ID LEFT JOIN gl_sys_users u ON u.ID=g.ADMIN_ID "
        "WHERE t.ORG_ID=6 AND t.ACTION_TYPE='taken' AND t.AMOUNT>=900000 "
        f"AND t.TRANSACTION_DATE<'{after}' AND t.TRANSACTION_DATE<=NOW() "
        f"AND g.ADMIN_ID IN ({sql_ids(sorted(ADMINS))}) "
        "AND EXISTS (SELECT 1 FROM transaction_list p WHERE p.ORG_ID=6 AND p.STUDENT_ID=t.STUDENT_ID "
        f"AND p.ACTION_TYPE='add' AND p.AMOUNT>0 AND p.TRANSACTION_DATE>='{start}' "
        f"AND p.TRANSACTION_DATE<'{after}' AND p.TRANSACTION_DATE<=NOW()) "
        "AND NOT EXISTS (SELECT 1 FROM transaction_list n WHERE n.ORG_ID=6 AND n.STUDENT_ID=t.STUDENT_ID "
        "AND n.ACTION_TYPE='taken' AND n.AMOUNT>=900000 AND "
        "(n.TRANSACTION_DATE>t.TRANSACTION_DATE OR (n.TRANSACTION_DATE=t.TRANSACTION_DATE AND n.ID>t.ID)) "
        f"AND n.TRANSACTION_DATE<'{after}' AND n.TRANSACTION_DATE<=NOW())")
    receipts = {}
    for part in chunks({number(c['sid']) for c in candidates}):
        for r in query("SELECT ID,STUDENT_ID sid,AMOUNT amount,TRANSACTION_DATE paid_date FROM transaction_list "
                       f"WHERE ORG_ID=6 AND ACTION_TYPE='add' AND STUDENT_ID IN ({sql_ids(part)}) "
                       f"AND TRANSACTION_DATE<'{after}' AND TRANSACTION_DATE<=NOW() ORDER BY TRANSACTION_DATE,ID"):
            receipts.setdefault(number(r['sid']), []).append(r)
    sales = []
    for c in candidates:
        sid = number(c['sid'])
        closed = package_completed(c, receipts.get(sid, []))
        if not closed or not str(start) <= closed[:10] <= str(end):
            continue
        full_admin = (str(c.get('admin_name') or '') + ' ' + str(c.get('admin_surname') or '')).strip()
        sales.append(dict(id=sid, name=c['student_name'], curator=short_name(full_admin),
                          months=package_months(str(c['tariff'] or ''), number(c['package_price'])),
                          tariff=c['tariff'] or '', amount=number(c['package_price']),
                          sale_date='.'.join(closed[:10].split('-')[::-1]), crm_charge_id=number(c['charge_id'])))
    return sales
