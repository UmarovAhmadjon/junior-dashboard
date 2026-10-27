import datetime as dt
import json
import os
import tempfile
import unittest
from unittest import mock

import mcp_dashboard_build as dashboard


class ArchivedCyclePaymentTests(unittest.TestCase):
    def test_late_payment_is_capped_by_frozen_old_debt(self):
        baseline = [{
            "id": 7, "required": 420000, "amount": 20000,
            "debt_amount": 400000, "paid": False, "partial": True,
            "payments": 0, "analytics_bucket": "debt",
        }]
        with tempfile.TemporaryDirectory() as directory:
            snapshot = os.path.join(directory, "snapshot.json")
            with open(snapshot, "w", encoding="utf-8") as handle:
                json.dump({"records": baseline}, handle)
            live = [{**baseline[0], "debt_amount": 420000, "amount": 0}]
            late = [{"ID": 20, "sid": 7, "STUDENT_TYPE": "student",
                     "AMOUNT": 500000, "TRANSACTION_DATE": "2026-10-25 09:00:00"}]
            with (mock.patch.object(dashboard, "REQUESTED_CYCLE", "2026-09-24"),
                  mock.patch.object(dashboard, "CYCLE_END", dt.date(2026, 10, 24)),
                  mock.patch.object(dashboard, "LEGACY_CLOSING_COMMITS", {}),
                  mock.patch.object(dashboard, "query", return_value=late)):
                rows, applied = dashboard.apply_late_payments_to_archived_cycle(live, snapshot)
        self.assertEqual(applied, 400000)
        self.assertEqual(rows[0]["amount"], 420000)
        self.assertEqual(rows[0]["debt_amount"], 0)
        self.assertTrue(rows[0]["paid"])
        self.assertEqual(rows[0]["analytics_bucket"], "paid")

    def test_backdated_payment_entered_after_close_is_applied(self):
        baseline = [{
            "id": 9, "required": 420000, "amount": 0,
            "debt_amount": 420000, "paid": False, "partial": False,
            "payments": 1, "last_paid": "2026-09-24 00:00:00",
            "analytics_bucket": "debt",
        }]
        current_transactions = [
            {"ID": 10, "sid": 9, "STUDENT_TYPE": "new_student",
             "AMOUNT": 420000, "TRANSACTION_DATE": "2026-09-24 00:00:00"},
            {"ID": 20, "sid": 9, "STUDENT_TYPE": "student",
             "AMOUNT": 420000, "TRANSACTION_DATE": "2026-10-21 00:00:00"},
        ]
        with tempfile.TemporaryDirectory() as directory:
            snapshot = os.path.join(directory, "snapshot.json")
            closing = snapshot.replace(".json", "-closing.json")
            for path in (snapshot, closing):
                with open(path, "w", encoding="utf-8") as handle:
                    json.dump({"records": baseline}, handle)
            with (mock.patch.object(dashboard, "REQUESTED_CYCLE", "2026-09-24"),
                  mock.patch.object(dashboard, "CYCLE_START", dt.date(2026, 9, 24)),
                  mock.patch.object(dashboard, "CYCLE_END", dt.date(2026, 10, 24)),
                  mock.patch.object(dashboard, "query", return_value=current_transactions)):
                rows, applied = dashboard.apply_late_payments_to_archived_cycle(
                    [{**baseline[0]}], snapshot)
        self.assertEqual(applied, 420000)
        self.assertTrue(rows[0]["paid"])
        self.assertEqual(rows[0]["debt_amount"], 0)

    def test_confirmed_bitirdi_correction_updates_archive_fact(self):
        baseline = [{
            "id": 8, "required": 390000, "amount": 0,
            "debt_amount": 390000, "paid": False, "partial": False,
            "payments": 0, "analytics_bucket": "frozen",
        }]
        with tempfile.TemporaryDirectory() as directory:
            snapshot = os.path.join(directory, "snapshot.json")
            closing = snapshot.replace(".json", "-closing.json")
            for path in (snapshot, closing):
                with open(path, "w", encoding="utf-8") as handle:
                    json.dump({"records": baseline}, handle)
            live = [{**baseline[0], "analytics_bucket": "bit"}]
            with (mock.patch.object(dashboard, "REQUESTED_CYCLE", "2026-09-24"),
                  mock.patch.object(dashboard, "CYCLE_END", dt.date(2026, 10, 24)),
                  mock.patch.object(dashboard, "query", return_value=[])):
                rows, applied = dashboard.apply_late_payments_to_archived_cycle(live, snapshot)
        self.assertEqual(applied, 0)
        self.assertEqual(rows[0]["analytics_bucket"], "bit")

    def test_later_freeze_removes_unpaid_student_from_debt(self):
        baseline = [{
            "id": 10, "required": 420000, "amount": 0,
            "debt_amount": 420000, "paid": False, "partial": False,
            "payments": 0, "analytics_bucket": "debt",
            "subscription_status": "active",
        }]
        with tempfile.TemporaryDirectory() as directory:
            snapshot = os.path.join(directory, "snapshot.json")
            closing = snapshot.replace(".json", "-closing.json")
            for path in (snapshot, closing):
                with open(path, "w", encoding="utf-8") as handle:
                    json.dump({"records": baseline}, handle)
            live = [{**baseline[0], "analytics_bucket": "frozen",
                     "subscription_status": "freezed"}]
            with (mock.patch.object(dashboard, "REQUESTED_CYCLE", "2026-09-24"),
                  mock.patch.object(dashboard, "CYCLE_END", dt.date(2026, 10, 24)),
                  mock.patch.object(dashboard, "query", return_value=[])):
                rows, _ = dashboard.apply_late_payments_to_archived_cycle(live, snapshot)
        self.assertEqual(rows[0]["analytics_bucket"], "frozen")
        self.assertEqual(rows[0]["subscription_status"], "freezed")

    def test_bitirdi_remains_bitirdi_after_late_payment(self):
        baseline = [{
            "id": 11, "required": 390000, "amount": 0,
            "debt_amount": 390000, "paid": False, "partial": False,
            "payments": 0, "analytics_bucket": "frozen",
        }]
        payment = [{"ID": 30, "sid": 11, "STUDENT_TYPE": "student",
                    "AMOUNT": 390000, "TRANSACTION_DATE": "2026-10-25 00:00:00"}]
        with tempfile.TemporaryDirectory() as directory:
            snapshot = os.path.join(directory, "snapshot.json")
            closing = snapshot.replace(".json", "-closing.json")
            for path in (snapshot, closing):
                with open(path, "w", encoding="utf-8") as handle:
                    json.dump({"records": baseline}, handle)
            live = [{**baseline[0], "analytics_bucket": "bit"}]
            with (mock.patch.object(dashboard, "REQUESTED_CYCLE", "2026-09-24"),
                  mock.patch.object(dashboard, "CYCLE_END", dt.date(2026, 10, 24)),
                  mock.patch.object(dashboard, "query", return_value=payment)):
                rows, _ = dashboard.apply_late_payments_to_archived_cycle(live, snapshot)
        self.assertTrue(rows[0]["paid"])
        self.assertEqual(rows[0]["analytics_bucket"], "bit")

    def test_confirmed_archive_bucket_overrides_later_generic_paid_status(self):
        baseline = [{
            "id": 9660, "required": 390000, "amount": 390000,
            "debt_amount": 0, "paid": True, "partial": False,
            "payments": 1, "analytics_bucket": "paid",
        }]
        with tempfile.TemporaryDirectory() as directory:
            snapshot = os.path.join(directory, "snapshot.json")
            closing = snapshot.replace(".json", "-closing.json")
            for path in (snapshot, closing):
                with open(path, "w", encoding="utf-8") as handle:
                    json.dump({"records": baseline}, handle)
            live = [{**baseline[0], "analytics_bucket": "paid"}]
            with (mock.patch.object(dashboard, "REQUESTED_CYCLE", "2026-09-24"),
                  mock.patch.object(dashboard, "CYCLE_START", dt.date(2026, 9, 24)),
                  mock.patch.object(dashboard, "CYCLE_END", dt.date(2026, 10, 24)),
                  mock.patch.object(dashboard, "query", return_value=[])):
                rows, _ = dashboard.apply_late_payments_to_archived_cycle(live, snapshot)
        self.assertTrue(rows[0]["paid"])
        self.assertEqual(rows[0]["analytics_bucket"], "bit")

    def test_any_closing_bitirdi_cannot_be_downgraded_to_archive(self):
        baseline = [{
            "id": 15245, "required": 420000, "amount": 0,
            "debt_amount": 420000, "paid": False, "partial": False,
            "payments": 0, "analytics_bucket": "bit",
            "subscription_status": "archive",
        }]
        with tempfile.TemporaryDirectory() as directory:
            snapshot = os.path.join(directory, "snapshot.json")
            closing = snapshot.replace(".json", "-closing.json")
            for path in (snapshot, closing):
                with open(path, "w", encoding="utf-8") as handle:
                    json.dump({"records": baseline}, handle)
            live = [{**baseline[0], "analytics_bucket": "deleted"}]
            with (mock.patch.object(dashboard, "REQUESTED_CYCLE", "2026-09-24"),
                  mock.patch.object(dashboard, "CYCLE_START", dt.date(2026, 9, 24)),
                  mock.patch.object(dashboard, "CYCLE_END", dt.date(2026, 10, 24)),
                  mock.patch.object(dashboard, "query", return_value=[])):
                rows, _ = dashboard.apply_late_payments_to_archived_cycle(live, snapshot)
        self.assertEqual(rows[0]["analytics_bucket"], "bit")


if __name__ == "__main__":
    unittest.main()
