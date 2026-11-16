import unittest
from upsell_installments import completion_date, audited_credit, report_sales
class InstallmentTests(unittest.TestCase):
    def test_november_only_keeps_split_completion_and_preserves_archive(self):
        rows=[dict(id=19687,sale_date='30.10.2026'),
              dict(id=20355,sale_date='04.11.2026'),
              dict(id=12193,sale_date='05.11.2026')]
        self.assertEqual([r['id'] for r in report_sales(rows,'2026-10-25','2026-11-23')], [20355,12193])
        self.assertEqual(report_sales(rows,'2026-09-24','2026-10-24'),rows)

    def test_behruz_missing_old_debit_does_not_backdate_november_receipt(self):
        receipts=[dict(paid_date='2026-11-05 18:37:59',amount=990000)]
        credit=audited_credit(12193,'2026-10-28',990000,0,1134000,receipts)
        self.assertEqual(completion_date(990000,credit,'2026-10-28',receipts),'2026-11-05 18:37:59')
        self.assertEqual(audited_credit(12193,'2026-10-28',990000,0,1134000,[]),1134000)
        self.assertEqual(audited_credit(999,'2026-10-28',990000,0,1134000,receipts),1134000)

    def test_muslima_split_across_cycles(self):
        rows=[dict(paid_date='2026-10-03',amount=420000),dict(paid_date='2026-10-31',amount=400000),dict(paid_date='2026-10-31',amount=260000)]
        self.assertEqual(completion_date(1080000,0,'2026-10-02',rows),'2026-10-31')
    def test_prepayment_and_late_final_installment(self):
        self.assertEqual(completion_date(990000,390000,'2026-10-21',[dict(paid_date='2026-10-30',amount=600000)]),'2026-10-30')
    def test_later_monthly_payment_does_not_move_sale(self):
        rows=[dict(paid_date='2026-08-27',amount=990000),dict(paid_date='2026-11-05',amount=390000)]
        self.assertEqual(completion_date(990000,0,'2026-08-05',rows),'2026-08-27')
    def test_partial_and_old_debt_not_closed(self):
        self.assertIsNone(completion_date(1080000,0,'2026-11-02',[dict(paid_date='2026-11-06',amount=600000)]))
        self.assertIsNone(completion_date(1080000,-40000,'2026-11-02',[dict(paid_date='2026-11-06',amount=1080000)]))
