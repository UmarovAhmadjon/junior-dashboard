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
        if row.get("paid") and int(row.get("debt_amount") or 0) > 0:
            raise RuntimeError(f"Paid student still has debt in {path}: {row['id']}")
        if status(row) == "debt" and row.get("subscription_status") == "freezed":
            raise RuntimeError(f"Frozen student shown as debtor in {path}: {row['id']}")
    return {int(row["id"]): row for row in rows}


current = validate("index.html")
upsell_ids = {int(row["id"]) for row in embedded_rows("index.html", "upsell-data")}
for student_id in (11198, 11668):
    if student_id not in upsell_ids:
        raise RuntimeError(f"Confirmed Upsell sale missing: id={student_id}")
if 16746 in upsell_ids:
    raise RuntimeError("Ordinary monthly payment incorrectly shown as Upsell: id=16746")
current_confirmed = current.get(14857)
if not current_confirmed or status(current_confirmed) != "archive":
    actual = "missing" if not current_confirmed else status(current_confirmed)
    raise RuntimeError(
        f"Confirmed current-cycle archive regressed: id=14857, status={actual}")
archive_path = "index-cycle-2026-09-24.html"
archive = validate(archive_path)
if len(archive) != 1426:
    raise RuntimeError(f"Confirmed archive plan changed: {len(archive)} != 1426")
if 19167 in archive:
    raise RuntimeError("First-cycle debit incorrectly entered previous cycle: id=19167")
if 19167 not in current:
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

# These students froze/owed in the previous cycle and only became archived in
# the current one. They must remain in the previous cycle without appearing in
# both plans.
for student_id in (18903, 18909):
    if student_id not in archive:
        raise RuntimeError(f"Previous-cycle student disappeared: id={student_id}")
    if student_id in current:
        raise RuntimeError(f"Cross-cycle archive duplicate returned: id={student_id}")

# User-confirmed regression cases. These assertions intentionally block a bad
# auto-update from publishing while still allowing debt to close as paid.
expected = {
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
