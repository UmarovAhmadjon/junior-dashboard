import unittest
import sales_upsells as su

PAGE = '''<script id="salesdata" type="application/json">{"2026-11":{"Asilbek Fozilov":[
{"id":71788419,"name":"Asrorqulov Nurmuhammad","date":"2026-11-07","category":"upsell3"},
{"id":1,"name":"Oddiy Sotuv","date":"2026-11-07","category":"sale"}],
"Abdulaziz Numonjonov":[{"id":71802137,"name":"Rafiqov Sunnat","date":"2026-11-19","category":"upsell12"}]}}</script>'''


class SalesUpsellTests(unittest.TestCase):
    def test_parses_only_upsell_categories(self):
        deals = su.parse_page(PAGE)
        self.assertEqual({d['lead_id']: d['months'] for d in deals}, {71788419: 3, 71802137: 12})

    def test_amo_upsell_is_removed_from_curator_list(self):
        deals = su.parse_page(PAGE)
        rows = [dict(id=22136, name='Asrorqulov Nurmuhammad', curator='Halima', sale_date='27.11.2026'),
                dict(id=22695, name='Rafiqov Sunnat', curator='Dilafruz', sale_date='20.11.2026'),
                dict(id=12419, name='Akramov Behruz', curator='Marjona', sale_date='24.11.2026')]
        kept, excluded = su.exclude(rows, deals)
        self.assertEqual([r['id'] for r in kept], [12419])
        self.assertEqual({r['id']: r['sales_manager'] for r in excluded},
                         {22136: 'Asilbek Fozilov', 22695: 'Abdulaziz Numonjonov'})

    def test_old_sales_deal_or_other_student_does_not_match(self):
        deals = su.parse_page(PAGE)
        rows = [dict(id=5, name='Asrorqulov Nurmuhammad', sale_date='01.06.2027'),
                dict(id=6, name='Asrorqulov Ali', sale_date='27.11.2026')]
        kept, excluded = su.exclude(rows, deals)
        self.assertEqual(len(kept), 2)
        self.assertEqual(excluded, [])

    def test_apostrophes_and_x_h_spelling_match(self):
        self.assertEqual(su.name_key("Abdug'ofurjonov Muhammadyusuf"), su.name_key('Abdugofurjonov Muhammadyusuf'))
        self.assertEqual(su.name_key('Xakimov Nuryigit'), su.name_key('Hakimov Nuryigit'))


if __name__ == '__main__':
    unittest.main()
