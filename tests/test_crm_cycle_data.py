import datetime as dt
import unittest

import crm_cycle_data as cycle
import mcp_dashboard_build as dashboard


class CollectionCycleTests(unittest.TestCase):
    start = dt.date(2026, 11, 24)
    end = dt.date(2026, 12, 23)

    def test_rollover_has_no_gap_or_overlap_and_keeps_history(self):
        for day, expected in [
            ('2026-11-23', ('2026-10-25', '2026-11-23')),
            ('2026-11-24', ('2026-11-24', '2026-12-23')),
            ('2026-12-23', ('2026-11-24', '2026-12-23')),
            ('2026-12-24', ('2026-12-24', '2027-01-23')),
            ('2027-01-01', ('2026-12-24', '2027-01-23')),
        ]:
            self.assertEqual(tuple(map(str, dashboard.current_cycle(dt.date.fromisoformat(day)))), expected)
        self.assertEqual(str(dashboard.cycle_end_for_start(dt.date(2026, 9, 24))), '2026-10-24')

    def test_all_payment_days_are_in_the_new_cycle(self):
        dates = [cycle.monthly_due(day, self.start, self.end) for day in range(1, 32)]
        self.assertTrue(all(self.start <= d <= self.end for d in dates))
        self.assertEqual(str(dates[22]), '2026-12-23')
        self.assertEqual(str(dates[23]), '2026-11-24')
        self.assertIsNone(cycle.monthly_due(0, self.start, self.end))

    def test_package_expiry_is_obligation_not_monthly_day(self):
        sub = dict(price=1080000, tariff='420', pay_day=28,
                   subscription_start='2026-08-28', subscription_end='2026-11-28')
        due, required, source, _, _ = cycle.obligation(sub, [], self.start, self.end)
        self.assertEqual((str(due), required, source), ('2026-11-28', 1080000, 'package_renewal'))
        sub.update(subscription_start='2026-11-20', subscription_end='2027-02-20')
        self.assertGreater(cycle.obligation(sub, [], self.start, self.end)[0], self.end)
        debit = [dict(taken_at='2026-11-24', charge_amount=1080000)]
        self.assertEqual(cycle.obligation(sub, debit, self.start, self.end)[:3],
                         (self.start, 1080000, 'crm_debit'))

    def state(self, balance, due='2026-11-28', charged=0, paid=0):
        charges = [dict(taken_at=due, charge_amount=charged)] if charged else []
        receipts = [dict(paid_date='2026-11-25', amount=paid)] if paid else []
        return cycle.financial_state(420000, dt.date.fromisoformat(due), dt.date(2026, 11, 25),
                                     balance, charges, receipts)

    def test_future_zero_and_late_old_payment_are_not_prepaid(self):
        for cash in (0, 420000):
            row = self.state(0, paid=cash)
            self.assertFalse(row['paid'])
            self.assertFalse(row['approved_overdue'])
            self.assertEqual(row['debt_amount'], 420000)
        self.assertTrue(self.state(420000, paid=420000)['paid'])

    def test_actual_charge_settlement_and_partial_balance(self):
        self.assertTrue(self.state(0, due='2026-11-24', charged=420000, paid=420000)['paid'])
        partial = self.state(-170000, due='2026-11-24', charged=420000, paid=250000)
        self.assertFalse(partial['paid'])
        self.assertTrue(partial['approved_overdue'])
        self.assertEqual(partial['debt_amount'], 170000)
        self.assertTrue(partial['partial'])
        self.assertFalse(self.state(0, due='2026-11-24')['paid'])

    def test_plan_member_survives_status_change_and_new_members_are_added(self):
        baseline = [dict(id=1, curator='A', due='2026-11-28', required=420000)]
        live = [dict(id=1, curator='B', due='2026-12-15', required=390000,
                     subscription_status='archive'),
                dict(id=2, curator='B', due='2026-12-23', required=390000)]
        rows = cycle.merge_plan(live, baseline)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]['due'], '2026-11-28')
        self.assertEqual(rows[0]['curator'], 'A')
        self.assertEqual(rows[0]['subscription_status'], 'archive')
        with self.assertRaises(RuntimeError):
            cycle.merge_plan(live[1:], baseline)
        live[0].update(due='2026-11-25', required=1080000, obligation_source='crm_debit')
        changed = cycle.merge_plan(live, baseline)[0]
        self.assertEqual((changed['due'], changed['required'], changed['curator']),
                         ('2026-11-25', 1080000, 'A'))

    def test_split_sale_completion_and_prepaid_package_are_not_duplicated(self):
        charge = dict(package_price=1080000, prior_balance=0, live_balance=0, charge_date='2026-11-13')
        receipts = [dict(amount=420000, paid_date='2026-11-13'), dict(amount=660000, paid_date='2026-11-24')]
        self.assertEqual(cycle.package_completed(charge, receipts)[:10], '2026-11-24')
        charge.update(prior_balance=1080000, charge_date='2026-11-25')
        self.assertEqual(cycle.package_completed(charge, [dict(amount=1080000, paid_date='2026-11-21')])[:10], '2026-11-21')
        charge.update(prior_balance=0, live_balance=-660000)
        self.assertIsNone(cycle.package_completed(charge, [dict(amount=420000, paid_date='2026-11-25')]))
        charge.update(prior_balance=1080000, charge_id=20, live_balance=0, charge_date='2026-11-24 00:00:00')
        receipts = [dict(ID=10, amount=1080000, paid_date='2026-11-24 00:00:00')]
        self.assertEqual(cycle.package_completed(charge, receipts)[:10], '2026-11-24')
        charge.update(prior_balance=420000, live_balance=-240000)
        receipts = [dict(ID=10, amount=420000, paid_date='2026-11-24 00:00:00'),
                    dict(ID=30, amount=420000, paid_date='2026-11-24 00:00:00')]
        self.assertIsNone(cycle.package_completed(charge, receipts))


if __name__ == '__main__':
    unittest.main()
