import unittest

from live_update import cumulative_kpi_due


class CumulativeKpiDueTests(unittest.TestCase):
    def test_uses_debtors_accumulated_by_date_instead_of_calendar_fraction(self):
        # 1 Oct: 5, 2 Oct: 15, 3 Oct: 10 => 30 scheduled by 3 Oct.
        self.assertEqual(cumulative_kpi_due(5 + 15 + 10, 80), 24)

    def test_supports_stretch_target(self):
        self.assertEqual(cumulative_kpi_due(100, 85), 85)


if __name__ == "__main__":
    unittest.main()
