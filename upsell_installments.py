"""Attribute split package payments to their first fully funded date."""
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
