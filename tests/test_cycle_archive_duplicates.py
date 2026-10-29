import datetime as dt
import json
import os
import tempfile
import unittest
from unittest import mock

import mcp_dashboard_build as dashboard


class CurrentCycleArchiveDuplicateTests(unittest.TestCase):
    def run_correction(self, current, previous, protected=None):
        with tempfile.TemporaryDirectory() as directory:
            snapshot = os.path.join(directory, "2026-10-25.json")
            closing = os.path.join(directory, "2026-09-24-closing.json")
            with open(snapshot, "w", encoding="utf-8") as handle:
                json.dump({"records": current}, handle)
            with open(closing, "w", encoding="utf-8") as handle:
                json.dump({"cycle_end": "2026-10-24", "records": previous}, handle)
            confirmed = {"2026-10-25": set(protected or [])}
            with (mock.patch.object(dashboard, "REQUESTED_CYCLE", ""),
                  mock.patch.object(dashboard, "CYCLE_START", dt.date(2026, 10, 25)),
                  mock.patch.object(dashboard, "SNAPSHOT_DIR", directory),
                  mock.patch.object(dashboard, "CONFIRMED_CYCLE_MEMBER_IDS", confirmed)):
                rows, removed = dashboard.remove_previous_cycle_archive_duplicates(
                    current, snapshot)
            with open(snapshot, encoding="utf-8") as handle:
                saved = json.load(handle)
        return rows, removed, saved

    def test_archive_without_new_charge_is_kept_only_in_previous_cycle(self):
        current = [{"id": 18903, "student_active": False,
                    "subscription_status": "archive", "current_cycle_taken": False}]
        previous = [{"id": 18903, "debt_amount": 410000,
                     "current_cycle_taken": True, "analytics_bucket": "debt"}]
        rows, removed, saved = self.run_correction(current, previous)
        self.assertEqual(rows, [])
        self.assertEqual(removed, 1)
        self.assertEqual(saved["records"], [])

    def test_current_freeze_is_not_removed_by_archive_rule(self):
        current = [{"id": 17716, "student_active": True,
                    "subscription_status": "freezed", "current_cycle_taken": False}]
        previous = [{"id": 17716, "debt_amount": 420000,
                     "current_cycle_taken": True, "analytics_bucket": "frozen"}]
        rows, removed, _ = self.run_correction(current, previous)
        self.assertEqual([row["id"] for row in rows], [17716])
        self.assertEqual(removed, 0)

    def test_confirmed_current_cycle_member_is_preserved(self):
        current = [{"id": 14857, "student_active": False,
                    "subscription_status": "archive", "current_cycle_taken": False}]
        previous = [{"id": 14857, "debt_amount": 420000,
                     "current_cycle_taken": True, "analytics_bucket": "debt"}]
        rows, removed, _ = self.run_correction(current, previous, protected={14857})
        self.assertEqual([row["id"] for row in rows], [14857])
        self.assertEqual(removed, 0)


if __name__ == "__main__":
    unittest.main()
