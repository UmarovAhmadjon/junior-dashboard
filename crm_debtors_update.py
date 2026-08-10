#!/usr/bin/env python3
"""Dashboard source: CRM Qarzdorlar module, using the module's own POST filters."""
import os, re, html, datetime, urllib.request, urllib.parse, http.cookiejar
import live_update as ui

CRM = "https://crm.junior-it.uz"
START = datetime.date(2026, 7, 25)
END = datetime.date(2026, 8, 24)
PERIODS = [
    ("month", START, END, "25.07–24.08 · весь цикл"),
    ("w1", datetime.date(2026,7,25), datetime.date(2026,7,31), "25.07–31.07 · неделя 1"),
    ("w2", datetime.date(2026,8,1), datetime.date(2026,8,7), "01.08–07.08 · неделя 2"),
    ("w3", datetime.date(2026,8,8), datetime.date(2026,8,14), "08.08–14.08 · неделя 3"),
    ("w4", datetime.date(2026,8,15), datetime.date(2026,8,24), "15.08–24.08 · неделя 4"),
]
CURATORS = {
    "Fotimabonu Abdulkhakova": ("A", "Fotima"),
    "Dilafruz Shokirova": ("A", "Dilafruz"),
    "Shaxlo Ziyodova": ("A", "Shaxlo"),
    "Marjona Pardayeva": ("B", "Marjona"),
    "Xalima Ismoiljonova": ("B", "Halima"),
    "Jasmina Tolibova": ("B", "Jasmina"),
    "Madina Normatova": ("B", "Madina"),
}

def strip_tags(value):
    value = re.sub(r"<script.*?</script>|<style.*?</style>", " ", value, flags=re.I|re.S)
    value = re.sub(r"<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", html.unescape(value)).strip()

def number(value):
    m = re.search(r"-?[\d\s]+", str(value or ""))
    return int(re.sub(r"\D", "", m.group(0))) if m else 0

def crm_session():
    cj = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    op.addheaders = [("User-Agent", "Mozilla/5.0"), ("X-Requested-With", "XMLHttpRequest")]
    op.open(CRM + "/account/", timeout=45).read()
    body = urllib.parse.urlencode({"phone": os.environ["CRM_PHONE"], "pass": os.environ["CRM_PASS"]}).encode()
    op.open(CRM + "/account/", body, timeout=45).read()
    return op

def fetch(op, start=START, end=END, status=""):
    body = urllib.parse.urlencode({
        "filter_status": status, "filter_tariff": "", "filter_group": "",
        "filter_team": "", "filter_curator": "",
        "filter_date_start": start.isoformat(), "filter_date_end": end.isoformat(),
        "filterModalSubmit": "1",
    }).encode()
    raw = op.open(CRM + "/account/debtors/list", body, timeout=90).read().decode(errors="ignore")
    if 'id="customtable"' not in raw and "id='customtable'" not in raw:
        raise RuntimeError("CRM Qarzdorlar table is unavailable")
    return raw

def card(raw):
    cards = re.findall(r'<div[^>]*class=["\'][^"\']*\bcard\b[^"\']*["\'][^>]*>(.*?)</div>\s*</div>', raw, re.I|re.S)
    text = strip_tags(cards[1]) if len(cards) > 1 else strip_tags(raw)
    patterns = {
        "total": r"Umumiy qarzdorlar\s*([\d ]+)",
        "plan": r"Plan\s*([\d ]+)\s*Fakt",
        "fact": r"Fakt\s*([\d ]+)\s*Muzlatilgan",
        "frozen": r"Muzlatilgan\s*([\d ]+)\s*/",
        "deleted": r"O.chirilgan\s*([\d ]+)\s*/",
    }
    out = {}
    for key, pattern in patterns.items():
        m = re.search(pattern, text, re.I)
        out[key] = number(m.group(1)) if m else 0
    return out

def table_rows(raw):
    m = re.search(r'<table[^>]*id=["\']customtable["\'][^>]*>(.*?)</table>', raw, re.I|re.S)
    if not m: return []
    out = []
    for tr in re.findall(r'<tr[^>]*>(.*?)</tr>', m.group(1), re.I|re.S):
        cells = [strip_tags(x) for x in re.findall(r'<t[dh][^>]*>(.*?)</t[dh]>', tr, re.I|re.S)]
        if len(cells) < 12 or not cells[1].isdigit(): continue
        try: due = datetime.datetime.strptime(cells[2], "%d.%m.%Y").date()
        except ValueError: due = START
        out.append({"due":due,"name":cells[3],"student_status":cells[5],"admin":cells[6],
                    "tariff":cells[7],"plan":number(cells[8]),"status":cells[9],
                    "debt":number(cells[10]),"paid":number(cells[11])})
    return out

def status_key(value):
    s = value.lower().replace("‘", "'").replace("’", "'")
    if "to'landi" in s or "tolandi" in s: return "paid"
    if "muz" in s: return "frozen"
    if "arxiv" in s or "o'ch" in s: return "deleted"
    return "debt"

def main():
    op = crm_session()
    raw = fetch(op)
    all_card = card(raw)
    rows = table_rows(raw)
    status_cards = {s:card(fetch(op,status=s)) for s in ("paid","qarzdor","frozen","deleted")}
    summary = {
        "total":all_card["total"], "plan":all_card["plan"], "fact":all_card["fact"],
        "paid":status_cards["paid"]["total"], "debt":status_cards["qarzdor"]["total"],
        "frozen":status_cards["frozen"]["total"], "deleted":status_cards["deleted"]["total"],
        "parsed_rows":len(rows),
    }
    if "check" in os.sys.argv:
        print("CRM_CHECK", " ".join(f"{k}={v}" for k,v in summary.items()))
        if summary["total"] != summary["paid"]+summary["debt"]+summary["frozen"]+summary["deleted"]:
            raise RuntimeError("CRM status totals do not match")
        if rows and len(rows) != summary["total"]:
            raise RuntimeError("CRM table row count does not match cards")
        return
    raise RuntimeError("Publish mode is enabled only after CRM_CHECK validation")

if __name__ == "__main__":
    main()
