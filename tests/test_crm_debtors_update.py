"""Offline CRM HTML fixtures: no CRM requests, file rendering or deployment."""
import copy
import datetime as dt
import html
from pathlib import Path
import re
import unittest
from unittest.mock import patch

import crm_debtors_update as crm


PAGE = (Path(__file__).parent / "fixtures" / "crm_debtors_page.html").read_text()
CYCLES = (
    (dt.date(2026, 9, 24), dt.date(2026, 10, 24)),
    (dt.date(2026, 8, 25), dt.date(2026, 9, 23)),
)
CURATOR = "Fotimabonu Abdulkhakova"
LABELS = {"paid": "To'landi", "qarzdor": "Qarzdor", "frozen": "Muzlatilgan"}


def billing_rows(start, end):
    # 17897 reproduces the reported two-month frozen student. The other
    # students exercise two payments and different statuses for the same ID.
    rows = []
    for sid, statuses in (
        (17897, ("frozen", "frozen")),
        (17900, ("paid", "paid")),
        (17901, ("paid", "qarzdor")),
    ):
        for due, amount, status in zip((start, end), (390000, 420000), statuses):
            rows.append(dict(sid=sid, date=due.strftime("%d.%m.%Y"),
                             status=status, plan=amount,
                             paid=amount if status == "paid" else 0))
    return rows


def page(rows, total=None):
    body = []
    for pos, row in enumerate(rows, 1):
        name = "Fixture student"
        if row.get("sid") is not None:
            name = f'<a href="/account/student_list/detail/{row["sid"]}">{name}</a>'
        cells = [str(pos), html.escape(row["date"]), name, "Aktiv", CURATOR,
                 "Standart", str(row["plan"]), LABELS[row["status"]],
                 str(row["plan"] - row["paid"]), str(row["paid"])]
        body.append("<tr>" + "".join(f"<td>{cell}</td>" for cell in cells) + "</tr>")
    return PAGE.format(
        total=len(rows) if total is None else total,
        plan=sum(r["plan"] for r in rows), fact=sum(r["paid"] for r in rows),
        frozen=sum(r["status"] == "frozen" for r in rows), deleted=0,
        rows="".join(body),
    )


class BillingRowTests(unittest.TestCase):
    def run_main(self, start, end, overrides=None):
        rows = billing_rows(start, end)
        overrides = overrides or {}
        rendered = []
        queries = []

        def fetch(op, start_arg=None, end_arg=None, status="", curator=""):
            if start_arg is not None:
                self.assertEqual((start_arg, end_arg), (start, end))
            selected = [r for r in rows if not status or r["status"] == status]
            if curator and curator != "13799":
                selected = []
            label = f"curator {curator}" if curator else status or "all"
            return overrides[label] if label in overrides else page(selected)

        def mcp_query(sql):
            queries.append(sql)
            return [dict(sid=sid, admin_id=13799, cashier_id=101,
                         admin_name=CURATOR, cashier_name="Fixture cashier")
                    for sid in (17897, 17900, 17901)]

        def render(*args):
            rendered.append((copy.deepcopy(args), copy.deepcopy(crm.ui.DETAIL_DATA),
                             copy.deepcopy(crm.ui.CASHIER_ROWS)))

        with patch.dict(vars(crm.ui)), \
             patch.multiple(crm, START=start, END=end,
                            PERIODS=crm.cycle_periods(start, end),
                            REQUESTED_CYCLE=start.isoformat()), \
             patch.object(crm, "crm_session", return_value=object()), \
             patch.object(crm, "fetch", side_effect=fetch), \
             patch.object(crm, "mcp_query", side_effect=mcp_query), \
             patch.object(crm, "archived_cycles", return_value=[]), \
             patch.object(crm.ui, "render", side_effect=render):
            crm.main()
        return rendered, queries

    def test_both_cycles_keep_rows_statuses_sums_and_student_group_lookup(self):
        for start, end in CYCLES:
            with self.subTest(cycle=(start, end)):
                self.assertEqual(crm.cycle_end_for_start(start), end)
                rendered, queries = self.run_main(start, end)
                args, details, cashiers = rendered[0]
                self.assertEqual(args[1:3], (start, end))
                self.assertEqual(args[4:6], (6, 2430000))
                curator = next(r for r in args[3] if r["short"] == "Fotima")
                self.assertEqual((curator["plan"], curator["paid"], curator["muz"],
                                  curator["debt"], curator["sob"]), (6, 3, 2, 1, 1200000))
                self.assertEqual(len(details), 6)
                self.assertEqual(len({(x["id"], x["period"]) for x in details}), 6)
                frozen = [x for x in details if x["id"] == 17897]
                self.assertEqual([x["bucket"] for x in frozen], ["frozen", "frozen"])
                self.assertEqual([x["period"] for x in frozen],
                                 [start.strftime("%d.%m.%Y"), end.strftime("%d.%m.%Y")])
                self.assertEqual([x["debt"] for x in frozen], [390000, 420000])
                self.assertEqual([x["paid"] for x in details if x["id"] == 17900],
                                 [390000, 420000])
                self.assertEqual([x["bucket"] for x in details if x["id"] == 17901],
                                 ["paid", "debt"])
                self.assertEqual((cashiers[0]["plan"], cashiers[0]["plansum"],
                                  cashiers[0]["sob"]), (6, 2430000, 1200000))
                self.assertEqual(len(queries), 1)
                ids = re.search(r"STUDENT_ID IN \((.*?)\)", queries[0]).group(1).split(",")
                self.assertCountEqual(ids, ["17897", "17900", "17901"])
                # Weekly slicing must also preserve each billing row and sum.
                self.assertEqual(sum(len(x[1]) for x in rendered[1:]), 6)
                self.assertEqual(sum(x[0][5] for x in rendered[1:]), 2430000)
                self.assertEqual(sum(r["sob"] for x in rendered[1:] for r in x[0][3]),
                                 1200000)

    def test_duplicate_in_one_filter_identifies_key_and_filters(self):
        start, end = CYCLES[0]
        row = billing_rows(start, end)[0]
        with self.assertRaisesRegex(
            RuntimeError, "student_id=17897, due=2026-09-24, "
                          "previous_filter=frozen, current_filter=frozen"
        ):
            self.run_main(start, end, {"frozen": page([row, row])})

    def test_duplicate_across_filters_identifies_both_filters(self):
        start, end = CYCLES[0]
        rows = billing_rows(start, end)
        # The same billing row is returned by qarzdor and frozen.
        duplicate = dict(rows[-1], status="frozen")
        with self.assertRaisesRegex(
            RuntimeError, "student_id=17901, due=2026-10-24, "
                          "previous_filter=qarzdor, current_filter=frozen"
        ):
            self.run_main(start, end, {"frozen": page(rows[:2] + [duplicate])})

    def test_missing_and_zero_id_fail_separately(self):
        start, end = CYCLES[0]
        for sid in (None, 0):
            with self.subTest(sid=sid), self.assertRaisesRegex(
                RuntimeError, "CRM missing student ID in frozen"
            ):
                row = dict(billing_rows(start, end)[0], sid=sid)
                self.run_main(start, end, {"frozen": page([row])})

    def test_invalid_dates_never_default_to_cycle_start(self):
        start, end = CYCLES[0]
        for date in ("", "31.09.2026", "not-a-date"):
            with self.subTest(date=date), self.assertRaisesRegex(
                RuntimeError, "CRM invalid due date: student_id=17897, .*source=frozen"
            ):
                row = dict(billing_rows(start, end)[0], date=date)
                self.run_main(start, end, {"frozen": page([row])})

    def test_duplicate_general_and_curator_rows_are_rejected(self):
        start, end = CYCLES[0]
        rows = billing_rows(start, end)
        for source in ("all", "curator 13799"):
            with self.subTest(source=source), self.assertRaisesRegex(
                RuntimeError, f"previous_filter={source}, current_filter={source}"
            ):
                self.run_main(start, end, {source: page(rows + [rows[0]])})

    def test_general_and_curator_lookups_require_matching_date(self):
        start, end = CYCLES[0]
        rows = billing_rows(start, end)
        rows[0] = dict(rows[0], date="25.09.2026")
        for source in ("all", "curator 13799"):
            with self.subTest(source=source), self.assertRaisesRegex(
                RuntimeError, "row missing from .*status filters: "
                              "student_id=17897, due=2026-09-25"
            ):
                self.run_main(start, end, {source: page(rows)})

    def test_card_total_still_must_equal_billing_row_count(self):
        start, end = CYCLES[0]
        rows = billing_rows(start, end)
        with self.assertRaisesRegex(RuntimeError, "CRM frozen filter card/table mismatch"):
            self.run_main(start, end, {"frozen": page(rows[:2], total=1)})


if __name__ == "__main__":
    unittest.main()
