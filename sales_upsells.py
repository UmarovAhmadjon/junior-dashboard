"""Upsells already credited to the sales department in amoCRM.

Rule (user, 28.11.2026): when an amoCRM deal is marked «Допродажа 3/6/12»
(field «Оплачена»), the package belongs to the sales manager and must not
appear in the curators' Upsell list.

Source: the sales plan pages, which publish every deal with the category the
sales system derives from that amoCRM field (``upsell3``/``upsell6``/``upsell12``).
Fetched deals are merged into a cache so older months stay known after the
pages roll forward, and a fetch failure falls back to the cache.
"""
import datetime as dt
import json
import re
import urllib.request
from pathlib import Path

SOURCES = (
    "https://dcamp-reports.surge.sh/plan.html",
    "https://dcamp-reports.surge.sh/plan-team-2.html",
)
CACHE = Path(__file__).parent / "approved-plans" / "sales-upsells.json"
CATEGORY = re.compile(r"^upsell(3|6|12)$")
# The sales deal is closed before or shortly after the package is fully paid.
DAYS_BEFORE = 120
DAYS_AFTER = 14


def name_key(name):
    text = str(name or "").lower().replace("ё", "е")
    text = re.sub(r"[ʻʼ'`‘’]", "", text).replace("x", "h")
    words = [w for w in re.split(r"[^a-zа-я0-9]+", text) if w]
    return frozenset(w for w in words if w not in {"ogli", "qizi", "ugli", "o"})


def parse_page(html):
    match = re.search(r'<script[^>]*id="salesdata"[^>]*>(.*?)</script>', html, re.S)
    if not match:
        raise ValueError("salesdata block not found")
    deals = []
    for month in json.loads(match.group(1)).values():
        for manager, items in month.items():
            for deal in items:
                found = CATEGORY.match(str(deal.get("category") or ""))
                if found and deal.get("id") and deal.get("date"):
                    deals.append({"lead_id": int(deal["id"]), "name": deal.get("name") or "",
                                  "date": str(deal["date"])[:10], "manager": manager,
                                  "months": int(found.group(1))})
    return deals


def load(fetch=None):
    fetch = fetch or (lambda url: urllib.request.urlopen(url, timeout=60).read().decode("utf-8"))
    cached = {}
    if CACHE.exists():
        cached = {int(d["lead_id"]): d for d in json.loads(CACHE.read_text(encoding="utf-8"))}
    fresh = {}
    try:
        for url in SOURCES:
            fresh.update({d["lead_id"]: d for d in parse_page(fetch(url))})
    except Exception as error:  # keep publishing with the last known list
        print(f"sales upsells: using cache ({error})")
        return list(cached.values())
    # A deal that disappears from the live pages is no longer an upsell there.
    live_months = {d["date"][:7] for d in fresh.values()}
    merged = {k: v for k, v in cached.items() if v["date"][:7] not in live_months}
    merged.update(fresh)
    rows = sorted(merged.values(), key=lambda d: (d["date"], d["lead_id"]))
    CACHE.parent.mkdir(exist_ok=True)
    CACHE.write_text(json.dumps(rows, ensure_ascii=False, indent=0) + "\n", encoding="utf-8")
    return rows


def _date(value):
    value = str(value or "")
    if re.match(r"^\d{2}\.\d{2}\.\d{4}$", value):
        return dt.date(int(value[6:]), int(value[3:5]), int(value[:2]))
    return dt.date.fromisoformat(value[:10])


def matching_deal(upsell, deals):
    key = name_key(upsell.get("name"))
    if len(key) < 2:
        return None
    sold = _date(upsell.get("sale_date"))
    for deal in deals:
        other = name_key(deal["name"])
        if len(other) < 2 or not (key <= other or other <= key):
            continue
        if -DAYS_AFTER <= (sold - _date(deal["date"])).days <= DAYS_BEFORE:
            return deal
    return None


def exclude(upsells, deals=None):
    """Return (curator upsells, excluded rows with the sales deal attached)."""
    if not upsells:
        return upsells, []
    deals = load() if deals is None else deals
    kept, excluded = [], []
    for row in upsells:
        deal = matching_deal(row, deals)
        if deal:
            excluded.append(dict(row, sales_lead_id=deal["lead_id"],
                                 sales_manager=deal["manager"], sales_date=deal["date"]))
        else:
            kept.append(row)
    return kept, excluded
