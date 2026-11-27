import unittest
import datetime as dt
import json
import re
import tempfile
from pathlib import Path
from unittest import mock
import approved_cycle_plan
import mcp_dashboard_build as dashboard


class ApprovedCycleTests(unittest.TestCase):
    def test_roster_approval_preserves_live_student_kassa_and_timestamp(self):
        live = [{'date': '2026-11-06', 'amount': 123456}]
        audit = {'generated_iso':'2026-11-06T12:14:32+05:00',
                 'generated':'06.11.2026 12:14', 'today':'2026-11-06'}
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'index.html'
            with (mock.patch.object(dashboard, 'OUT', str(target)),
                  mock.patch.object(dashboard, 'CYCLE_START', dt.date(2026,10,25)),
                  mock.patch.object(dashboard, 'CYCLE_END', dt.date(2026,11,23)),
                  mock.patch.object(approved_cycle_plan, 'refresh_payments', return_value=approved_cycle_plan.load('2026-10-25', '2026-11-23')['records']),
                  mock.patch.object(approved_cycle_plan, 'refresh_statuses', side_effect=lambda plan, rows, query, today: rows),
                  mock.patch.object(dashboard, 'archive_previous_dashboard'),
                  mock.patch.object(dashboard, 'ensure_legacy_cycle_archives'),
                  mock.patch.object(dashboard, 'cycle_archive_options', return_value=''),
                  mock.patch('builtins.print')):
                dashboard.render([], audit, [], live)
            page = target.read_text()
        embedded = lambda key: json.loads(re.search(
            '<script id="'+key+'" type="application/json">(.*?)</script>',page,re.S).group(1))
        self.assertEqual(embedded('collection-data'), live)
        self.assertEqual(len(embedded('data')), 1605)
        self.assertIn(audit['generated_iso'], page)

    def test_user_approved_totals_and_excluded_cases(self):
        plan = approved_cycle_plan.load('2026-10-25', '2026-11-23')
        rows = plan['records']
        self.assertEqual(len(rows), 1605)
        self.assertEqual(sum(r['approved_overdue'] for r in rows if not r.get('november_one_month')), 267)
        self.assertEqual(sum(bool(r.get('november_one_month')) for r in rows), 88)
        self.assertEqual(sum(bool(r.get('plan_archived_unpaid')) for r in rows), 9)
        self.assertFalse({15458, 20816, 20826, 20877, 9660, 20787, 13970, 17308, 15084, 10315} & {r['id'] for r in rows})
        ashurov = next(r for r in rows if r['id'] == 13278)
        self.assertEqual(ashurov['due'], '2026-11-18')
        self.assertFalse(ashurov['approved_overdue'])
        self.assertEqual(ashurov['curator'], 'Marjona')
        self.assertTrue(all(r['subscription_status'] in ('active', 'freezed') or r.get('plan_archived_unpaid') for r in rows))
        self.assertEqual(ashurov['required'], 420000)
        self.assertFalse({15982, 14857, 19167} & {r['id'] for r in rows})
        self.assertTrue(any(r['subscription_status'] == 'freezed' and r['approved_overdue'] for r in rows))

    def test_snapshot_does_not_override_other_cycles(self):
        self.assertIsNone(approved_cycle_plan.load('2026-09-24', '2026-10-24'))
        self.assertIsNone(approved_cycle_plan.load('2026-11-25', '2026-12-23'))

    def test_approved_data_does_not_add_layout_or_styles(self):
        plan = approved_cycle_plan.load('2026-10-25', '2026-11-23')
        original = '<style>.panel{color:red}</style></head><div class="page" id="curatorPage"><div class="hero">existing layout</div></div>'
        self.assertEqual(approved_cycle_plan.apply_document(original, plan), original)

    def test_existing_debt_card_opens_overdue_subset(self):
        plan = approved_cycle_plan.load('2026-10-25', '2026-11-23')
        original = '<button data-list="debt" id="debtCard">267</button>'
        self.assertEqual(approved_cycle_plan.apply_document(original, plan),
                         '<button data-list="overdue" id="debtCard">267</button>')


class LiveApprovedPaymentsTests(unittest.TestCase):
    def plan(self):
        return {'cycle_start':'2026-10-25', 'cycle_end':'2026-11-23', 'records':[
            {'id':17040,'curator':'Shaxlo','due':'2026-11-04','required':380000,
             'paid':False,'approved_status':'debt','approved_overdue':True},
            {'id':18255,'curator':'Shaxlo','due':'2026-11-05','required':420000,
             'paid':False,'approved_status':'debt','approved_overdue':True}]}

    def test_real_paid_cases_refresh_without_changing_plan(self):
        plan=self.plan()
        def query(sql):
            self.assertIn('t.TRANSACTION_DATE<=NOW()',sql)
            return [dict(sid=r['id'],balance=0,amount=r['required'],payments=1,
                         last_paid='2026-11-06',charged=r['required']) for r in plan['records']]
        rows=approved_cycle_plan.refresh_payments(plan,query,'2026-11-09')
        self.assertEqual([r['id'] for r in rows],[17040,18255])
        self.assertTrue(all(r['paid'] and not r['approved_overdue'] for r in rows))
        self.assertTrue(all(r['approved_status']=='paid' for r in rows))
        self.assertTrue(plan['records'][0]['approved_overdue'])

    def test_partial_payment_and_future_zero_balance(self):
        plan=self.plan();plan['records'][1]['due']='2026-11-18'
        rows=approved_cycle_plan.refresh_payments(plan,lambda sql:[
            dict(sid=17040,balance=-180000,amount=200000,payments=1,last_paid='2026-11-06',charged=380000),
            dict(sid=18255,balance=0,amount=0,payments=0,last_paid=None,charged=0)],'2026-11-09')
        self.assertEqual(rows[0]['debt_amount'],180000)
        self.assertTrue(rows[0]['approved_overdue'])
        self.assertFalse(rows[1]['paid']);self.assertFalse(rows[1]['approved_overdue'])

    def test_closed_cycle_ignores_next_charge_and_counts_late_receipt(self):
        plan=self.plan()
        rows=approved_cycle_plan.refresh_payments(plan,lambda sql:[
            # Paid in cycle, charged again for the next cycle on 26.11.
            dict(sid=17040,balance=-380000,amount=380000,payments=1,last_paid='2026-11-04',
                 charged=380000,later_charged=380000,later_added=0,later_last_paid=None),
            # Owed at close, paid on 25.11 (and no new charge yet).
            dict(sid=18255,balance=0,amount=0,payments=0,last_paid=None,
                 charged=420000,later_charged=0,later_added=420000,
                 later_last_paid='2026-11-25 00:00:00')],'2026-11-23')
        self.assertEqual([r['approved_status'] for r in rows],['paid','paid'])
        self.assertEqual(rows[1]['amount'],420000)
        self.assertEqual(rows[1]['last_paid'],'2026-11-25 00:00:00')

    def test_incomplete_refresh_fails_closed(self):
        with self.assertRaises(RuntimeError):
            approved_cycle_plan.refresh_payments(self.plan(),lambda sql:[],'2026-11-09')

    def campaign(self, balance, amount, *, archive=False, due='2026-11-04', charged=380000, start='2026-10-25'):
        plan = self.plan()
        plan['cycle_start'] = start
        plan['records'] = [dict(plan['records'][0], november_one_month=True,
                                plan_archived_unpaid=archive, due=due)]
        return approved_cycle_plan.refresh_payments(plan, lambda sql: [dict(
            sid=17040, balance=balance, amount=amount, payments=int(amount > 0),
            last_paid='2026-11-06' if amount else None, charged=charged)], '2026-11-12')[0]

    def test_one_month_payment_completes_campaign_without_erasing_old_crm_debt(self):
        row = self.campaign(-1520000, 380000)
        self.assertTrue(row['paid'])
        self.assertEqual(row['balance'], -1520000)
        self.assertEqual(row['debt_amount'], 0)
        self.assertFalse(row['approved_overdue'])

    def test_old_debt_capped_at_one_month_and_partial_payment_deducted_once(self):
        self.assertEqual(self.campaign(-1900000, 0)['debt_amount'], 380000)
        row = self.campaign(-1700000, 200000)
        self.assertEqual(row['debt_amount'], 180000)
        self.assertTrue(row['partial'])
        self.assertEqual(self.campaign(-40000, 350000)['debt_amount'], 30000)

    def test_archived_remains_in_plan_but_not_paid_or_collectible(self):
        row = self.campaign(-1900000, 0, archive=True)
        self.assertEqual(row['required'], 380000)
        self.assertEqual(row['approved_status'], 'archive')
        self.assertFalse(row['paid'])
        self.assertFalse(row['approved_overdue'])
        self.assertEqual(row['debt_amount'], 0)

    def test_future_zero_and_missing_charge_are_not_paid(self):
        self.assertFalse(self.campaign(0, 0, due='2026-11-18')['paid'])
        self.assertFalse(self.campaign(0, 0, charged=0)['paid'])
        self.assertTrue(self.campaign(0, 0)['paid'])

    def test_one_off_rule_never_applies_to_other_cycles(self):
        row = self.campaign(-1520000, 380000, start='2026-11-25')
        self.assertFalse(row['paid'])
        self.assertEqual(row['debt_amount'], 1520000)


class CycleTransferTests(unittest.TestCase):
    def test_jasur_removed_only_from_source_cycle(self):
        rows = [{'id': 14034}, {'id': 20647}, {'id': 999}]
        query = mock.Mock(side_effect=AssertionError('Source needs no CRM write/read'))
        self.assertEqual(approved_cycle_plan.apply_cycle_transfers(
            rows, '2026-10-25', '2026-11-23', query, '2026-11-16'), [{'id': 999}])
        self.assertEqual(approved_cycle_plan.apply_cycle_transfers(
            rows, '2026-09-24', '2026-10-24', query, '2026-10-24'), rows)
        self.assertNotIn(14034, {r['id'] for r in approved_cycle_plan.load(
            '2026-10-25', '2026-11-23')['records']})
        self.assertNotIn(20647, {r['id'] for r in approved_cycle_plan.load(
            '2026-10-25', '2026-11-23')['records']})

    def target(self, amount=0, balance=0, charged=0, today='2026-11-24'):
        def query(sql):
            if 's.ID IN (20647)' in sql:
                if 'CURRENT_BALANCE' in sql:
                    return [dict(sid=20647, balance=0, amount=0, payments=0,
                                 charged=0, last_paid=None)]
                return [dict(sid=20647, subscription_id=77791,
                             subscription_status='active', tariff='420 000',
                             subscription_start='2026-11-28', subscription_end='2026-12-28')]
            if 'CURRENT_BALANCE' in sql:
                self.assertIn("t.TRANSACTION_DATE>='2026-11-24'", sql)
                return [dict(sid=14034, balance=balance, amount=amount,
                             payments=int(amount > 0), charged=charged,
                             last_paid='2026-11-28' if amount else None)]
            return [dict(sid=14034, subscription_id=78030,
                         subscription_status='active', tariff='420',
                         subscription_start='2026-08-28', subscription_end='2026-11-28')]
        return approved_cycle_plan.apply_cycle_transfers(
            [{'id': 14034, 'paid': True, 'november_one_month': True},
             {'id': 20647, 'paid': True, 'november_one_month': True}, {'id': 999}],
            '2026-11-24', '2026-12-23', query, today)

    def test_target_includes_once_without_old_paid_or_campaign_flags(self):
        rows = self.target()
        self.assertEqual(len(rows), 3)
        jasur = next(r for r in rows if r['id'] == 14034)
        self.assertEqual(jasur['due'], '2026-11-28')
        self.assertEqual(jasur['curator'], 'Marjona')
        self.assertFalse(jasur['paid'])
        self.assertFalse(jasur['approved_overdue'])
        self.assertNotIn('november_one_month', jasur)
        self.assertEqual(jasur['required'], 1080000)
        ruhshona = next(r for r in rows if r['id'] == 20647)
        self.assertEqual(ruhshona['due'], '2026-11-28')
        self.assertEqual(ruhshona['curator'], 'Jasmina')
        self.assertEqual(ruhshona['required'], 420000)
        self.assertFalse(ruhshona['paid'])
        self.assertFalse(ruhshona['approved_overdue'])
        self.assertNotIn('november_one_month', ruhshona)

    def test_target_refresh_preserves_partial_and_full_payment(self):
        partial = next(r for r in self.target(500000, -580000, 1080000, '2026-11-28') if r['id']==14034)
        self.assertFalse(partial['paid'])
        self.assertEqual(partial['debt_amount'], 580000)
        paid = next(r for r in self.target(1080000, 0, 1080000, '2026-11-28') if r['id']==14034)
        self.assertTrue(paid['paid'])
        self.assertEqual(paid['debt_amount'], 0)
