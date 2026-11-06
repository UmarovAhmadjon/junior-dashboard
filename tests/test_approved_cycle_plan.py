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
                  mock.patch.object(dashboard, 'archive_previous_dashboard'),
                  mock.patch.object(dashboard, 'ensure_legacy_cycle_archives'),
                  mock.patch.object(dashboard, 'cycle_archive_options', return_value=''),
                  mock.patch('builtins.print')):
                dashboard.render([], audit, [], live)
            page = target.read_text()
        embedded = lambda key: json.loads(re.search(
            '<script id="'+key+'" type="application/json">(.*?)</script>',page,re.S).group(1))
        self.assertEqual(embedded('collection-data'), live)
        self.assertEqual(len(embedded('data')), 1518)
        self.assertIn(audit['generated_iso'], page)

    def test_user_approved_totals_and_excluded_cases(self):
        plan = approved_cycle_plan.load('2026-10-25', '2026-11-23')
        rows = plan['records']
        self.assertEqual(len(rows), 1518)
        self.assertEqual(sum(r['approved_overdue'] for r in rows), 267)
        ashurov = next(r for r in rows if r['id'] == 13278)
        self.assertEqual(ashurov['due'], '2026-11-18')
        self.assertFalse(ashurov['approved_overdue'])
        self.assertEqual(ashurov['curator'], 'Marjona')
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
