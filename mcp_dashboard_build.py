#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Local-only MCP prototype. It never writes to the production dashboard."""
import datetime as dt
import calendar
import glob
import html
import json
import os
import re
import shutil
import subprocess
import time
import urllib.request
from zoneinfo import ZoneInfo

GATEWAY = os.environ.get("JUNIOR_MCP_GATEWAY", "https://myclinic.agc.uz/new_junior_mcp.php")
ORG_ID = 6
BUSINESS_TZ = ZoneInfo("America/Los_Angeles")


def month_end(value):
    return value.replace(day=calendar.monthrange(value.year, value.month)[1])


def cycle_end_for_start(start):
    # A cycle contains the last seven days of the previous month and
    # all days of the next month except its final seven days.
    next_month = (start.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
    return month_end(next_month) - dt.timedelta(days=7)


def current_cycle(today=None):
    today = today or dt.datetime.now(BUSINESS_TZ).date()
    this_month = today.replace(day=1)
    previous_end = this_month - dt.timedelta(days=1)
    start = previous_end - dt.timedelta(days=6)
    end = month_end(this_month) - dt.timedelta(days=7)
    if today <= end:
        return start, end
    next_month = (this_month.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
    return month_end(this_month) - dt.timedelta(days=6), month_end(next_month) - dt.timedelta(days=7)


def cycle_periods(start, end):
    first_next = (start.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
    cuts = [(start, min(first_next - dt.timedelta(days=1), end))]
    cursor = cuts[-1][1] + dt.timedelta(days=1)
    while cursor <= end and len(cuts) < 4:
        finish = end if len(cuts) == 3 else min(cursor + dt.timedelta(days=6), end)
        cuts.append((cursor, finish))
        cursor = finish + dt.timedelta(days=1)
    return cuts


REQUESTED_CYCLE = os.environ.get("DASHBOARD_CYCLE_START", "").strip()
if REQUESTED_CYCLE:
    CYCLE_START = dt.date.fromisoformat(REQUESTED_CYCLE)
    CYCLE_END = cycle_end_for_start(CYCLE_START)
else:
    CYCLE_START, CYCLE_END = current_cycle()
CYCLE_WEEKS = cycle_periods(CYCLE_START, CYCLE_END)
TEST_ADMINS = {21453}
CURRENT_ADMINS = {13799, 14974, 14241, 14451, 16386, 16005, 21463}
OUT = os.environ.get("MCP_TEST_OUT", os.path.join(os.path.dirname(__file__), "index.html"))
SITE_LABEL = os.environ.get("DASHBOARD_LABEL", "MCP TEST")
ANALYTICS_URL = "https://umarovahmadjon.github.io/junior-dashboard/index.html"
SNAPSHOT_DIR = os.path.join(os.path.dirname(__file__), "cycle-snapshots")
PLAN_FIELDS = (
    "id", "name", "admin_id", "curator", "admin_full", "team", "group", "upsell",
    "due", "required", "price", "tariff", "is_package", "package_months", "package_until",
)
# The current cycle started before immutable snapshots were introduced. Seed it
# from the last accepted 1,384-student plan; subsequent cycles seed themselves.
LEGACY_SNAPSHOT_COMMITS = {"2026-09-24": "fa69f142"}
LEGACY_ARCHIVE_COMMITS = {"2026-09-24": "b6ef9031"}
LEGACY_CLOSING_COMMITS = {"2026-09-24": "fa85707d"}
# User-confirmed cycle results are durable checkpoints. CRM can later expose a
# generic paid/frozen state after a receipt or subscription change; that must
# not rewrite an already confirmed Bitirdi/Referral result in an old cycle.
CONFIRMED_ARCHIVE_BUCKETS = {
    "2026-09-24": {
        7201: "bit",   # Shadimuratov Nurlibek
        7363: "bit",   # Ismoilov Ismoiljon
        8880: "bit",   # Botirov Boburjon
        8893: "bit",   # Xayrullayeva Parizoda
        9660: "bit",   # Soatmo'minov Asliddin
        11198: "bit",  # Rahmataliyev Muhammadrizo
        13818: "bit",  # Ma'ribjonova Oydinoy
        17716: "frozen",  # Erkinov Ikromjon
    },
}
# Students confirmed as members of an open cycle remain in its frozen plan
# even if CRM later removes/freezes/archives them.
CONFIRMED_CYCLE_MEMBER_IDS = {
    "2026-10-25": {14857},  # Ibragimov Behzodbek
}


def query(sql):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                       "params": {"name": "query_db", "arguments": {"sql": sql}}}).encode()
    last_error = None
    for attempt in range(4):
        try:
            req = urllib.request.Request(GATEWAY, data=body, headers={"Content-Type": "application/json"})
            raw = json.loads(urllib.request.urlopen(req, timeout=180).read().decode())
            break
        except Exception as error:
            last_error = error
            if attempt == 3:
                raise RuntimeError(f"MCP query failed after 4 attempts: {last_error}") from error
            time.sleep(5 * (attempt + 1))
    text = raw["result"]["content"][0]["text"]
    payload = json.loads(text)
    rows = payload.get("data", {}).get("data", [])
    if rows in ("empty", ["empty"]):
        return []
    return rows if isinstance(rows, list) else [rows]


def chunks(values, size=300):
    values = list(values)
    for pos in range(0, len(values), size):
        yield values[pos:pos + size]


def freeze_cycle_plan(live_records, mandatory_cycle_ids=None):
    """Keep cycle membership and plan fields fixed; refresh only live results."""
    mandatory_cycle_ids = set(mandatory_cycle_ids or ())
    os.makedirs(SNAPSHOT_DIR, exist_ok=True)
    snapshot_path = os.path.join(
        SNAPSHOT_DIR, f"{CYCLE_START.isoformat()}_{CYCLE_END.isoformat()}.json")
    created = False
    if os.path.isfile(snapshot_path):
        with open(snapshot_path, encoding="utf-8") as handle:
            payload = json.load(handle)
        baseline = payload["records"]
    else:
        baseline = live_records
        seed_commit = LEGACY_SNAPSHOT_COMMITS.get(CYCLE_START.isoformat())
        if seed_commit:
            try:
                old_html = subprocess.check_output(
                    ["git", "show", f"{seed_commit}:index.html"], text=True)
                match = re.search(
                    r'<script id="data" type="application/json">(.*?)</script>',
                    old_html, re.S)
                if match:
                    baseline = json.loads(match.group(1))
            except (OSError, subprocess.CalledProcessError, json.JSONDecodeError):
                pass
        payload = {
            "cycle_start": CYCLE_START.isoformat(),
            "cycle_end": CYCLE_END.isoformat(),
            "created_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "records": baseline,
        }
        with open(snapshot_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
            handle.write("\n")
        created = True

    due_dates_corrected = 0
    for base in baseline:
        raw_due = str(base.get("due") or "")[:10]
        try:
            payment_day = dt.date.fromisoformat(raw_due).day
        except ValueError:
            continue
        corrected_due = due_date(payment_day).isoformat()
        if raw_due != corrected_due:
            base["due"] = corrected_due
            due_dates_corrected += 1

    live_by_id = {int(row["id"]): row for row in live_records}
    baseline_ids = {int(row["id"]) for row in baseline}
    confirmed_restored = 0
    required_ids = (CONFIRMED_CYCLE_MEMBER_IDS.get(CYCLE_START.isoformat(), set()) |
                    mandatory_cycle_ids)
    mandatory_due_corrected = 0
    for student_id in required_ids:
        if student_id not in baseline_ids and student_id in live_by_id:
            baseline.append(dict(live_by_id[student_id]))
            baseline_ids.add(student_id)
            confirmed_restored += 1
        elif (student_id in mandatory_cycle_ids and student_id in live_by_id and
              student_id in baseline_ids):
            base = next(row for row in baseline if int(row["id"]) == student_id)
            live_due = str(live_by_id[student_id].get("due") or "")[:10]
            if live_due and str(base.get("due") or "")[:10] != live_due:
                base["due"] = live_due
                mandatory_due_corrected += 1
    frozen = []
    for base in baseline:
        row = dict(live_by_id.get(int(base["id"]), base))
        for field in PLAN_FIELDS:
            if field in base:
                row[field] = base[field]
        frozen.append(row)
    # An inactive student without a charge in this cycle belongs to an older
    # cycle even if an old negative balance is still present.  Keep explicit
    # Bitirdi/Referral cases because business rules count them in plan+fact.
    def belongs_to_cycle(row):
        bucket = str(row.get("analytics_bucket") or "").lower()
        special = (bucket in {"bit", "referral"} or row.get("is_referral") or
                   str(row.get("tariff") or "").strip().upper() == "SARAFAN RADIO")
        due = str(row.get("due") or "")[:10]
        in_cycle = CYCLE_START.isoformat() <= due <= CYCLE_END.isoformat()
        return in_cycle and (special or bool(row.get("student_active", True)) or
                             bool(row.get("current_cycle_taken")))

    # Once a cycle is archived its membership is immutable. Later CRM
    # deactivation/archive changes only the status, never the frozen plan.
    corrected = ([row for row in frozen if belongs_to_cycle(row)]
                 if created else frozen)
    excluded = len(frozen) - len(corrected)
    if excluded or due_dates_corrected or confirmed_restored or mandatory_due_corrected:
        kept_ids = {int(row["id"]) for row in corrected}
        payload["records"] = [row for row in baseline if int(row["id"]) in kept_ids]
        payload["corrected_at"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        payload["excluded_previous_cycle"] = excluded
        payload["due_dates_corrected"] = due_dates_corrected
        payload["confirmed_members_restored"] = confirmed_restored
        payload["mandatory_due_corrected"] = mandatory_due_corrected
        with open(snapshot_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
            handle.write("\n")
    return corrected, snapshot_path, created, excluded, due_dates_corrected


def remove_previous_cycle_archive_duplicates(records, snapshot_path):
    """Keep an old obligation in its original cycle, not again as current archive."""
    if REQUESTED_CYCLE:
        return records, 0
    previous_files = []
    for path in glob.glob(os.path.join(SNAPSHOT_DIR, "*-closing.json")):
        try:
            with open(path, encoding="utf-8") as handle:
                payload = json.load(handle)
            end = dt.date.fromisoformat(str(payload.get("cycle_end") or ""))
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        if end < CYCLE_START:
            previous_files.append((end, path, payload.get("records", [])))
    if not previous_files:
        return records, 0
    _, _, previous = max(previous_files, key=lambda item: item[0])
    previous_by_id = {int(row["id"]): row for row in previous}
    protected = CONFIRMED_CYCLE_MEMBER_IDS.get(CYCLE_START.isoformat(), set())

    def is_duplicate(row):
        student_id = int(row["id"])
        old = previous_by_id.get(student_id)
        if student_id in protected or not old:
            return False
        archived = (not bool(row.get("student_active", True)) or
                    str(row.get("subscription_status") or "").lower() == "archive")
        no_current_charge = not bool(row.get("current_cycle_taken"))
        old_obligation = (bool(old.get("current_cycle_taken")) or
                          int(old.get("debt_amount") or 0) > 0 or
                          str(old.get("analytics_bucket") or "").lower() in
                          {"debt", "frozen", "bit", "referral", "deleted"})
        return archived and no_current_charge and old_obligation

    corrected = [row for row in records if not is_duplicate(row)]
    removed = len(records) - len(corrected)
    if removed:
        kept_ids = {int(row["id"]) for row in corrected}
        with open(snapshot_path, encoding="utf-8") as handle:
            payload = json.load(handle)
        payload["records"] = [row for row in payload.get("records", [])
                              if int(row["id"]) in kept_ids]
        payload["previous_cycle_archive_duplicates_removed"] = removed
        payload["corrected_at"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        with open(snapshot_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
            handle.write("\n")
    return corrected, removed


def apply_late_payments_to_archived_cycle(records, snapshot_path):
    """Apply post-cycle receipts to the cycle's frozen outstanding debt."""
    if not REQUESTED_CYCLE or CYCLE_END >= dt.datetime.now(BUSINESS_TZ).date():
        return records, 0
    closing_path = snapshot_path.replace(".json", "-closing.json")
    if os.path.isfile(closing_path):
        with open(closing_path, encoding="utf-8") as handle:
            baseline = json.load(handle).get("records", [])
    else:
        baseline = []
        seed_commit = LEGACY_CLOSING_COMMITS.get(CYCLE_START.isoformat())
        if seed_commit:
            try:
                closing_html = subprocess.check_output(
                    ["git", "show", f"{seed_commit}:index.html"], text=True)
                match = re.search(
                    r'<script id="data" type="application/json">(.*?)</script>',
                    closing_html, re.S)
                if match:
                    baseline = json.loads(match.group(1))
            except (OSError, subprocess.CalledProcessError, json.JSONDecodeError):
                pass
        if not baseline:
            archive_path = os.path.join(
                os.path.dirname(__file__), f"index-cycle-{CYCLE_START.isoformat()}.html")
            if os.path.isfile(archive_path):
                with open(archive_path, encoding="utf-8") as handle:
                    match = re.search(
                        r'<script id="data" type="application/json">(.*?)</script>',
                        handle.read(), re.S)
                if match:
                    baseline = json.loads(match.group(1))
        if not baseline:
            with open(snapshot_path, encoding="utf-8") as handle:
                baseline = json.load(handle).get("records", [])
        with open(closing_path, "w", encoding="utf-8") as handle:
            json.dump({
                "cycle_start": CYCLE_START.isoformat(),
                "cycle_end": CYCLE_END.isoformat(),
                "closed_at": (CYCLE_END + dt.timedelta(days=1)).isoformat(),
                "records": baseline,
            }, handle, ensure_ascii=False, separators=(",", ":"))
            handle.write("\n")
    baseline_ids = {int(row["id"]) for row in baseline}
    added_at_rebuild = [dict(row) for row in records
                        if int(row["id"]) not in baseline_ids]
    if added_at_rebuild:
        baseline.extend(added_at_rebuild)
        with open(closing_path, "w", encoding="utf-8") as handle:
            json.dump({
                "cycle_start": CYCLE_START.isoformat(),
                "cycle_end": CYCLE_END.isoformat(),
                "closed_at": (CYCLE_END + dt.timedelta(days=1)).isoformat(),
                "second_charge_students_added": len(added_at_rebuild),
                "records": baseline,
            }, handle, ensure_ascii=False, separators=(",", ":"))
            handle.write("\n")
    baseline_by_id = {int(row["id"]): row for row in baseline}
    debt_ids = [sid for sid, row in baseline_by_id.items()
                if int(row.get("debt_amount") or 0) > 0]
    payments_by_id = {}
    for part in chunks(debt_ids):
        joined = sql_ids(part)
        for item in query(
            "SELECT ID,STUDENT_ID sid,STUDENT_TYPE,AMOUNT,TRANSACTION_DATE "
            "FROM transaction_list WHERE ORG_ID=6 AND ACTION_TYPE='add' "
            f"AND TRANSACTION_DATE>='{CYCLE_START}' AND STUDENT_ID IN ({joined}) "
            "ORDER BY STUDENT_ID,ID"):
            payments_by_id.setdefault(int(item["sid"]), []).append(item)
    applied_total = 0
    confirmed_buckets = CONFIRMED_ARCHIVE_BUCKETS.get(CYCLE_START.isoformat(), {})
    for row in records:
        student_id = int(row["id"])
        live_cycle_charge = max(0, int(row.get("cycle_charge_amount") or 0))
        live_cycle_taken = str(row.get("current_cycle_taken") or "")
        live_due = str(row.get("due") or "")[:10]
        live_balance = int(row.get("balance") or 0)
        live_bucket = str(row.get("analytics_bucket") or "").lower()
        live_subscription_status = str(row.get("subscription_status") or "").lower()
        base = baseline_by_id.get(student_id, {})
        base_bucket = str(base.get("analytics_bucket") or "").lower()
        # An archive is derived from its end-of-cycle snapshot, not from the
        # student's newer subscription/balance/status. Only a later receipt
        # is allowed to change the archived result.
        row.clear()
        row.update(base)
        if live_cycle_charge:
            row["cycle_charge_amount"] = live_cycle_charge
            row["current_cycle_taken"] = live_cycle_taken
            if live_due:
                row["due"] = live_due
        # The CRM can void/delete a previously created cycle charge (for
        # example after replacing a subscription).  When the exact charge no
        # longer exists and the authoritative balance is non-negative, the
        # frozen debt is no longer real and must not remain on the archive.
        charge_voided = (int(base.get("debt_amount") or 0) > 0 and
                bool(base.get("current_cycle_taken")) and
                not live_cycle_charge and live_balance >= 0 and
                base_bucket not in {"bit", "referral"})
        if charge_voided:
            row["balance"] = live_balance
            row["cycle_charge_amount"] = 0
            row["debt_amount"] = 0
            row["amount"] = int(row.get("required") or 0)
            row["partial"] = False
            row["paid"] = True
            row["analytics_bucket"] = "paid"
        # The transaction charge is the authoritative obligation for this
        # cycle. A later tariff/subscription price can differ and must not
        # leave a false remainder in an archived cycle.
        if (live_cycle_charge and int(base.get("debt_amount") or 0) > 0 and
                not bool(base.get("paid")) and
                base_bucket not in {"bit", "referral"}):
            base_amount = max(0, int(base.get("amount") or 0))
            row["cycle_charge_amount"] = live_cycle_charge
            row["required"] = live_cycle_charge
            row["debt_amount"] = max(0, live_cycle_charge - base_amount)
            if row["debt_amount"] == 0:
                row["paid"] = True
                row["analytics_bucket"] = "paid"
        # A curator can confirm completion/referral after the cycle closes.
        # Preserve the closing snapshot for every other status, but allow
        # these two fact-producing CRM corrections to flow into the archive.
        confirmed_bucket = confirmed_buckets.get(student_id)
        if confirmed_bucket:
            row["analytics_bucket"] = confirmed_bucket
        elif base_bucket in {"bit", "referral"}:
            # A completed/referral result is final for its cycle. Later CRM
            # archival, freezing or payment must not lower that cycle's fact.
            row["analytics_bucket"] = base_bucket
        elif live_bucket in {"bit", "referral"}:
            row["analytics_bucket"] = live_bucket
        elif not bool(row.get("paid")):
            # Generic live archive/freeze changes after cycle close must not
            # rewrite that cycle's final classification.  Only a later
            # Bitirdi/Referral confirmation or a real receipt can change it.
            if live_bucket == "frozen" or live_subscription_status == "freezed":
                row["analytics_bucket"] = "frozen"
                row["subscription_status"] = "freezed"
            else:
                row["analytics_bucket"] = base_bucket
        old_debt = max(0, int(row.get("debt_amount") or 0))
        all_payments = payments_by_id.get(student_id, [])
        # payment_count is frozen at cycle close. Taking only newly appended
        # transaction rows also catches payments entered later with a
        # backdated transaction date inside the old cycle.
        new_count = max(0, len(all_payments) - int(base.get("payments") or 0))
        new_payments = all_payments[-new_count:] if new_count else []
        debt_payments = [item for item in new_payments
                         if str(item.get("STUDENT_TYPE") or "") == "student"]
        new_amount = sum(max(0, int(item.get("AMOUNT") or 0)) for item in debt_payments)
        applied = min(old_debt, new_amount)
        row["debt_amount"] = old_debt - applied
        if not charge_voided:
            row["amount"] = min(int(row.get("required") or 0),
                                max(0, int(base.get("amount") or 0)) + applied)
        row["partial"] = 0 < row["amount"] < int(row.get("required") or 0)
        if old_debt and row["debt_amount"] == 0:
            row["paid"] = True
            if str(row.get("analytics_bucket") or "").lower() not in {"bit", "referral"}:
                row["analytics_bucket"] = "paid"
        if applied:
            row["payments"] = int(base.get("payments") or 0) + len(new_payments)
            row["last_paid"] = str(max(
                (item.get("TRANSACTION_DATE") or "" for item in debt_payments),
                default=row.get("last_paid") or ""))
        applied_total += applied
    return records, applied_total


def sql_ids(values):
    return ",".join(str(int(v)) for v in values) or "0"


def money(value):
    return f"{int(value or 0):,}".replace(",", " ")


def load_analytics_rows():
    """Temporary analytics snapshot for statuses and paid Upsell sales."""
    if os.environ.get("CRM_PHONE") and os.environ.get("CRM_PASS"):
        import crm_debtors_update as crm
        rows = crm.table_rows(crm.fetch(crm.crm_session(), CYCLE_START, CYCLE_END))
        return [{"id": row["student_id"], "bucket": crm.status_key(row["status"])}
                for row in rows]
    page = urllib.request.urlopen(ANALYTICS_URL, timeout=120).read().decode("utf-8")
    match = re.search(r'<script id="detail-data" type="application/json">(.*?)</script>', page, re.S)
    if match:
        return json.loads(match.group(1))
    # The current MCP dashboard stores the already verified CRM bucket inside
    # its canonical data payload. Reuse it when CRM credentials are not
    # available (for example during an emergency local refresh).
    match = re.search(r'<script id="data" type="application/json">(.*?)</script>', page, re.S)
    if not match:
        return []
    return [{"id": row["id"], "bucket": row.get("analytics_bucket", "")}
            for row in json.loads(match.group(1))]


def short_name(value):
    value = re.sub(r"\s+", " ", str(value or "").strip())
    aliases = {"Abdulkhakova Fotimabonu": "Fotima", "Xalima Ismoiljonova": "Halima"}
    return aliases.get(value, value.split()[0] if value else "Biriktirilmagan")


def due_date(day):
    try:
        day = int(float(day or 0))
    except (TypeError, ValueError):
        return CYCLE_START
    # A cycle spans the tail of one month and the beginning of the next.
    # Payment days at/after the cycle's start day belong to the first month;
    # lower days belong to the second month.  A day beyond CYCLE_END remains
    # outside this cycle and is filtered from its plan view.
    target = CYCLE_START if day >= CYCLE_START.day else CYCLE_END
    safe_day = max(1, min(day, calendar.monthrange(target.year, target.month)[1]))
    return dt.date(target.year, target.month, safe_day)


def add_months(value, months):
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    return dt.date(year, month, min(value.day, calendar.monthrange(year, month)[1]))


def package_months(tariff, price):
    text = str(tariff or "").lower()
    match = re.search(r"(?:upsell|nasiya).*?(12|6|3)|-\s*(12|6|3)(?:\D|$)", text)
    if match:
        return int(next(value for value in match.groups() if value))
    return 12 if price >= 2500000 else (6 if price >= 1750000 else (3 if price >= 900000 else 0))


def load_data():
    analytics_rows = load_analytics_rows()
    analytics_buckets = {int(row["id"]): str(row.get("bucket") or "") for row in analytics_rows}
    upsells = []
    # Upsell means package-sized payments made by an existing student in the
    # selected cycle. The payment is authoritative: CRM can keep the package
    # subscription's old start date or replace it with a monthly subscription,
    # which must not make a real sale disappear from this report.
    for row in query(
        "SELECT s.ID sid,s.NAME student_name,sub.PRICE_TYPE tariff,"
        "u.NAME admin_name,u.SURNAME admin_surname,"
        "SUM(CASE WHEN t.STUDENT_TYPE='student' THEN t.AMOUNT ELSE 0 END) paid_amount,"
        "SUM(CASE WHEN t.STUDENT_TYPE='new_student' THEN t.AMOUNT ELSE 0 END) new_amount,"
        "MAX(CASE WHEN t.STUDENT_TYPE='student' THEN t.TRANSACTION_DATE END) paid_date "
        "FROM student_list s JOIN transaction_list t ON t.STUDENT_ID=s.ID "
        "AND t.ORG_ID=6 AND t.ACTION_TYPE='add' "
        f"AND t.TRANSACTION_DATE>='{CYCLE_START}' AND t.TRANSACTION_DATE<'{CYCLE_END + dt.timedelta(days=1)}' "
        "LEFT JOIN subscribe_list sub ON sub.ID=(SELECT sub2.ID FROM subscribe_list sub2 "
        "WHERE sub2.ORG_ID=6 AND sub2.STUDENT_ID=s.ID "
        "ORDER BY sub2.ACTIVE DESC,sub2.ID DESC LIMIT 1) "
        "LEFT JOIN group_list g ON g.ID=sub.GROUP_ID LEFT JOIN gl_sys_users u ON u.ID=g.ADMIN_ID "
        "WHERE s.ORG_ID=6 GROUP BY s.ID,s.NAME,sub.PRICE_TYPE,u.NAME,u.SURNAME "
        "HAVING paid_amount>=900000 AND new_amount=0"):
        tariff = str(row.get("tariff") or "").strip()
        package_price = int(row.get("paid_amount") or 0)
        month_match = re.search(r"(?:upsell|nasiya)\s*(3|6|12)|(?:^|\D)(3|6|12)(?:\D|$)", tariff, re.I)
        explicit_months = int(next((value for value in month_match.groups() if value), 0)) if month_match else 0
        months = explicit_months or (12 if package_price >= 2500000 else (6 if package_price >= 1750000 else 3))
        full_admin = (str(row.get("admin_name") or "").strip() + " " +
                      str(row.get("admin_surname") or "").strip()).strip()
        upsells.append({
            "id": int(row["sid"]),
            "name": row.get("student_name") or f"Student {row['sid']}",
            "curator": short_name(full_admin),
            "months": months,
            "tariff": tariff,
            "amount": max(0, int(row.get("paid_amount") or 0)),
            "sale_date": ".".join(str(row.get("paid_date") or "")[:10].split("-")[::-1]),
        })
    plan = query("SELECT ID,START_DATE,END_DATE,DATA FROM debtors_plan "
                 "WHERE ORG_ID=6 ORDER BY ID DESC LIMIT 1")[0]
    raw_ids = [int(x) for x in json.loads(plan["DATA"])]
    raw_ids = sorted(set(raw_ids) |
                     CONFIRMED_CYCLE_MEMBER_IDS.get(CYCLE_START.isoformat(), set()))
    requested_snapshot = os.path.join(
        SNAPSHOT_DIR, f"{CYCLE_START.isoformat()}_{CYCLE_END.isoformat()}.json")
    if REQUESTED_CYCLE and os.path.isfile(requested_snapshot):
        with open(requested_snapshot, encoding="utf-8") as handle:
            raw_ids = [int(row["id"]) for row in json.load(handle).get("records", [])]
    # A repeat debit is an obligation even when the student's first
    # subscription began inside this same cycle. This covers boundary cases
    # such as a first payment on the opening day and a second debit on the
    # closing day of the cycle.
    second_charge_rows = query(
        "SELECT DISTINCT t.STUDENT_ID sid FROM transaction_list t "
        "WHERE t.ORG_ID=6 AND t.ACTION_TYPE='taken' "
        f"AND t.TRANSACTION_DATE>='{CYCLE_START}' "
        f"AND t.TRANSACTION_DATE<'{CYCLE_END + dt.timedelta(days=1)}' "
        "AND DATEDIFF(t.TRANSACTION_DATE,(SELECT MIN(p.TRANSACTION_DATE) "
        "FROM transaction_list p WHERE p.ORG_ID=t.ORG_ID "
        "AND p.STUDENT_ID=t.STUDENT_ID AND p.ACTION_TYPE='add'))>=20")
    second_charge_ids = {int(row["sid"]) for row in second_charge_rows}
    second_charge_added_ids = second_charge_ids - set(raw_ids)
    raw_ids = sorted(set(raw_ids) | second_charge_ids)
    # Safety net for omissions in debtors_plan: an active repeat student with
    # a negative authoritative balance must still appear as a debtor.
    admin_ids = ",".join(str(x) for x in sorted(CURRENT_ADMINS))
    balance_candidates = query(
        "SELECT DISTINCT s.ID sid FROM student_list s "
        "JOIN subscribe_list sub ON sub.STUDENT_ID=s.ID AND sub.ORG_ID=6 "
        "AND sub.ACTIVE=1 AND sub.STATUS='active' "
        "JOIN group_list g ON g.ID=sub.GROUP_ID "
        "WHERE s.ORG_ID=6 AND s.ACTIVE=1 AND s.CURRENT_BALANCE<0 "
        f"AND g.ADMIN_ID IN ({admin_ids}) "
        f"AND (SELECT MIN(s0.START_DATE) FROM subscribe_list s0 WHERE s0.ORG_ID=6 "
        f"AND s0.STUDENT_ID=s.ID)<'{CYCLE_START}'")
    balance_candidate_ids = {int(row["sid"]) for row in balance_candidates}
    plan_id_set = set(raw_ids)
    balance_added_ids = balance_candidate_ids - plan_id_set
    ids = sorted(plan_id_set | balance_candidate_ids)

    requested_month_key = CYCLE_END.strftime("%Y-%m")
    month_key_row = query(
        "SELECT MAX(MONTH_KEY) month_key FROM debtors_plan_members "
        f"WHERE ORG_ID=6 AND MONTH_KEY<='{requested_month_key}'")[0]
    month_key = str(month_key_row.get("month_key") or requested_month_key)
    member = query("SELECT COUNT(*) cnt,COUNT(DISTINCT STUDENT_ID) unique_cnt "
                   f"FROM debtors_plan_members WHERE ORG_ID=6 AND MONTH_KEY='{month_key}'")[0]
    member_rows = query("SELECT STUDENT_ID sid,IS_REFERRAL_PLAN is_referral,PRICE_TYPE member_tariff "
                        f"FROM debtors_plan_members WHERE ORG_ID=6 AND MONTH_KEY='{month_key}'")
    member_flags = {int(row["sid"]): row for row in member_rows}

    students = {}
    subscriptions = {}
    payments = {}
    charges = {}
    for part in chunks(ids):
        joined = sql_ids(part)
        for row in query(
            "SELECT s.ID sid,s.NAME student_name,s.ACTIVE student_active,s.CURRENT_BALANCE balance,sub.ID sub_id,"
            "sub.ACTIVE sub_active,sub.STATUS sub_status,sub.PAYMENT_STATUS payment_status,sub.START_DATE sub_start_date,"
            "(SELECT MIN(s0.START_DATE) FROM subscribe_list s0 WHERE s0.ORG_ID=6 AND s0.STUDENT_ID=s.ID) first_sub_start,"
            "sub.DAY pay_day,COALESCE(NULLIF(sub.SPECIAL_PRICE,0),NULLIF(sub.STANDARD_PRICE_SNAPSHOT,0),0) price,sub.PRICE_TYPE price_type,sub.GROUP_ID group_id,"
            "g.NAME group_name,g.ADMIN_ID admin_id,u.NAME admin_name,u.SURNAME admin_surname,u.TEAM_ID team_id "
            "FROM student_list s LEFT JOIN subscribe_list sub ON sub.STUDENT_ID=s.ID AND sub.ORG_ID=6 "
            "LEFT JOIN group_list g ON g.ID=sub.GROUP_ID "
            "LEFT JOIN gl_sys_users u ON u.ID=g.ADMIN_ID "
            f"WHERE s.ID IN ({joined}) ORDER BY s.ID,sub.ACTIVE DESC,sub.ID DESC"):
            sid = int(row["sid"])
            students.setdefault(sid, row)
            if sid not in subscriptions and row.get("sub_id"):
                subscriptions[sid] = row
        for row in query(
            "SELECT STUDENT_ID sid,SUM(AMOUNT) amount,COUNT(*) payment_count,MIN(TRANSACTION_DATE) first_paid,MAX(TRANSACTION_DATE) last_paid "
            "FROM transaction_list WHERE ORG_ID=6 AND ACTION_TYPE='add' "
            f"AND TRANSACTION_DATE>='{CYCLE_START}' AND TRANSACTION_DATE<'{CYCLE_END + dt.timedelta(days=1)}' "
            f"AND STUDENT_ID IN ({joined}) GROUP BY STUDENT_ID"):
            payments[int(row["sid"])] = row
        for row in query(
            "SELECT ID,STUDENT_ID sid,TRANSACTION_DATE taken_at,AMOUNT charge_amount "
            "FROM transaction_list WHERE ORG_ID=6 AND ACTION_TYPE='taken' "
            f"AND TRANSACTION_DATE>='{CYCLE_START}' AND TRANSACTION_DATE<'{CYCLE_END + dt.timedelta(days=1)}' "
            f"AND STUDENT_ID IN ({joined}) ORDER BY STUDENT_ID,ID"):
            charges.setdefault(int(row["sid"]), []).append(row)

    today = min(CYCLE_END, dt.datetime.now(BUSINESS_TZ).date())
    records = []
    test_excluded = 0
    other_admin_excluded = 0
    first_month_excluded = 0
    expired_package_excluded = 0
    for sid in ids:
        base = students.get(sid, {})
        sub = subscriptions.get(sid, {})
        admin_id = int(sub.get("admin_id") or 0)
        if admin_id in TEST_ADMINS:
            test_excluded += 1
            continue
        if admin_id and admin_id not in CURRENT_ADMINS:
            other_admin_excluded += 1
            continue
        # Debtors are repeat monthly payers only. A student whose current
        # subscription started inside this cycle is still in month one and
        # must not affect the debtor plan, fact, gap, statuses or rating.
        sub_start_raw = str(sub.get("first_sub_start") or sub.get("sub_start_date") or "")[:10]
        try:
            sub_start = dt.date.fromisoformat(sub_start_raw)
        except ValueError:
            sub_start = None
        if (sub_start and CYCLE_START <= sub_start <= CYCLE_END and
                sid not in second_charge_ids):
            first_month_excluded += 1
            continue
        full_admin = (str(sub.get("admin_name") or "").strip() + " " +
                      str(sub.get("admin_surname") or "").strip()).strip()
        paid = payments.get(sid, {})
        price = max(0, int(sub.get("price") or 0))
        balance = int(base.get("balance") or 0)
        due = due_date(sub.get("pay_day"))
        all_cycle_charges = charges.get(sid, [])
        if sid in second_charge_ids and all_cycle_charges:
            due_charges = all_cycle_charges
            due = dt.date.fromisoformat(str(max(
                item.get("taken_at") or "" for item in due_charges))[:10])
        else:
            due_charges = [item for item in all_cycle_charges
                           if str(item.get("taken_at") or "")[:10] == due.isoformat()]
        cycle_charge_amount = sum(max(0, int(item.get("charge_amount") or 0))
                                  for item in due_charges)
        cycle_charge_date = str(max(
            (item.get("taken_at") or "" for item in due_charges), default=""))
        tariff_text = str(sub.get("price_type") or "").strip().lower()
        months = package_months(tariff_text, price)
        package_start_raw = str(sub.get("sub_start_date") or "")[:10]
        try:
            package_start = dt.date.fromisoformat(package_start_raw)
        except ValueError:
            package_start = None
        package_until = add_months(package_start, months) if package_start and months else None
        if months and package_until and package_until <= CYCLE_START:
            expired_package_excluded += 1
            continue
        is_package = bool(months and package_start and package_start <= CYCLE_END and
                          package_until > CYCLE_START)
        required = max(0, round(price / months)) if is_package and months else price
        # Only students whose debit date is still ahead in the selected cycle
        # are "Kutilmoqda".  The CRM's historical "kutilyabdi" flag is not a
        # source of truth here because it can remain after the due date.
        if is_package:
            debt_amount = 0
        elif due > today:
            debt_amount = max(0, required - max(0, balance))
        else:
            debt_amount = max(0, -balance)
        amount = required if debt_amount == 0 else max(0, required - debt_amount)
        fully_paid = is_package or debt_amount == 0
        common = {
            "id": sid,
            "name": base.get("student_name") or f"Student {sid}",
            "admin_id": admin_id,
            "curator": short_name(full_admin),
            "admin_full": full_admin or "Biriktirilmagan",
            "team": "A" if int(sub.get("team_id") or 0) == 2 else ("B" if admin_id else "—"),
            "group": sub.get("group_name") or "Guruhsiz",
            "upsell": (str(sub.get("price_type") or "").strip()
                       if str(sub.get("price_type") or "").lower().startswith("upsell") else ""),
            "subscription_status": sub.get("sub_status") or "missing",
            "payment_status": sub.get("payment_status") or "—",
            "student_active": bool(int(base.get("student_active") or 0)),
            "current_cycle_taken": cycle_charge_date,
            "cycle_charge_amount": cycle_charge_amount,
            "has_subscription": bool(sub),
            "subscription_start": str(sub.get("sub_start_date") or "")[:10],
            "first_subscription_start": sub_start_raw,
            "tariff": str(member_flags.get(sid, {}).get("member_tariff") or sub.get("price_type") or "").strip(),
            "is_referral": bool(int(member_flags.get(sid, {}).get("is_referral") or 0)),
            "is_package": is_package,
            "package_months": months if is_package else 0,
            "package_until": package_until.isoformat() if is_package else "",
            "analytics_bucket": analytics_buckets.get(sid, ""),
        }
        records.append({**common,
            "obligation": "current",
            "due": due.isoformat(),
            "due_arrived": due <= today,
            "paid": fully_paid,
            "partial": amount > 0 and not fully_paid,
            "delayed": False,
            "amount": amount,
            "required": required,
            "debt_amount": debt_amount,
            "payments": int(paid.get("payment_count") or 0),
            "last_paid": str(paid.get("last_paid") or ""),
            "price": price,
            "balance": balance,
        })

    records, snapshot_path, snapshot_created, previous_cycle_excluded, due_dates_corrected = freeze_cycle_plan(
        records, second_charge_ids)
    records, archive_duplicates_removed = remove_previous_cycle_archive_duplicates(
        records, snapshot_path)
    records, archived_late_collected = apply_late_payments_to_archived_cycle(records, snapshot_path)
    generated_at = dt.datetime.now(dt.timezone.utc)
    audit = {
        "plan_id": int(plan["ID"]),
        "plan_start": plan["START_DATE"],
        "plan_end": plan["END_DATE"],
        "raw_ids": len(raw_ids),
        "unique_ids": len(ids),
        "balance_debtors_added": len(balance_added_ids),
        "second_charge_students": len(second_charge_ids),
        "second_charge_added": len(second_charge_added_ids),
        "member_rows": int(member["cnt"]),
        "member_unique": int(member["unique_cnt"]),
        "member_month_key": month_key,
        "missing_subscription": sum(not r.get("has_subscription", False) for r in records),
        "unassigned": sum(not r["admin_id"] for r in records),
        "test_excluded": test_excluded,
        "other_admin_excluded": other_admin_excluded,
        "first_month_excluded": first_month_excluded,
        "expired_package_excluded": expired_package_excluded,
        "plan_snapshot": os.path.basename(snapshot_path),
        "plan_snapshot_created": snapshot_created,
        "plan_locked_students": len(records),
        "previous_cycle_excluded": previous_cycle_excluded,
        "previous_cycle_archive_duplicates_removed": archive_duplicates_removed,
        "due_dates_corrected": due_dates_corrected,
        "archived_late_collected": archived_late_collected,
        "generated": generated_at.strftime("%d.%m.%Y %H:%M UTC"),
        "generated_iso": generated_at.isoformat(timespec="seconds"),
        "today": today.isoformat(),
    }
    def is_special_fact(row):
        bucket = str(row["analytics_bucket"] or "").lower()
        return (bucket in {"bit", "referral"} or row["is_referral"] or row["is_package"] or
                str(row["tariff"] or "").strip().upper() == "SARAFAN RADIO")

    audit["anomalies"] = {
        "debt_with_zero": sum((not r["paid"]) and not is_special_fact(r) and
                              r["subscription_status"] == "active" and r["debt_amount"] <= 0
                              for r in records),
        "paid_with_debt": sum(r["paid"] and r["debt_amount"] > 0 for r in records),
        "duplicate_students": len(records) - len({r["id"] for r in records}),
    }
    if any(audit["anomalies"].values()):
        invalid_ids = [r["id"] for r in records if ((not r["paid"] and not is_special_fact(r) and
                                                     r["subscription_status"] == "active" and r["debt_amount"] <= 0) or
                                                     (r["paid"] and r["debt_amount"] > 0))]
        raise RuntimeError(f"Dashboard integrity check failed: {audit['anomalies']}; ids={invalid_ids}")
    collection_rows = query(
        "SELECT DATE(TRANSACTION_DATE) paid_date,SUM(AMOUNT) amount "
        "FROM transaction_list WHERE ORG_ID=6 AND ACTION_TYPE='add' AND STUDENT_TYPE='student' "
        f"AND TRANSACTION_DATE>='{CYCLE_START}' AND TRANSACTION_DATE<'{CYCLE_END + dt.timedelta(days=1)}' "
        "GROUP BY DATE(TRANSACTION_DATE) ORDER BY paid_date")
    collections = [{"date": str(row["paid_date"])[:10], "amount": int(row["amount"] or 0)}
                   for row in collection_rows]
    if archived_late_collected:
        collections.append({"date": CYCLE_END.isoformat(), "amount": archived_late_collected})
    audit["collected_total"] = sum(row["amount"] for row in collections)
    audit["upsell_sales"] = len(upsells)
    return records, audit, upsells, collections


def short_date(value):
    return value.strftime("%d.%m")


def archive_previous_dashboard(path):
    if not os.path.isfile(path) or os.path.basename(path) != "index.html":
        return
    with open(path, encoding="utf-8") as handle:
        previous = handle.read()
    match = re.search(r"const periods=.*?all.*?[\"'](\d{4}-\d{2}-\d{2})[\"']", previous)
    if not match or match.group(1) == CYCLE_START.isoformat():
        return
    target = os.path.join(os.path.dirname(path), f"index-cycle-{match.group(1)}.html")
    if not os.path.exists(target):
        shutil.copyfile(path, target)
        print(f"ARCHIVED dashboard cycle {match.group(1)} -> {target}")


def ensure_legacy_cycle_archives(path):
    if (os.path.basename(path) != "index.html" or
            os.path.basename(os.path.dirname(path)) == "mcp-test"):
        return
    base = os.path.dirname(path) or "."
    for cycle_start, commit in LEGACY_ARCHIVE_COMMITS.items():
        target = os.path.join(base, f"index-cycle-{cycle_start}.html")
        if os.path.exists(target):
            continue
        try:
            previous = subprocess.check_output(
                ["git", "show", f"{commit}:index.html"], text=True)
        except (OSError, subprocess.CalledProcessError):
            continue
        with open(target, "w", encoding="utf-8") as handle:
            handle.write(previous)
        print(f"RESTORED dashboard cycle {cycle_start} -> {target}")


def cycle_archive_options(path):
    if os.path.basename(os.path.dirname(path)) == "mcp-test":
        return ""
    base = os.path.dirname(path) or "."
    viewing_archive = bool(REQUESTED_CYCLE)
    live_start, live_end = current_cycle()
    live_label = f"{short_date(live_start)}–{short_date(live_end)}"
    options = [f'<option value="index.html"{"" if viewing_archive else " selected"}>📅 Цикл {live_label} · текущий</option>']
    prefix = "index-cycle-"
    for name in sorted(os.listdir(base), reverse=True):
        if not (name.startswith(prefix) and name.endswith(".html")):
            continue
        raw = name[len(prefix):-5]
        try:
            start = dt.date.fromisoformat(raw)
        except ValueError:
            continue
        end = cycle_end_for_start(start)
        selected = " selected" if viewing_archive and start == CYCLE_START else ""
        options.append(f'<option value="{html.escape(name)}"{selected}>📅 Цикл {short_date(start)}–{short_date(end)} · архив</option>')
    return '<select id="cycleHistory" aria-label="Выберите цикл" onchange="location.href=this.value">' + "".join(options) + '</select>'


def render(records, audit, upsells, collections):
    data_json = json.dumps(records, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
    upsell_json = json.dumps(upsells, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
    collection_json = json.dumps(collections, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
    document = '''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>__SITE_LABEL__ · Кураторы</title><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin><link href="https://fonts.googleapis.com/css2?family=Barlow:wght@600;700;800&amp;family=Barlow+Condensed:ital,wght@0,600;0,700;0,800;1,700;1,800&amp;family=Manrope:wght@500;700;800&amp;display=swap" rel="stylesheet"><style>
:root{--ink:#101722;--mut:#657080;--line:#d9dee6;--paper:#f3f1eb;--panel:#fff;--red:#ff4b28;--green:#24d993;--blue:#38a8f4;--violet:#a95af4;--orange:#ffad1f;--gray:#929dad;--yellow:#ffc51b}*{box-sizing:border-box}body{margin:0;background:repeating-linear-gradient(115deg,transparent 0 54px,#e9e6de 54px 57px),var(--paper);font-family:Arial,sans-serif;color:var(--ink)}button,select,input{font:inherit}button{color:inherit}.wrap{max-width:1560px;margin:auto;padding:28px}.top{display:flex;align-items:center;gap:18px;flex-wrap:wrap;margin-bottom:20px}.brand{display:flex;align-items:stretch;font-size:26px;font-weight:1000;font-style:italic}.brand b{background:var(--red);color:#fff;padding:14px 20px}.brand span{background:#fff;padding:14px 20px}.updated{color:var(--mut);font-weight:800}.nav{margin-left:auto;display:flex;gap:8px}.nav button,.controls select{border:1px solid var(--line);background:#fff;padding:14px 20px;font-weight:800}.nav .active{border-bottom:3px solid var(--red)}.controls{display:flex;gap:10px}.hero{display:grid;grid-template-columns:1.7fr 1fr;gap:16px}.panel{background:#fff;border-left:5px solid var(--red);padding:24px}.panel h1,.panel h2{margin:0 0 14px;font-style:italic}.big{display:flex;align-items:end;gap:20px}.big strong{font-size:72px;line-height:.9}.big span{font-size:24px;font-weight:900}.big button{font-size:28px}.click{border:0;background:transparent;padding:0;font-weight:1000;cursor:pointer;border-bottom:3px dotted #9ca5b2}.click:hover{color:var(--red)}.progress{height:34px;margin:18px 0;background:repeating-linear-gradient(90deg,#e9e6df 0 calc(5% - 2px),#fff calc(5% - 2px) 5%);position:relative}.progress i{display:block;height:100%;background:linear-gradient(90deg,#ef2d0a,var(--red))}.cards{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}.stat{background:#f4f1eb;border-left:4px solid var(--red);padding:14px}.stat small{display:block;color:var(--mut);font-weight:900;text-transform:uppercase}.stat button{font-size:28px}.statuses{display:grid;gap:14px}.status-row{display:grid;grid-template-columns:120px 1fr 70px;align-items:center;gap:12px;font-weight:900}.status-row .bar{height:28px;background:#e9e5dd;border-radius:9px;overflow:hidden}.status-row i{display:block;height:100%;border-radius:9px}.teams{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin:16px 0}.team-card{border:0;padding:24px;text-align:left;background:#dfe5ef;clip-path:polygon(0 0,97% 0,100% 100%,0 100%);cursor:pointer}.team-card.win{background:linear-gradient(100deg,#ffd528,#ffb400)}.team-card b{font-size:28px}.team-card strong{float:right;font-size:50px}.ticker{background:#fff;border-left:100px solid var(--red);padding:15px 22px;font-weight:900;margin-bottom:16px}.board{background:#fff;border-left:5px solid var(--red)}.board-title{padding:22px 24px 12px;display:flex;justify-content:space-between;gap:20px}.board-title h2{margin:0;font-style:italic}.hint{color:var(--mut);font-weight:800}.head,.rank{display:grid;grid-template-columns:60px 230px 1fr 160px 80px 80px 100px;gap:14px;align-items:center}.head{padding:10px 20px;color:var(--mut);font-size:11px;font-weight:900;text-transform:uppercase}.rank{padding:14px 20px;border-top:1px solid var(--line)}.rank:hover{background:#fff8df}.pos{font-size:28px;font-weight:1000}.person{border:0;background:none;text-align:left;cursor:pointer}.person b{font-size:21px}.person small,.metric small{display:block;color:var(--mut);margin-top:4px}.badge{display:inline-grid;place-items:center;width:22px;height:22px;background:var(--yellow);margin-right:7px;font-weight:900}.badge.b{background:#ccd5e2}.track{position:relative;height:22px;background:repeating-linear-gradient(90deg,#e8e5de 0 calc(10% - 1px),#d0d4da calc(10% - 1px) 10%);overflow:hidden}.track i{display:block;height:100%;background:linear-gradient(90deg,#ef2d0a,#ff6540)}.track em{position:absolute;top:0;bottom:0;width:3px;background:#fff;box-shadow:0 0 5px #111}.num{font-size:20px}.good{color:#0b9d68}.bad{color:#d51d45}.modal{position:fixed;inset:0;background:#111827b5;display:grid;place-items:center;padding:20px;z-index:5}.modal[hidden]{display:none}.dialog{width:min(1120px,100%);max-height:90vh;background:#fff;border-radius:22px;overflow:auto}.dialog-head{position:sticky;top:0;background:#fff;padding:20px 24px;border-bottom:1px solid var(--line);display:flex;justify-content:space-between;z-index:2}.dialog-head h2{margin:0}.x{border:0;border-radius:50%;font-size:24px;width:42px;height:42px}.filters{position:sticky;top:83px;background:#f5f2ec;padding:14px 24px;display:flex;gap:10px;align-items:center;flex-wrap:wrap;z-index:2}.filters input,.filters details,.reset{background:#fff;border:1px solid var(--line);border-radius:9px;padding:11px}.filters .search{flex:1;min-width:230px}.checks{position:absolute;background:#fff;padding:12px;box-shadow:0 8px 25px #0002;display:grid;gap:8px}.reset{cursor:pointer}.students{padding:0 24px 22px}.student{display:grid;grid-template-columns:1fr 190px 165px;gap:14px;padding:14px 0;border-bottom:1px solid var(--line)}.student a{color:var(--ink);font-size:19px;font-weight:900}.student small{display:block;color:var(--mut);margin-top:4px}.status-stack{display:flex;align-items:center;gap:6px;flex-wrap:wrap}.upsell-pill{border-radius:999px;padding:6px 10px;background:#eee3ff;color:#6b31a8;font-weight:900;white-space:nowrap}.payment-value{display:block;text-align:right}.payment-value small{font-size:12px;font-weight:800;color:var(--mut);white-space:nowrap}.pill{justify-self:start;border-radius:999px;padding:6px 12px;font-weight:900}.pill.paid{background:#dffbed;color:#087c54}.pill.delayed{background:#dcedff;color:#1268a8}.pill.overdue{background:#ffe2e0;color:#be183b}.pill.future{background:#fff0c9;color:#986400}@media(max-width:980px){.hero{grid-template-columns:1fr}.cards{grid-template-columns:1fr 1fr}.head{display:none}.rank{grid-template-columns:45px 1fr 70px 70px}.track,.metric,.overdue-cell{display:none}.controls{width:100%;overflow:auto}.nav{margin-left:0}.teams{grid-template-columns:1fr}.student{grid-template-columns:1fr 180px 145px}}
.metric-pair{display:flex;align-items:center;gap:6px;font-size:18px;font-weight:1000}.metric-pair .paid-count{color:#087c54}.metric-pair .debt-count{color:#be183b}.metric-pair .sep{color:var(--mut)}
.lang-switch{display:flex;border:2px solid var(--line);border-radius:999px;padding:4px;background:#f7f8fa}.lang-switch button{border:0;background:transparent;border-radius:999px;padding:8px 14px;font-weight:900;color:#858b96;cursor:pointer}.lang-switch button.active{background:#171a20;color:#fff}
.head,.rank{grid-template-columns:60px 230px 1fr 180px 80px 100px}
.page[hidden]{display:none}.subpage{display:grid;gap:16px}.data-table{background:#fff;border-left:5px solid var(--red)}.data-row,.data-head{display:grid;grid-template-columns:180px 1fr 130px 130px 140px;gap:14px;align-items:center;padding:14px 20px;border-top:1px solid var(--line)}.data-head{color:var(--mut);font-size:11px;font-weight:900;text-transform:uppercase}.data-row b{font-size:18px}.tariff-pill{display:inline-block;border-radius:999px;padding:7px 11px;background:#eee3ff;color:#6b31a8;font-weight:900}.week-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:14px}.week-card{background:#fff;border-left:5px solid var(--red);padding:20px;text-align:left}.week-card h3{margin:0 0 14px}.week-card .week-metrics{display:grid;grid-template-columns:1fr 1fr;gap:10px}.week-card strong{font-size:26px}.week-card small{display:block;color:var(--mut);font-weight:800}.upsell-summary{display:grid;grid-template-columns:repeat(3,1fr);gap:14px}.upsell-summary .stat{background:#fff}.nav button{cursor:pointer}@media(max-width:980px){.week-grid,.upsell-summary{grid-template-columns:1fr}.data-head{display:none}.data-row{grid-template-columns:1fr 1fr}.data-row>*:first-child{grid-column:1/-1}}
.upsell-head,.upsell-rank{display:grid;grid-template-columns:64px minmax(230px,.5fr) 1fr 190px 130px 110px;gap:14px;align-items:center}.upsell-head{padding:10px 26px 5px 20px;color:var(--mut);font-size:10px;font-weight:800;text-transform:uppercase;letter-spacing:.08em}.upsell-rank{padding:9px 26px 9px 20px;border-top:1px solid rgba(12,16,24,.07);animation:rowIn .55s both}.upsell-rank:hover,.rank:hover{background:linear-gradient(90deg,rgba(255,79,40,.08),transparent 70%)}.package-mix{display:flex;gap:6px;flex-wrap:wrap}.package-mix span{padding:4px 8px;font-size:11px;font-weight:900;color:#fff;clip-path:polygon(6px 0,100% 0,calc(100% - 6px) 100%,0 100%)}.package-mix .m3{background:#f1b800;color:#17120a}.package-mix .m6{background:#ff5d35}.package-mix .m12{background:#7650ae}.upsell-total{font-size:25px;font-weight:800}.upsell-filter-title{display:flex;align-items:center;justify-content:space-between;padding:18px 20px}.upsell-filter-title h3{margin:0}.upsell-summary button{font-size:28px}
body{font-family:Manrope,-apple-system,"Segoe UI",Arial,sans-serif;background:repeating-linear-gradient(115deg,transparent 0 54px,rgba(12,16,24,.04) 54px 57px),radial-gradient(1000px 420px at 0% -10%,rgba(255,79,40,.10),transparent 55%),#f1efe9}.wrap{max-width:none;padding:22px 32px 24px}.top{margin-bottom:16px}.brand,.panel h1,.panel h2,.board-title h2,.pos,.big strong,.num,.upsell-total{font-family:"Barlow Condensed","Arial Narrow",Arial,sans-serif}.brand b,.brand span{font:italic 800 24px/1 "Barlow Condensed",Arial,sans-serif;text-transform:uppercase;letter-spacing:.03em;padding:11px 18px;clip-path:polygon(0 0,100% 0,calc(100% - 12px) 100%,0 100%)}.brand span{margin-left:-8px;padding-left:24px;border:1px solid var(--line)}.updated{font-size:12px}.nav button,.controls select{padding:10px 16px;font-size:13px;background:#fff;clip-path:polygon(8px 0,100% 0,calc(100% - 8px) 100%,0 100%)}.panel,.board,.data-table,.week-card{border:1px solid var(--line);border-left:4px solid var(--red);clip-path:polygon(0 0,calc(100% - 22px) 0,100% 22px,100% 100%,0 100%)}.panel h1,.panel h2,.board-title h2{font-style:italic;font-weight:800;text-transform:uppercase;letter-spacing:.02em}.big strong{font-style:italic;font-size:76px}.big span{font-family:"Barlow Condensed",Arial,sans-serif;font-style:italic}.progress{height:30px;border:1px solid var(--line);overflow:hidden}.cards{gap:10px}.stat{background:#f4f2ec;border-left:3px solid var(--red)}.stat button,.stat strong{font:italic 800 30px/1 "Barlow Condensed",Arial,sans-serif}.status-row{font-size:13px}.status-row .bar{height:22px;background:#ece8df;border-radius:6px}.board-title{padding:18px 26px 8px 20px}.head,.rank{grid-template-columns:64px minmax(230px,.5fr) 1fr 180px 80px 100px}.head{padding:8px 26px 4px 20px;letter-spacing:.08em}.rank{padding:8px 26px 8px 20px}.person b{font:700 18px/1.2 Barlow,Manrope,Arial,sans-serif}.pos{width:52px;height:38px;display:grid;place-items:center;background:#f4f2ec;clip-path:polygon(22% 0,100% 0,78% 100%,0 100%);font:italic 800 23px "Barlow Condensed",Arial,sans-serif}.rank:nth-child(1) .pos,.upsell-rank:nth-child(1) .pos{background:linear-gradient(100deg,#ffd21e,#f0a400)}.rank:nth-child(2) .pos,.upsell-rank:nth-child(2) .pos{background:linear-gradient(100deg,#e7ecf4,#b9c3d4)}.rank:nth-child(3) .pos,.upsell-rank:nth-child(3) .pos{background:linear-gradient(100deg,#ffb37c,#ff8b47)}.track{height:18px;border:1px solid var(--line);background:#ece8df}.track i{background:linear-gradient(90deg,#e62e00,#ff4f28);box-shadow:0 0 12px rgba(255,79,40,.35)}.metric-pair,.num{font:italic 800 24px/1 "Barlow Condensed",Arial,sans-serif}.ticker{position:relative;height:46px;padding:0;border:1px solid var(--line);border-left:0;margin:0 0 14px;display:flex;align-items:center;overflow:hidden;background:#fff;white-space:nowrap}.ticker:before{content:"LIVE //";align-self:stretch;display:flex;align-items:center;background:var(--red);color:#fff;font:italic 800 17px "Barlow Condensed";padding:0 24px 0 16px;clip-path:polygon(0 0,100% 0,calc(100% - 14px) 100%,0 100%);z-index:2}.ticker span{display:inline-block;padding-left:30px;animation:crawl 30s linear infinite}.track i,.progress i,.status-row i{transition:width 1.5s cubic-bezier(.22,1,.36,1);position:relative;overflow:hidden}.track i:after,.progress i:after{content:"";position:absolute;inset:0;background:linear-gradient(110deg,transparent 30%,rgba(255,255,255,.45) 50%,transparent 70%);transform:translateX(-100%);animation:sweep 3.2s ease-in-out infinite}.rank{animation:rowIn .55s both}.rank:nth-child(2),.upsell-rank:nth-child(2){animation-delay:.06s}.rank:nth-child(3),.upsell-rank:nth-child(3){animation-delay:.12s}.rank:nth-child(4),.upsell-rank:nth-child(4){animation-delay:.18s}.rank:nth-child(5),.upsell-rank:nth-child(5){animation-delay:.24s}@keyframes sweep{0%{transform:translateX(-100%)}60%,100%{transform:translateX(100%)}}@keyframes rowIn{from{opacity:0;transform:translateY(12px)}to{opacity:1;transform:none}}@keyframes crawl{to{transform:translateX(-100%)}}@media(prefers-reduced-motion:reduce){.rank,.upsell-rank,.ticker span,.track i:after,.progress i:after{animation:none}}@media(max-width:980px){.wrap{padding:14px 12px}.upsell-head{display:none}.upsell-rank{grid-template-columns:45px 1fr 100px}.upsell-rank .track,.upsell-rank .package-mix,.upsell-rank .upsell-amount{display:none}}
</style></head><body><main class="wrap"><div class="top"><div class="brand"><b data-i18n="brandDebt">⚡ ДОЛГИ</b><span data-i18n="brandCurators">КУРАТОРЫ</span></div><div class="updated" data-generated="__GENERATED_ISO__"><span data-i18n="updated">обновлено</span> <span id="updatedTime">__GENERATED__</span> · __SITE_LABEL__</div><div class="lang-switch" aria-label="Language"><button type="button" data-lang="ru">RU</button><button type="button" data-lang="uz">UZ</button></div><nav class="nav"><button class="active" data-page="curatorPage" data-i18n="curators">Кураторы</button><button data-page="weeksPage" data-i18n="weeks">Недели</button><button data-page="upsellPage">Upsell</button></nav><div class="controls"><select id="period"><option value="all" data-i18n="cycleOption">📅 Цикл 24.09–24.10</option><option value="w1" data-i18n="week1">Неделя 24.09–30.09</option><option value="w2" data-i18n="week2">Неделя 01.10–07.10</option><option value="w3" data-i18n="week3">Неделя 08.10–14.10</option><option value="w4" data-i18n="week4">Неделя 15.10–24.10</option></select></div></div><div class="page" id="curatorPage">
<section class="hero"><article class="panel"><h1 id="cycleTitle">ЦИКЛ 24.09–24.10 // СБОР ДОЛГОВ</h1><div class="big"><strong id="heroPct">0%</strong><span><button class="click" data-list="paid" id="heroPaid">0</button> <span data-i18n="of">из</span> <button class="click" data-list="all" id="heroTotal">0</button> <span data-i18n="debtorsPaid">должников оплатили</span></span></div><div class="progress"><i id="heroBar"></i></div><div class="cards"><div class="stat"><small data-i18n="needCollect">Нужно собрать</small><button class="click" data-list="all" id="need">0</button></div><div class="stat"><small data-i18n="collected">Собрано</small><button class="click" data-list="paid" id="collected">0</button></div><div class="stat"><small data-i18n="paid">Оплатили</small><button class="click" data-list="paid" id="paidCard">0</button></div><div class="stat"><small data-i18n="stillOwe">Ещё должны</small><button class="click" data-list="debt" id="debtCard">0</button></div></div></article><article class="panel"><h2 data-i18n="allStatuses">СТАТУСЫ // ВСЕ КУРАТОРЫ</h2><div class="statuses" id="statuses"></div></article></section>
<div class="ticker" id="ticker"></div><section class="board"><div class="board-title"><h2 data-i18n="ratingTitle">РЕЙТИНГ КУРАТОРОВ // ПО ВЫПОЛНЕНИЮ ПЛАНА</h2><div class="hint" data-i18n="ratingHint">белая метка = срок оплаты наступил · отрыв = не оплатили до сегодня</div></div><div class="head"><span data-i18n="position">Поз</span><span data-i18n="curator">Куратор</span><span data-i18n="planTrack">Трасса к плану</span><span data-i18n="planFact">План / факт · сбор</span><span>%</span><span data-i18n="gap">Отрыв</span></div><div id="ranking"></div></section></div><section class="page subpage" id="weeksPage" hidden><div class="board-title panel"><h2 data-i18n="weeksTitle">НЕДЕЛИ // ДИНАМИКА ЗАКРЫТИЯ</h2><div class="hint" data-i18n="weeksHint">план, факт, процент, отрыв и сбор по каждой неделе</div></div><div class="week-grid" id="weekGrid"></div></section><section class="page subpage" id="upsellPage" hidden><div class="board-title panel"><h2 data-i18n="upsellTitle">UPSELL // ПАКЕТНЫЕ ПРОДАЖИ</h2><div class="hint" data-i18n="upsellHint">рейтинг по количеству оплаченных пакетов действующих учеников</div></div><div class="upsell-summary" id="upsellSummary"></div><section class="board"><div class="board-title"><h2 data-i18n="upsellRating">РЕЙТИНГ КУРАТОРОВ // UPSELL</h2><div class="hint" data-i18n="upsellOrder">сначала количество продаж, затем сумма</div></div><div class="upsell-head"><span data-i18n="position">Поз</span><span data-i18n="curator">Куратор</span><span data-i18n="salesTrack">Трасса продаж</span><span data-i18n="months">3 / 6 / 12 месяцев</span><span data-i18n="sales">Продажи</span><span data-i18n="amount">Сумма</span></div><div id="upsellRanking"></div></section><section class="data-table"><div class="upsell-filter-title"><h3 id="upsellListTitle">ВСЕ ПАКЕТНЫЕ ПРОДАЖИ</h3><button class="reset" data-upsell-reset data-i18n="showAll">Показать все</button></div><div class="data-head"><span data-i18n="curator">Куратор</span><span data-i18n="student">Ученик</span><span data-i18n="tariff">Тариф</span><span data-i18n="amount">Сумма</span><span data-i18n="paymentDate">Дата оплаты</span></div><div id="upsellRows"></div></section></section></main>
<div class="modal" id="modal" hidden><section class="dialog"><div class="dialog-head"><div><h2 id="modalTitle" data-i18n="students">Ученики</h2><div class="updated" id="modalCount"></div></div><button class="x" id="close">×</button></div><div class="filters"><input class="search" id="search" placeholder="Поиск по имени" data-i18n-placeholder="searchName"><label><span data-i18n="from">От</span> <input type="date" id="from"></label><label><span data-i18n="to">До</span> <input type="date" id="to"></label><details><summary data-i18n="statuses">Статусы</summary><div class="checks"><label><input type="checkbox" name="st" value="paid" checked> <span data-i18n="paid">Оплатили</span></label><label><input type="checkbox" name="st" value="finished" checked> <span data-i18n="finished">Завершили</span></label><label><input type="checkbox" name="st" value="referral" checked> <span data-i18n="referral">Реферал</span></label><label><input type="checkbox" name="st" value="frozen" checked> <span data-i18n="frozen">Заморожены</span></label><label><input type="checkbox" name="st" value="archive" checked> <span data-i18n="archive">Архив</span></label><label><input type="checkbox" name="st" value="overdue" checked> <span data-i18n="overdue">Должники</span></label><label><input type="checkbox" name="st" value="future" checked> <span data-i18n="notDue">Ожидается</span></label></div></details><details id="groupFilter"><summary data-i18n="groups">Группы</summary><div class="checks" id="groupChecks"></div></details><button class="reset" id="reset" data-i18n="reset">Сбросить</button></div><div class="students" id="studentList"></div></section></div>
<script id="data" type="application/json">__DATA__</script><script id="upsell-data" type="application/json">__UPSELL_DATA__</script><script id="collection-data" type="application/json">__COLLECTION_DATA__</script><script>
const DATA=JSON.parse(document.getElementById('data').textContent),UPSELL_DATA=JSON.parse(document.getElementById('upsell-data').textContent),COLLECTION_DATA=JSON.parse(document.getElementById('collection-data').textContent),TODAY='__TODAY__';
const I18N={ru:{brandDebt:'⚡ ДОЛГИ',brandCurators:'КУРАТОРЫ',updated:'обновлено',curators:'Кураторы',weeks:'Недели',cycleOption:'📅 Цикл 24.09–24.10',week1:'Неделя 24.09–30.09',week2:'Неделя 01.10–07.10',week3:'Неделя 08.10–14.10',week4:'Неделя 15.10–24.10',of:'из',debtorsPaid:'должников оплатили',needCollect:'Нужно собрать',collected:'Собрано',paid:'Оплатили',finished:'Завершили',referral:'Реферал',frozen:'Заморожены',archive:'Архив',stillOwe:'Ещё должны',allStatuses:'СТАТУСЫ // ВСЕ КУРАТОРЫ',ratingTitle:'РЕЙТИНГ КУРАТОРОВ // ПО ВЫПОЛНЕНИЮ ПЛАНА',ratingHint:'белая метка = срок оплаты наступил · отрыв = не оплатили до сегодня',position:'Поз',curator:'Куратор',planTrack:'Трасса к плану',planFact:'План / факт · сбор',gap:'Отрыв',weeksTitle:'НЕДЕЛИ // ДИНАМИКА ЗАКРЫТИЯ',weeksHint:'план, факт, процент, отрыв и сбор по каждой неделе',upsellTitle:'UPSELL // ПАКЕТНЫЕ ПРОДАЖИ',upsellHint:'рейтинг по количеству оплаченных пакетов действующих учеников',upsellRating:'РЕЙТИНГ КУРАТОРОВ // UPSELL',upsellOrder:'сначала количество продаж, затем сумма',salesTrack:'Трасса продаж',months:'3 / 6 / 12 месяцев',sales:'Продажи',amount:'Сумма',showAll:'Показать все',student:'Ученик',tariff:'Тариф',paymentDate:'Дата оплаты',students:'Ученики',searchName:'Поиск по имени',from:'От',to:'До',statuses:'Статусы',overdue:'Должники',notDue:'Ожидается',groups:'Группы',reset:'Сбросить',cycle:'ЦИКЛ',debtCollection:'СБОР ДОЛГОВ',plan:'План',fact:'Факт',percent:'Процент',owe:'Должны',monthsWord:'месяцев',saleWord:'продаж',all:'ВСЕ',noPackages:'Оплаченные пакетные продажи не найдены',noData:'Информация не найдена',paymentDateShort:'Дата оплаты'},uz:{brandDebt:'⚡ QARZLAR',brandCurators:'KURATORLAR',updated:'yangilandi',curators:'Kuratorlar',weeks:'Haftalar',cycleOption:'📅 Sikl 24.09–24.10',week1:'Hafta 24.09–30.09',week2:'Hafta 01.10–07.10',week3:'Hafta 08.10–14.10',week4:'Hafta 15.10–24.10',of:'/',debtorsPaid:'o‘quvchi to‘lov qildi',needCollect:'Yig‘ish kerak',collected:'Yig‘ildi',paid:'To‘lagan',finished:'Bitirdi',referral:'Referal',frozen:'Muzlagan',archive:'Arxiv',stillOwe:'Hali to‘lashi kerak',allStatuses:'STATUSLAR // BARCHA KURATORLAR',ratingTitle:'KURATORLAR REYTINGI // REJA BAJARILISHI',ratingHint:'oq belgi = to‘lov sanasi kelganlar · farq = bugungacha to‘lamaganlar',position:'O‘rin',curator:'Kurator',planTrack:'Rejaga yo‘l',planFact:'Reja / fakt · yig‘im',gap:'Farq',weeksTitle:'HAFTALAR // YOPILISH DINAMIKASI',weeksHint:'har hafta bo‘yicha reja, fakt, foiz, farq va yig‘im',upsellTitle:'UPSELL // PAKETLI SOTUVLAR',upsellHint:'amaldagi o‘quvchilarning to‘langan paketlari bo‘yicha reyting',upsellRating:'KURATORLAR REYTINGI // UPSELL',upsellOrder:'avval sotuvlar soni, keyin summa',salesTrack:'Sotuvlar yo‘li',months:'3 / 6 / 12 oy',sales:'Sotuvlar',amount:'Summa',showAll:'Barchasini ko‘rsatish',student:'O‘quvchi',tariff:'Tarif',paymentDate:'To‘lov sanasi',students:'O‘quvchilar',searchName:'Ism bo‘yicha qidirish',from:'Dan',to:'Gacha',statuses:'Statuslar',overdue:'Qarzdor',notDue:'Kutilmoqda',groups:'Guruhlar',reset:'Tozalash',cycle:'SIKL',debtCollection:'QARZ YIG‘IMI',plan:'Reja',fact:'Fakt',percent:'Foiz',owe:'Qarzdor',monthsWord:'oy',saleWord:'sotuv',all:'BARCHA',noPackages:'To‘langan paketli sotuvlar topilmadi',noData:'Ma’lumot topilmadi',paymentDateShort:'To‘lov sanasi'}};
let LANG=localStorage.getItem('dashboardLang')||'ru';const t=k=>I18N[LANG][k]||I18N.ru[k]||k;
const periods={all:['2026-09-24','2026-10-24','24.09–24.10'],w1:['2026-09-24','2026-09-30','24.09–30.09'],w2:['2026-10-01','2026-10-07','01.10–07.10'],w3:['2026-10-08','2026-10-14','08.10–14.10'],w4:['2026-10-15','2026-10-24','15.10–24.10']};
const $=id=>document.getElementById(id),money=n=>Number(n||0).toLocaleString(LANG==='uz'?'uz-UZ':'ru-RU'),compact=n=>(n/1e6).toFixed(1)+' mln',payDate=x=>x.last_paid?x.last_paid.slice(0,10).split('-').reverse().join('.'):"",upsellLabel=x=>{const m=String(x.upsell||'').match(/upsell\s*(3|6|12)/i);return m?'Upsell '+m[1]+' '+(LANG==='uz'?'oy':'мес'):''},rowDate=x=>x.delayed&&x.last_paid?x.last_paid.slice(0,10):x.due,status=x=>{const s=businessStatus(x);return s==='debt'?(isOverdue(x)?'overdue':'future'):s};let current=[],modalBase=[];
const collectionAmount=(a,b)=>COLLECTION_DATA.filter(x=>x.date>=a&&x.date<=b).reduce((s,x)=>s+x.amount,0);
function pageRows(){const [a,b]=periods[$('period').value];return DATA.filter(x=>rowDate(x)>=a&&rowDate(x)<=b&&x.curator!=='Biriktirilmagan')}
const debtEligible=x=>x.subscription_status==='active';
const isOverdue=x=>Number(x.balance||0)<0;
const businessStatus=x=>{const a=String(x.analytics_bucket||'').toLowerCase();if(a==='bit')return'finished';if(a==='referral'||x.is_referral||String(x.tariff||'').trim().toUpperCase()==='SARAFAN RADIO')return'referral';if(x.is_package||x.paid)return'paid';if(a==='frozen'||x.subscription_status==='freezed')return'frozen';if(a==='deleted'&&!(x.student_active&&x.subscription_status==='active'))return'archive';if(x.subscription_status==='archive'||x.subscription_status!=='active'||x.student_active===false)return'archive';return'debt'};
const isFact=x=>['paid','finished','referral'].includes(businessStatus(x));
const displayStatus=x=>{const s=businessStatus(x);if(LANG==='uz')return s==='finished'?'Bitirdi':s==='referral'?'Referal':s==='frozen'?'Muzlagan':s==='archive'?'Arxiv':s==='paid'?"To‘lagan":(isOverdue(x)?'Qarzdor':'Kutilmoqda');return s==='finished'?'Завершил':s==='referral'?'Реферал':s==='frozen'?'Заморожен':s==='archive'?'Архив':s==='paid'?'Оплатил':(isOverdue(x)?'Должник':'Ожидается')};
const displayClass=x=>{const s=businessStatus(x);return s==='paid'?'paid':s==='finished'?'delayed':s==='referral'?'future':s==='debt'?(isOverdue(x)?'overdue':'future'):'archive'};
const displayAmount=x=>businessStatus(x)==='paid'?x.amount:(businessStatus(x)==='debt'?x.debt_amount:0);
function button(value,type,extra=''){return `<button class="click" data-list="${type}" ${extra}>${value}</button>`}
function openRows(type,curator='',team='',limit=0){let rows=current.filter(x=>(!curator||x.curator===curator)&&(!team||x.team===team));if(type==='fact')rows=rows.filter(isFact);if(['paid','finished','referral','frozen','archive'].includes(type))rows=rows.filter(x=>businessStatus(x)===type);if(type==='debt')rows=rows.filter(x=>businessStatus(x)==='debt');if(type==='overdue'||type==='gap')rows=rows.filter(x=>businessStatus(x)==='debt'&&x.due<=TODAY);if(type==='future')rows=rows.filter(x=>businessStatus(x)==='debt'&&x.due>TODAY);rows.sort((a,b)=>rowDate(a).localeCompare(rowDate(b))||a.name.localeCompare(b.name));if(limit)rows=rows.slice(0,limit);modalBase=rows;const titles=LANG==='uz'?{all:'REJA tarkibi',fact:'Fakt: To‘lagan + Bitirdi + Referal',paid:'To‘lagan',finished:'Bitirdi',referral:'Referal',frozen:'Muzlagan',archive:'Arxiv',debt:'Barcha to‘lamaganlar',overdue:'Qarzdor',future:'Kutilmoqda',gap:'Farq'}:{all:'Состав плана',fact:'Факт: оплатили + завершили + реферал',paid:'Оплатили',finished:'Завершили',referral:'Реферал',frozen:'Заморожены',archive:'Архив',debt:'Все неоплатившие',overdue:'Должники',future:'Ожидается',gap:'Отрыв'};$('modalTitle').textContent=(curator?curator+' · ':'')+(titles[type]||t('students'));$('from').value=periods[$('period').value][0];$('to').value=periods[$('period').value][1];$('search').value='';document.querySelectorAll('[name=st]').forEach(x=>x.checked=true);const groups=[...new Set(rows.map(x=>x.group))].sort((a,b)=>a.localeCompare(b));$('groupChecks').innerHTML=groups.map(g=>`<label><input type="checkbox" name="grp" value="${g.replaceAll('&','&amp;').replaceAll('"','&quot;')}" checked> ${g}</label>`).join('');document.querySelectorAll('[name=grp]').forEach(x=>x.onchange=drawModal);$('modal').hidden=false;drawModal()}
function drawModal(){const q=$('search').value.toLowerCase(),a=$('from').value,b=$('to').value,allowed=[...document.querySelectorAll('[name=st]:checked')].map(x=>x.value),groups=[...document.querySelectorAll('[name=grp]:checked')].map(x=>x.value);const rows=modalBase.filter(x=>(!q||x.name.toLowerCase().includes(q))&&(!a||rowDate(x)>=a)&&(!b||rowDate(x)<=b)&&allowed.includes(status(x))&&groups.includes(x.group));$('modalCount').textContent=rows.length+(LANG==='uz'?' ta':'');$('studentList').innerHTML=rows.map(x=>`<div class="student"><div><a href="https://crm.junior-it.uz/account/student_list/detail/${x.id}" target="_blank">${x.name}</a><small>${x.curator} · ${x.group} · ${x.due}${x.delayed?(LANG==='uz'?' · oldingi oy':' · предыдущий месяц'):(x.partial?' · '+money(x.amount)+(LANG==='uz'?' / ':' оплачено из ')+money(x.required):'')}</small></div><span class="status-stack"><span class="pill ${displayClass(x)}">${displayStatus(x)}</span>${upsellLabel(x)?`<span class="upsell-pill">${upsellLabel(x)}</span>`:''}</span><strong class="payment-value">${money(displayAmount(x))} so‘m${x.last_paid&&x.amount>0?`<small>${t('paymentDateShort')}: ${payDate(x)}</small>`:''}</strong></div>`).join('')||`<p>${t('noData')}</p>`}
function render(){current=pageRows();const [periodStart,periodEnd]=periods[$('period').value],total=current.length,paid=current.filter(isFact),debt=current.filter(x=>businessStatus(x)==='debt'),over=debt.filter(x=>x.due<=TODAY),future=debt.filter(x=>x.due>TODAY),pct=total?Math.round(paid.length/total*100):0,amount=collectionAmount(periodStart,periodEnd),need=current.filter(x=>isFact(x)||businessStatus(x)==='debt').reduce((s,x)=>s+x.required,0);$('cycleTitle').textContent=t('cycle')+' '+periods[$('period').value][2]+' // '+t('debtCollection');$('heroPct').textContent=pct+'%';$('heroPaid').textContent=paid.length;$('heroTotal').textContent=total;$('heroBar').style.width=pct+'%';$('need').textContent=compact(need);$('collected').textContent=compact(amount);$('paidCard').textContent=paid.length;$('debtCard').textContent=debt.length;
const sts=[["TO'LAGAN",current.filter(x=>businessStatus(x)==='paid').length,'paid','var(--green)'],['BITIRDI',current.filter(x=>businessStatus(x)==='finished').length,'finished','var(--blue)'],['REFERAL',current.filter(x=>businessStatus(x)==='referral').length,'referral','var(--violet)'],['MUZLAGAN',current.filter(x=>businessStatus(x)==='frozen').length,'frozen','var(--orange)'],['ARXIV',current.filter(x=>businessStatus(x)==='archive').length,'archive','var(--gray)'],['QARZDOR',over.length,'overdue','var(--red)'],['KUTILMOQDA',future.length,'future','var(--orange)']];$('statuses').innerHTML=sts.map(x=>`<div class="status-row"><span>${x[0]}</span><div class="bar"><i style="width:${total?x[1]/total*100:0}%;background:${x[3]}"></i></div>${button(x[1],x[2])}</div>`).join('');
const groups={};current.forEach(x=>(groups[x.curator]??={name:x.curator,rows:[]}).rows.push(x));const rating=Object.values(groups).map(g=>{const p=g.rows.filter(isFact),d=g.rows.filter(x=>businessStatus(x)==='debt'),arrived=g.rows.filter(x=>x.due<=TODAY&&(isFact(x)||businessStatus(x)==='debt')),ov=d.filter(x=>x.due<=TODAY),expected=arrived.length,pct=g.rows.length?Math.round(p.length/g.rows.length*100):0;return {...g,total:g.rows.length,paid:p.length,debt:d.length,overdue:ov.length,expected,gap:-ov.length,pct,amount:g.rows.reduce((s,x)=>s+x.amount,0)}}).sort((a,b)=>b.pct-a.pct||b.paid-a.paid||a.name.localeCompare(b.name));const tickerText=LANG==='uz'?`fakt ${paid.length} / ${total} (${pct}%)　//　hali to‘lashi kerak ${debt.length}　//　yig‘ildi ${compact(amount)}　//　Har bir to‘lov — yopilgan qarz!　//　`:`факт ${paid.length} из ${total} (${pct}%)　//　ещё должны ${debt.length}　//　собрано ${compact(amount)}　//　Каждая оплата — закрытый долг!　//　`;$('ticker').innerHTML=`<span>${tickerText}${tickerText}</span>`;$('ranking').innerHTML=rating.map((g,i)=>`<div class="rank"><span class="pos">${i+1}</span><button class="person" data-list="all" data-curator="${g.name}"><b>${g.name}</b></button><span class="track"><i style="width:${Math.min(100,g.pct)}%"></i><em style="left:${Math.min(100,g.total?g.expected/g.total*100:0)}%"></em></span><span class="metric"><span class="metric-pair">${button(g.total,'all',`data-curator="${g.name}" title="${t('plan')}"`)}<span class="sep">/</span>${button(g.paid,'fact',`class="paid-count" data-curator="${g.name}" title="${t('fact')}"`)}</span><small>${compact(g.amount)}</small></span><span class="num">${button(g.pct+'%','all',`data-curator="${g.name}"`)}</span><span class="num bad">${button(g.gap,'gap',`data-curator="${g.name}"`)}</span></div>`).join('');}
function renderWeeks(){const keys=['w1','w2','w3','w4'];$('weekGrid').innerHTML=keys.map(k=>{const [a,b,label]=periods[k],r=DATA.filter(x=>rowDate(x)>=a&&rowDate(x)<=b&&x.curator!=='Biriktirilmagan'),f=r.filter(isFact),d=r.filter(x=>businessStatus(x)==='debt'),gap=d.filter(x=>x.due<=TODAY),pct=r.length?Math.round(f.length/r.length*100):0,amount=collectionAmount(a,b);return `<article class="week-card"><h3>${label}</h3><div class="progress"><i style="width:${pct}%"></i></div><div class="week-metrics"><div><small>${t('plan')}</small>${button(r.length,'all',`data-week="${k}"`)}</div><div><small>${t('fact')}</small>${button(f.length,'fact',`data-week="${k}"`)}</div><div><small>${t('percent')}</small><strong>${pct}%</strong></div><div><small>${t('gap')}</small>${button('-'+gap.length,'gap',`data-week="${k}"`)}</div><div><small>${t('collected')}</small><strong>${compact(amount)}</strong></div><div><small>${t('owe')}</small>${button(d.length,'debt',`data-week="${k}"`)}</div></div></article>`}).join('')}
function showUpsellDetails(curator='',months=''){const rows=UPSELL_DATA.filter(x=>(!curator||x.curator===curator)&&(!months||String(x.months)===String(months))).sort((a,b)=>String(b.sale_date).localeCompare(String(a.sale_date))||a.name.localeCompare(b.name));$('upsellListTitle').textContent=(curator?curator.toUpperCase():t('all'))+(months?' · '+months+' '+t('monthsWord'):'')+' // '+rows.length+' '+t('saleWord');$('upsellRows').innerHTML=rows.map(x=>`<div class="data-row"><b>${x.curator}</b><a class="click" href="https://crm.junior-it.uz/account/student_list/detail/${x.id}" target="_blank">${x.name}</a><span class="tariff-pill">Upsell ${x.months}</span><strong>${money(x.amount)} so‘m</strong><span>${x.sale_date}</span></div>`).join('')||`<p style="padding:20px">${t('noPackages')}</p>`}
function renderUpsell(){const rows=[...UPSELL_DATA],total=rows.length,maxSales=Math.max(1,...Object.values(rows.reduce((a,x)=>(a[x.curator]=(a[x.curator]||0)+1,a),{})));$('upsellSummary').innerHTML=['3','6','12'].map(m=>{const r=rows.filter(x=>String(x.months)===m),sum=r.reduce((s,x)=>s+x.amount,0);return `<div class="stat"><small>${m} ${t('monthsWord')}</small><button class="click" data-upsell-month="${m}">${r.length} ${t('saleWord')}</button><div>${compact(sum)}</div></div>`}).join('');const groups={};rows.forEach(x=>(groups[x.curator]??={name:x.curator,rows:[]}).rows.push(x));const rating=Object.values(groups).map(g=>({...g,total:g.rows.length,amount:g.rows.reduce((s,x)=>s+x.amount,0),m3:g.rows.filter(x=>x.months===3).length,m6:g.rows.filter(x=>x.months===6).length,m12:g.rows.filter(x=>x.months===12).length})).sort((a,b)=>b.total-a.total||b.amount-a.amount||a.name.localeCompare(b.name));$('upsellRanking').innerHTML=rating.map((g,i)=>{const share=total?Math.round(g.total/total*100):0;return `<div class="upsell-rank"><span class="pos">${i+1}</span><button class="person" data-upsell-curator="${g.name}"><b>${g.name}</b><small>${share}% ${LANG==='uz'?'barcha sotuvlar':'всех продаж'}</small></button><span class="track"><i style="width:${g.total/maxSales*100}%"></i></span><span class="package-mix"><span class="m3">3 ${LANG==='uz'?'oy':'мес'} · ${g.m3}</span><span class="m6">6 ${LANG==='uz'?'oy':'мес'} · ${g.m6}</span><span class="m12">12 ${LANG==='uz'?'oy':'мес'} · ${g.m12}</span></span><button class="click upsell-total" data-upsell-curator="${g.name}">${g.total}</button><strong class="upsell-amount">${compact(g.amount)}</strong></div>`}).join('');showUpsellDetails()}
function showPage(id){document.querySelectorAll('.page').forEach(x=>x.hidden=x.id!==id);document.querySelectorAll('[data-page]').forEach(x=>x.classList.toggle('active',x.dataset.page===id));$('period').style.display=id==='upsellPage'?'none':'';if(id==='weeksPage')renderWeeks();if(id==='upsellPage')renderUpsell()}
function applyLanguage(){document.documentElement.lang=LANG;document.querySelectorAll('[data-i18n]').forEach(x=>x.textContent=t(x.dataset.i18n));document.querySelectorAll('[data-i18n-placeholder]').forEach(x=>x.placeholder=t(x.dataset.i18nPlaceholder));document.querySelectorAll('[data-lang]').forEach(x=>x.classList.toggle('active',x.dataset.lang===LANG));const stamp=document.querySelector('.updated').dataset.generated;$('updatedTime').textContent=new Intl.DateTimeFormat(LANG==='uz'?'uz-UZ':'ru-RU',{day:'2-digit',month:'2-digit',year:'numeric',hour:'2-digit',minute:'2-digit',hour12:false}).format(new Date(stamp)).replace(',','');render();renderWeeks();renderUpsell();if(!$('modal').hidden)drawModal()}
document.addEventListener('click',e=>{const l=e.target.closest('[data-lang]');if(l){LANG=l.dataset.lang;localStorage.setItem('dashboardLang',LANG);applyLanguage();return}const p=e.target.closest('[data-page]');if(p){showPage(p.dataset.page);return}const uc=e.target.closest('[data-upsell-curator]');if(uc){showUpsellDetails(uc.dataset.upsellCurator);return}const um=e.target.closest('[data-upsell-month]');if(um){showUpsellDetails('',um.dataset.upsellMonth);return}if(e.target.closest('[data-upsell-reset]')){showUpsellDetails();return}const b=e.target.closest('[data-list]');if(b){if(b.dataset.week){$('period').value=b.dataset.week;render()}openRows(b.dataset.list,b.dataset.curator||'',b.dataset.team||'',Number(b.dataset.limit||0))}});$('period').onchange=render;$('close').onclick=()=>$('modal').hidden=true;$('modal').onclick=e=>{if(e.target===$('modal'))$('modal').hidden=true};['search','from','to'].forEach(id=>$(id).oninput=drawModal);document.querySelectorAll('[name=st]').forEach(x=>x.onchange=drawModal);$('reset').onclick=()=>{$('from').value='';$('to').value='';$('search').value='';document.querySelectorAll('[name=st],[name=grp]').forEach(x=>x.checked=true);drawModal()};applyLanguage();
const PAGE_BUILD='__GENERATED__';async function checkForUpdate(){try{const text=await fetch(location.pathname+'?refresh='+Date.now(),{cache:'no-store'}).then(r=>r.text());const doc=new DOMParser().parseFromString(text,'text/html');const stamp=(doc.querySelector('.updated')||{}).textContent||'';if(stamp&&!stamp.includes(PAGE_BUILD))location.reload()}catch(_){}}setInterval(checkForUpdate,5*60*1000);
</script></body></html>'''
    document = document.replace('data-i18n="needCollect">Нужно собрать',
                                'data-i18n="plan">План')
    document = document.replace('data-i18n="collected">Собрано',
                                'data-i18n="fact">Факт')
    document = document.replace(
        "need=current.filter(x=>isFact(x)||businessStatus(x)==='debt').reduce((s,x)=>s+x.required,0)",
        "need=current.reduce((s,x)=>s+x.required,0)")
    document = document.replace("businessStatus(x)==='debt'&&x.due<=TODAY",
                                "businessStatus(x)==='debt'&&isOverdue(x)")
    document = document.replace("businessStatus(x)==='debt'&&x.due>TODAY",
                                "businessStatus(x)==='debt'&&!isOverdue(x)")
    document = document.replace("debt.filter(x=>x.due<=TODAY)", "debt.filter(isOverdue)")
    document = document.replace("debt.filter(x=>x.due>TODAY)", "debt.filter(x=>!isOverdue(x))")
    document = document.replace(
        "g.rows.filter(x=>x.due<=TODAY&&(isFact(x)||businessStatus(x)==='debt'))",
        "g.rows.filter(x=>isFact(x)||(businessStatus(x)==='debt'&&isOverdue(x)))")
    document = document.replace("d.filter(x=>x.due<=TODAY)", "d.filter(isOverdue)")
    archive_previous_dashboard(OUT)
    ensure_legacy_cycle_archives(OUT)
    cycle_label = f"{short_date(CYCLE_START)}–{short_date(CYCLE_END)}"
    week_labels = [f"{short_date(start)}–{short_date(end)}" for start, end in CYCLE_WEEKS]
    old_labels = ["24.09–24.10", "24.09–30.09", "01.10–07.10", "08.10–14.10", "15.10–24.10"]
    for old, new in zip(old_labels, [cycle_label] + week_labels):
        document = document.replace(old, new)
    period_data = {"all": [CYCLE_START.isoformat(), CYCLE_END.isoformat(), cycle_label]}
    period_data.update({f"w{i}": [start.isoformat(), end.isoformat(), week_labels[i - 1]]
                        for i, (start, end) in enumerate(CYCLE_WEEKS, 1)})
    document = re.sub(r"const periods=\{all:.*?\};",
                      "const periods=" + json.dumps(period_data, ensure_ascii=False, separators=(",", ":")) + ";",
                      document)
    history = cycle_archive_options(OUT)
    if history:
        document = document.replace('<div class="controls">', '<div class="controls">' + history, 1)
    document = (document.replace("__DATA__", data_json)
                .replace("__UPSELL_DATA__", upsell_json)
                .replace("__COLLECTION_DATA__", collection_json)
                .replace("__GENERATED_ISO__", html.escape(audit["generated_iso"]))
                .replace("__GENERATED__", html.escape(audit["generated"]))
                .replace("__SITE_LABEL__", html.escape(SITE_LABEL))
                .replace("__TODAY__", audit["today"]))
    with open(OUT, "w", encoding="utf-8") as handle:
        handle.write(document)
    print(json.dumps({"output": OUT, "audit": audit, "records": len(records)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    render(*load_data())
