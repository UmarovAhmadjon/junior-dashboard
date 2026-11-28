import copy
import unittest

import upsell_cycle_transfers as transfers


class UpsellCycleTransferTests(unittest.TestCase):
    def setUp(self):
        self.batch = transfers.load()[0]
        self.source = (self.batch["from_start"], self.batch["from_end"])
        self.target = (self.batch["to_start"], self.batch["to_end"])

    def test_approved_thirteen_move_once_and_keep_real_payment_dates(self):
        sales = copy.deepcopy(self.batch["sales"])
        original = copy.deepcopy(sales)
        self.assertEqual(len(sales), 13)
        self.assertEqual(sum(row["amount"] for row in sales), 13860000)
        self.assertEqual(transfers.apply(sales, *self.source), [])
        moved = transfers.apply([], *self.target)
        self.assertEqual(len(moved), 13)
        self.assertEqual([r["sale_date"] for r in moved],
                         [r["sale_date"] for r in original])
        self.assertEqual(transfers.apply(moved, *self.target), moved)
        self.assertEqual(sales, original)

    def test_shamshod_legacy_duplicate_is_replaced_not_added_twice(self):
        legacy = dict(self.batch["legacy_replacements"][0], name="Murodjonov Shamshod")
        moved = transfers.apply([legacy], *self.target)
        same_student = [r for r in moved if r["id"] == 14772]
        self.assertEqual(len(same_student), 1)
        self.assertEqual(same_student[0]["crm_charge_id"], 248259)
        self.assertEqual(same_student[0]["sale_date"], "27.11.2026")

    def test_new_package_for_same_student_and_other_cycles_are_untouched(self):
        later = dict(self.batch["sales"][0], crm_charge_id=999999,
                     sale_date="01.12.2026")
        self.assertEqual(transfers.apply([later], *self.source), [later])
        self.assertIn(later, transfers.apply([later], *self.target))
        self.assertEqual(transfers.apply(self.batch["sales"],
                                        "2026-12-24", "2027-01-23"),
                         self.batch["sales"])

    def test_other_legacy_sale_for_same_student_is_preserved(self):
        old = dict(self.batch["legacy_replacements"][0], sale_date="02.11.2026")
        self.assertIn(old, transfers.apply([old], *self.target))


if __name__ == "__main__":
    unittest.main()
