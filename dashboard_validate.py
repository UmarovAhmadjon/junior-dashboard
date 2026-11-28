#!/usr/bin/env python3
"""Fail publication when confirmed dashboard business rules regress."""
import json
import os
import re


def records(path):
    with open(path, encoding="utf-8") as handle:
        match = re.search(
            r'<script id="data" type="application/json">(.*?)</script>',
            handle.read(), re.S)
    if not match:
        raise RuntimeError(f"Missing dashboard data: {path}")
    return json.loads(match.group(1))


def embedded_rows(path, element_id):
    with open(path, encoding="utf-8") as handle:
        match = re.search(
            rf'<script id="{element_id}" type="application/json">(.*?)</script>',
            handle.read(), re.S)
    if not match:
        raise RuntimeError(f"Missing {element_id}: {path}")
    return json.loads(match.group(1))


def status(row):
    if row.get('approved_status'):
        return row['approved_status']
    bucket = str(row.get("analytics_bucket") or "").lower()
    if bucket == "bit":
        return "bit"
    if (bucket == "referral" or row.get("is_referral") or
            str(row.get("tariff") or "").strip().upper() == "SARAFAN RADIO"):
        return "referral"
    if row.get("is_package") or row.get("paid"):
        return "paid"
    if bucket == "frozen" or row.get("subscription_status") == "freezed":
        return "frozen"
    if bucket == "deleted" or row.get("subscription_status") != "active":
        return "archive"
    return "debt"


def validate(path):
    rows = records(path)
    ids = [int(row["id"]) for row in rows]
    if len(ids) != len(set(ids)):
        raise RuntimeError(f"Duplicate students in {path}")
    for row in rows:
        if row.get("paid") and (int(row.get("debt_amount") or 0) > 0 or row.get('approved_overdue')):
            raise RuntimeError(f"Paid student still has debt in {path}: {row['id']}")
        if (status(row) == "debt" and row.get("subscription_status") == "freezed"
                and not row.get('approved_overdue')
                and row.get('due_arrived', True)
                and not (row.get('november_one_month') and not row.get('due_arrived'))):
            raise RuntimeError(f"Frozen student shown as debtor in {path}: {row['id']}")
    return {int(row["id"]): row for row in rows}


current = validate("index.html")
with open("index.html", encoding="utf-8") as handle:
    document = handle.read()
period = json.loads(re.search(r"const periods=(\{.*?\});", document)[1])["all"][:2]
# Source and destination must publish together, including when the archive
# refresh fails: never drop transferred sales or restore the legacy duplicate.
import upsell_cycle_transfers
for transfer in upsell_cycle_transfers.load():
    target_path = f"index-cycle-{transfer['to_start']}.html"
    if period == [transfer['to_start'], transfer['to_end']]:
        target_path = 'index.html'
    target_sales = embedded_rows(target_path, 'upsell-data')
    for expected_sale in transfer['sales']:
        matches = [row for row in target_sales
                   if upsell_cycle_transfers.sale_key(row) ==
                   upsell_cycle_transfers.sale_key(expected_sale)]
        if len(matches) != 1 or any(
                matches[0].get(key) != value for key, value in expected_sale.items()):
            raise RuntimeError(f"Approved Upsell transfer missing/changed: {expected_sale['id']}")
    for alias in transfer.get('legacy_replacements', []):
        if any(not row.get('crm_charge_id') and
               all(row.get(key) == value for key, value in alias.items())
               for row in target_sales):
            raise RuntimeError(f"Transferred Upsell still duplicated in archive: {alias['id']}")
    source_path = f"index-cycle-{transfer['from_start']}.html"
    if period == [transfer['from_start'], transfer['from_end']]:
        source_path = 'index.html'
    if os.path.isfile(source_path):
        moved_keys = {upsell_cycle_transfers.sale_key(row) for row in transfer['sales']}
        if any(upsell_cycle_transfers.sale_key(row) in moved_keys
               for row in embedded_rows(source_path, 'upsell-data')):
            raise RuntimeError('Transferred Upsell returned to its original cycle')
november = period == ['2026-10-25', '2026-11-23']
new_cycle = period[0] >= '2026-11-24'
approved_current = november and bool(current) and all('approved_status' in r for r in current.values())
if new_cycle:
    import datetime as dt
    import mcp_dashboard_build as dashboard
    start, end = map(dt.date.fromisoformat, period)
    if start.day != 24 or end != dashboard.cycle_end_for_start(start) or end.day != 23:
        raise RuntimeError(f'Incorrect cycle boundaries: {period}')
    snapshot = json.load(open(f'cycle-snapshots/{start}_{end}.json', encoding='utf-8'))
    if set(current) != {r['id'] for r in snapshot['records']}:
        raise RuntimeError('Current cycle membership differs from snapshot')
    for row in current.values():
        if not str(start) <= row['due'] <= str(end):
            raise RuntimeError(f'Out-of-cycle obligation: {row["id"]}')
        if row.get('november_one_month') or row.get('plan_archived_unpaid'):
            raise RuntimeError(f'November-only exception leaked: {row["id"]}')
        if row['approved_status'] == 'paid' and not row['cycle_charge_amount'] and row['balance'] < row['required']:
            raise RuntimeError(f'Payment lacks debit/prepayment evidence: {row["id"]}')
    for sale in embedded_rows('index.html', 'upsell-data'):
        closed = dt.datetime.strptime(sale['sale_date'], '%d.%m.%Y').date()
        if not start <= closed <= end or not sale.get('crm_charge_id'):
            raise RuntimeError(f'Out-of-cycle or unverified Upsell: {sale["id"]}')
    for cash in embedded_rows('index.html', 'collection-data'):
        if not str(start) <= cash['date'] <= str(end):
            raise RuntimeError('Out-of-cycle cash receipt')
    if str(start) == '2026-11-24':
        for sid in (14034, 20647):
            row = current.get(sid, {})
            if row.get('due') != '2026-11-28' and row.get('obligation_source') != 'crm_debit':
                raise RuntimeError(f'Confirmed November 28 transfer missing: {sid}')
    previous = validate('index-cycle-2026-10-25.html')
    if len(previous) != 1605:
        raise RuntimeError('Published November archive membership changed')
if approved_current:
    import approved_cycle_plan
    approved = approved_cycle_plan.load('2026-10-25', '2026-11-23')
    if set(current) != {r['id'] for r in approved['records']}:
        raise RuntimeError('Approved cycle membership changed')
    for name, (total, debt) in approved['expected'].items():
        selected = [r for r in current.values() if r['curator'] == name]
        if len(selected) != total:
            raise RuntimeError(f'Approved curator counts changed: {name}')
if 20291 in current:
    raise RuntimeError("Active demo student entered current debt plan: id=20291")
upsell_ids = {int(row["id"]) for row in embedded_rows("index.html", "upsell-data")}
if approved_current:
    current_sales = embedded_rows("index.html", "upsell-data")
    if any(row['sale_date'][3:] != '11.2026' for row in current_sales):
        raise RuntimeError('October Upsell returned to November report')
    if 12193 not in upsell_ids:
        raise RuntimeError('Confirmed November 5 Upsell missing: id=12193')
if 16746 in upsell_ids:
    raise RuntimeError("Ordinary monthly payment incorrectly shown as Upsell: id=16746")
current_confirmed = current.get(14857)
if november and not approved_current and (not current_confirmed or status(current_confirmed) != "archive"):
    actual = "missing" if not current_confirmed else status(current_confirmed)
    raise RuntimeError(
        f"Confirmed current-cycle archive regressed: id=14857, status={actual}")
archive_path = "index-cycle-2026-09-24.html"
archive = validate(archive_path)
archive_upsell_ids = {
    int(row["id"]) for row in embedded_rows(archive_path, "upsell-data")
}
if 17036 not in archive_upsell_ids:
    raise RuntimeError("Carried-credit Upsell sale missing: id=17036")
if len(archive) != 1426:
    raise RuntimeError(f"Confirmed archive plan changed: {len(archive)} != 1426")
if 19167 in archive:
    raise RuntimeError("First-cycle debit incorrectly entered previous cycle: id=19167")
if november and not approved_current and 19167 not in current:
    raise RuntimeError("Repeat debit missing from current cycle: id=19167")
for student_id in (19020, 19077):
    if student_id not in archive:
        raise RuntimeError(f"Confirmed second-charge student missing: id={student_id}")

# A cycle can contain both the previous debit and the next monthly debit.
# Only the latest obligation date may be counted; otherwise one payment is
# incorrectly shown as partial debt (the regression reported for this student).
suxrob = archive.get(16746)
if (not suxrob or status(suxrob) != "paid" or
        int(suxrob.get("required") or 0) != 350000 or
        int(suxrob.get("debt_amount") or 0) != 0):
    raise RuntimeError(f"Latest-debit regression returned: id=16746, row={suxrob}")

for student_id in (14857, 16534, 18921):
    row = archive.get(student_id)
    if not row or status(row) != "archive":
        actual = "missing" if not row else status(row)
        raise RuntimeError(
            f"Inactive archived student returned as debtor: id={student_id}, status={actual}")

# These students froze/owed in the previous cycle and only became archived in
# the current one. They must remain in the previous cycle without appearing in
# both plans.
for student_id in (18903, 18909):
    if student_id not in archive:
        raise RuntimeError(f"Previous-cycle student disappeared: id={student_id}")
    if november and student_id in current:
        raise RuntimeError(f"Cross-cycle archive duplicate returned: id={student_id}")

# User-confirmed regression cases. These assertions intentionally block a bad
# auto-update from publishing while still allowing debt to close as paid.
expected = {
    5561: {"bit"}, 10690: {"bit"}, 14518: {"bit"},  # Marjona historical fact
    12180: {"paid", "bit", "referral"},       # Boboxonova Rayxona
    19029: {"paid", "bit", "referral"},       # Shahobiddinov Muhammadqodir
    14479: {"paid", "bit", "referral"},       # Bozorboyev Jahongir
    7201: {"bit"}, 7363: {"bit"}, 8880: {"bit"}, 8893: {"bit"},
    9660: {"bit"}, 11198: {"bit"}, 13818: {"bit"},
    17716: {"frozen", "paid", "bit", "referral"},  # Erkinov Ikromjon
}
for student_id, allowed in expected.items():
    row = archive.get(student_id)
    if not row or status(row) not in allowed:
        actual = "missing" if not row else status(row)
        raise RuntimeError(
            f"Confirmed case regressed: id={student_id}, status={actual}, allowed={sorted(allowed)}")

closing_path = "cycle-snapshots/2026-09-24_2026-10-24-closing.json"
if os.path.isfile(closing_path):
    with open(closing_path, encoding="utf-8") as handle:
        closing = json.load(handle).get("records", [])
    closing_fact = sum(status(row) in {"paid", "bit", "referral"} for row in closing)
    archive_fact = sum(status(row) in {"paid", "bit", "referral"} for row in archive.values())
    if archive_fact < closing_fact:
        raise RuntimeError(f"Archive fact decreased: {archive_fact} < {closing_fact}")
    archive_by_id = archive
    for row in closing:
        closing_status = status(row)
        if closing_status not in {"bit", "referral"}:
            continue
        student_id = int(row["id"])
        archived = archive_by_id.get(student_id)
        if not archived or status(archived) != closing_status:
            actual = "missing" if not archived else status(archived)
            raise RuntimeError(
                f"Final cycle status regressed: id={student_id}, "
                f"{closing_status} -> {actual}")

print(f"Validated current={len(current)}, archive={len(archive)}")
