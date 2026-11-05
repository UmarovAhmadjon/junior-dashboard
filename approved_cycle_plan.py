"""User-approved cycle snapshot; independent of automatic CRM refreshes."""
import html
import json
import re
from pathlib import Path


def load(start, end):
    path = Path(__file__).parent / 'approved-plans' / f'{start}_{end}.json'
    if not path.exists():
        return None
    plan = json.loads(path.read_text(encoding='utf-8'))
    rows = plan['records']
    assert len(rows) == len({r['id'] for r in rows})
    assert plan['cycle_start'] == str(start) and plan['cycle_end'] == str(end)
    for name, (total, debt) in plan['expected'].items():
        selected = [r for r in rows if r['curator'] == name]
        assert len(selected) == total
        assert sum(r['approved_overdue'] for r in selected) == debt
    assert len(rows) == sum(n[0] for n in plan['expected'].values())
    assert all(str(start) <= r['due'] <= str(end) for r in rows)
    return plan


def apply_document(document, plan):
    """Use approved payment classification, including overdue automatic freezes."""
    document = document.replace(
        'const isOverdue=x=>Number(x.balance||0)<0;',
        'const isOverdue=x=>x.approved_overdue===true;')
    document = document.replace(
        'const businessStatus=x=>{',
        "const businessStatus=x=>{if(x.approved_status)return x.approved_status;")
    document = document.replace(
        'const displayStatus=x=>{',
        'const displayStatus=x=>{if(x.approved_label)return x.approved_label;')
    document = re.sub(r'<button[^>]*id="debtCard"[^>]*>',
                      lambda m: m[0].replace('data-list="debt"', 'data-list="overdue"'), document)
    titles = {'Fotima':'Фотима','Dilafruz':'Дилафруз','Jasmina':'Жасмина',
              'Madina':'Мадина','Shaxlo':'Шахло','Marjona':'Маржона','Halima':'Ҳалима'}
    cards = []
    for name, (total, debt) in plan['expected'].items():
        attr = f'data-curator="{html.escape(name)}" data-week="all"'
        cards.append(
            '<article class="approved-card">'
            f'<button class="person" data-list="all" {attr}><b>{titles[name]}</b></button>'
            '<div><span>Циклдаги тўловчилар</span>'
            f'<button class="click" data-list="all" {attr}>{total}</button></div>'
            '<div><span>Шундан қарз қолган</span>'
            f'<button class="click" data-list="overdue" {attr}>{debt}</button></div></article>')
    total = sum(n[0] for n in plan['expected'].values())
    debt = sum(n[1] for n in plan['expected'].values())
    panel = ('<section class="panel approved-plan" id="approvedPlan">'
             '<h2>25 октябрь–23 ноябрь · тасдиқланган план</h2>'
             '<p>Ҳисоб ҳолати: 05.11.2026, 20:21 (Тошкент). '
             'Сонга босинг — ўқувчилар рўйхати очилади.</p>'
             '<div class="approved-grid">'+''.join(cards)+'</div>'
             '<div class="approved-total"><b>Жами</b><span>Циклдаги тўловчилар: '
             f'<button class="click" data-list="all" data-week="all">{total}</button>'
             '</span><span>Қарз қолган: '
             f'<button class="click" data-list="overdue" data-week="all">{debt}</button>'
             '</span></div></section>')
    marker = '<div class="page" id="curatorPage">'
    assert marker in document
    document = document.replace(marker, marker + panel, 1)
    css = ('<style>.approved-plan{margin-bottom:18px}.approved-plan p{font-size:13px;color:#657080}'
           '.approved-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(245px,1fr));gap:12px}'
           '.approved-card{background:#f4f2ec;padding:16px;border-left:3px solid #ff4b28}'
           '.approved-card>.person{width:100%;padding:0 0 12px}.approved-card>div{display:flex;'
           'justify-content:space-between;align-items:center;gap:12px;font-size:13px;margin:8px 0}'
           '.approved-card .click{font-size:26px}.approved-total{display:flex;gap:24px;'
           'align-items:center;flex-wrap:wrap;margin-top:18px}.approved-total .click{font-size:28px}</style>')
    return document.replace('</head>', css + '</head>', 1)
