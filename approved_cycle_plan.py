"""User-approved cycle snapshot; independent of automatic CRM refreshes."""
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
    document = re.sub(r'<button[^>]*id="debtCard"[^>]*>',
                      lambda m: m[0].replace('data-list="debt"', 'data-list="overdue"'), document)
    return document
