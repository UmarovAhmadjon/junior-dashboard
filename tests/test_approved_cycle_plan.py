import unittest
import approved_cycle_plan


class ApprovedCycleTests(unittest.TestCase):
    def test_user_approved_totals_and_excluded_cases(self):
        plan = approved_cycle_plan.load('2026-10-25', '2026-11-23')
        rows = plan['records']
        self.assertEqual(len(rows), 1517)
        self.assertEqual(sum(r['approved_overdue'] for r in rows), 267)
        self.assertTrue(all(r['subscription_status'] in ('active', 'freezed') for r in rows))
        self.assertFalse({15982, 14857, 19167} & {r['id'] for r in rows})
        self.assertTrue(any(r['subscription_status'] == 'freezed' and r['approved_overdue'] for r in rows))

    def test_snapshot_does_not_override_other_cycles(self):
        self.assertIsNone(approved_cycle_plan.load('2026-09-24', '2026-10-24'))
        self.assertIsNone(approved_cycle_plan.load('2026-11-25', '2026-12-24'))

    def test_approved_data_does_not_add_layout_or_styles(self):
        plan = approved_cycle_plan.load('2026-10-25', '2026-11-23')
        original = '<style>.panel{color:red}</style></head><div class="page" id="curatorPage"><div class="hero">existing layout</div></div>'
        self.assertEqual(approved_cycle_plan.apply_document(original, plan), original)

    def test_existing_debt_card_opens_overdue_subset(self):
        plan = approved_cycle_plan.load('2026-10-25', '2026-11-23')
        original = '<button data-list="debt" id="debtCard">267</button>'
        self.assertEqual(approved_cycle_plan.apply_document(original, plan),
                         '<button data-list="overdue" id="debtCard">267</button>')
