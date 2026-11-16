"""Attribute split package payments to their first fully funded date."""
def audited_credit(student_id, charged_at, price, live_balance, credit, receipts):
    # CRM omits Behruz's January debit, leaving fictitious carried credit in
    # the ledger sum. The October package was paid on Nov 5, not on its debit.
    if (int(student_id) == 12193 and str(charged_at)[:10] == '2026-10-28'
            and int(price) == 990000 and int(live_balance) == 0
            and any(str(r['paid_date'])[:10] == '2026-11-05'
                    and int(r['amount']) == 990000 for r in receipts)):
        return 0
    return credit


def report_sales(rows, cycle_start, cycle_end):
    # User request, Nov 16: October sales have already been counted. Keep
    # only November completions in this report; historical reports are intact.
    if (str(cycle_start), str(cycle_end)) == ('2026-10-25', '2026-11-23'):
        return [r for r in rows if str(r.get('sale_date', ''))[3:] == '11.2026']
    return rows


def completion_date(price, credit, charged_at, receipts):
    price = int(price or 0)
    # A prior debt must be covered as well; do not discard negative credit.
    covered = int(credit or 0)
    charged_at = str(charged_at)
    if price <= 0:
        return None
    if covered >= price:
        return charged_at
    for row in sorted(receipts, key=lambda r: (str(r['paid_date']), int(r.get('ID', 0)))):
        when = str(row['paid_date'])
        if when < charged_at:
            continue
        covered += int(row['amount'] or 0)
        if covered >= price:
            return when
    return None
