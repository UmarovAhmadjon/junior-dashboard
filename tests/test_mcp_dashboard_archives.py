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
            "payments": 1, "analytics_bucket": "debt",
        }]
        with tempfile.TemporaryDirectory() as directory:
            snapshot = os.path.join(directory, "snapshot.json")
            with open(snapshot, "w", encoding="utf-8") as handle:
                json.dump({"records": baseline}, handle)
            live = [{**baseline[0], "debt_amount": 420000, "amount": 0}]
            late = [{"sid": 7, "amount": 500000, "payment_count": 1,
                     "last_paid": "2026-10-25 09:00:00"}]
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


if __name__ == "__main__":
    unittest.main()
