import unittest
from upsell_installments import completion_date
class InstallmentTests(unittest.TestCase):
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
