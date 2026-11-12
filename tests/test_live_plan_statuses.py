import unittest
from approved_cycle_plan import refresh_statuses


class LivePlanStatuses(unittest.TestCase):
    def classify(self, status='active', paid=False, **state):
        row = dict(id=19628, due='2026-11-05', required=420000, balance=-420000,
                   paid=paid, partial=False, approved_status='paid' if paid else 'debt',
                   approved_overdue=not paid, debt_amount=0 if paid else 420000)
        state = dict(sid=19628, subscription_id=77363, subscription_status=status, **state)
        result = refresh_statuses({}, [row], lambda sql: [state], '2026-11-12')
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['id'], 19628)
        self.assertEqual(result[0]['required'], 420000)
        return result[0]

    def test_archive_refusal_retains_plan_and_raw_balance(self):
        row = self.classify('archive', archive_reason="Vaqti yo'q")
        self.assertEqual(row['approved_status'], 'archive')
        self.assertEqual(row['balance'], -420000)
        self.assertFalse(row['approved_overdue'])

    def test_graduate_separated_from_refusal(self):
        self.assertEqual(self.classify('archive', archive_reason='Bitirdi')['approved_status'], 'finished')
        self.assertEqual(self.classify('active', archive_reason='Bitirdi')['approved_status'], 'debt')

    def test_manual_freeze_vs_debt_triggered_freeze(self):
        self.assertEqual(self.classify('freezed', freeze_type='Freezing', freeze_reason='Kasal')['approved_status'], 'frozen')
        self.assertEqual(self.classify('freezed', freeze_type='auto', freeze_reason='auto_overdue')['approved_status'], 'debt')
        self.assertEqual(self.classify('freezed', paid=True, freeze_type='auto')['approved_status'], 'paid')

    def test_referral_renewal_and_new_free_month(self):
        row = dict(id=15232, due='2026-11-12', required=420000, balance=0, paid=False,
                   approved_status='debt', approved_overdue=False, debt_amount=420000)
        state = dict(sid=15232, subscription_id=1, subscription_status='active',
                     tariff='sarafan radio', subscription_start='2026-10-12', subscription_end='2026-11-12')
        classify = lambda: refresh_statuses({}, [row], lambda sql: [state], '2026-11-12')[0]
        self.assertEqual(classify()['approved_status'], 'debt')
        state.update(subscription_start='2026-11-12', subscription_end='2026-12-12')
        result = classify()
        self.assertEqual(result['approved_status'], 'referral')
        self.assertFalse(result['paid'])
        self.assertEqual(result['debt_amount'], 0)

    def test_state_recomputed_on_every_refresh(self):
        first = self.classify('archive', archive_reason="Vaqti yo'q")
        first.update(paid=True, debt_amount=0, balance=0)
        active = dict(sid=19628, subscription_status='active', subscription_id=90000)
        result = refresh_statuses({}, [first], lambda sql: [active], '2026-11-13')[0]
        self.assertEqual(result['approved_status'], 'paid')

    def test_missing_rows_fail_closed(self):
        with self.assertRaises(RuntimeError):
            refresh_statuses({}, [dict(id=19628)], lambda sql: [], '2026-11-12')
