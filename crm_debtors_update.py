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
    "Fotimabonu Abdulkhakova": ("A", "Fotima", "13799"),
    "Dilafruz Shokirova": ("A", "Dilafruz", "14241"),
    "Shaxlo Ziyodova": ("A", "Shaxlo", "21463"),
    "Marjona Pardayeva": ("B", "Marjona", "14451"),
    "Xalima Ismoiljonova": ("B", "Halima", "16386"),
    "Jasmina Tolibova": ("B", "Jasmina", "14974"),
    "Madina Normatova": ("B", "Madina", "16005"),
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

def fetch(op, start=START, end=END, status="", curator=""):
    body = urllib.parse.urlencode({
        "filter_status": status, "filter_tariff": "", "filter_group": "",
        "filter_team": "", "filter_curator": curator,
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

def validate(label, c, rows):
    counts = {k:0 for k in ("paid","debt","frozen","deleted")}
    for row in rows: counts[status_key(row["status"])] += 1
    if len(rows) != c["total"] or sum(counts.values()) != c["total"]:
        raise RuntimeError(f"{label}: CRM card/table mismatch")
    return counts

def dashboard_row(team, short, full, c, source_rows, hidden=False):
    counts = validate(short, c, source_rows)
    paid = counts["paid"]
    today = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=5))).date()
    return dict(team=team, short=short, full=full, paid=paid, tol=paid, bit=0, sar=0,
        muz=counts["frozen"], arx=counts["deleted"], plan=c["total"], plansum=c["plan"],
        debt=max(0,c["total"]-paid), sob=c["fact"],
        pct=round(paid/c["total"]*100) if c["total"] else 0,
        due=sum(1 for x in source_rows if x["due"]<=today), hidden=hidden)

def detail(source_rows):
    out=[]
    for i,x in enumerate(source_rows):
        team_short=CURATORS.get(x["admin"])
        curator=team_short[1] if team_short else "Biriktirilmagan"
        paid=status_key(x["status"])=="paid"
        out.append(dict(id=i+1,name=x["name"],curator=curator,
            status="To'lagan" if paid else x["status"],
            paid=x["paid"] if paid else 0,debt=x["debt"],period=x["due"].strftime("%d.%m.%Y")))
    return out

def week_stats(month_rows):
    result=[]
    for _key,a,b,_label in PERIODS[1:]:
        rr=[x for x in month_rows if a<=x["due"]<=b]
        paid=[x for x in rr if status_key(x["status"])=="paid"]
        def team_of(x): return CURATORS.get(x["admin"],("U",))[0]
        result.append(dict(ws=a,we=b,total=len(rr),paid=len(paid),
            qarz=sum(status_key(x["status"])=="debt" for x in rr),
            muz=sum(status_key(x["status"])=="frozen" for x in rr),
            arx=sum(status_key(x["status"])=="deleted" for x in rr),
            sob=sum(x["paid"] for x in paid),
            A_total=sum(team_of(x)=="A" for x in rr),A_paid=sum(team_of(x)=="A" for x in paid),A_sob=sum(x["paid"] for x in paid if team_of(x)=="A"),
            B_total=sum(team_of(x)=="B" for x in rr),B_paid=sum(team_of(x)=="B" for x in paid),B_sob=sum(x["paid"] for x in paid if team_of(x)=="B")))
    return result

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
        "parsed_rows":len(rows), "row_plan":sum(r["plan"] for r in rows),
        "row_paid":sum(r["paid"] for r in rows),
    }
    if "check" in os.sys.argv:
        print("CRM_CHECK", " ".join(f"{k}={v}" for k,v in summary.items()))
        if summary["total"] != summary["paid"]+summary["debt"]+summary["frozen"]+summary["deleted"]:
            raise RuntimeError("CRM status totals do not match")
        if rows and len(rows) != summary["total"]:
            raise RuntimeError("CRM table row count does not match cards")
        return

    # Exact month: one full-module request plus one request per ranked curator.
    # Week pages are slices of this same validated 1331-row CRM response, avoiding 40 slow requests.
    all_raw=fetch(op,START,END); month_card=card(all_raw); month_source=table_rows(all_raw)
    validate("month",month_card,month_source)
    month_rows=[]; known_total=known_plan=known_fact=0; known_counts={k:0 for k in ("paid","debt","frozen","deleted")}
    for full,(team,short,cid) in CURATORS.items():
        cr=fetch(op,START,END,curator=cid); cc=card(cr); tr=table_rows(cr)
        month_rows.append(dashboard_row(team,short,full,cc,tr))
        known_total+=cc["total"]; known_plan+=cc["plan"]; known_fact+=cc["fact"]
        for x in tr: known_counts[status_key(x["status"])]+=1
    all_counts=validate("month",month_card,month_source)
    other_total=month_card["total"]-known_total
    if other_total:
        opaid=all_counts["paid"]-known_counts["paid"]
        month_rows.append(dict(team="U",short="Biriktirilmagan",full="Boshqa adminlar",paid=opaid,tol=opaid,bit=0,sar=0,
            muz=all_counts["frozen"]-known_counts["frozen"],arx=all_counts["deleted"]-known_counts["deleted"],
            plan=other_total,plansum=month_card["plan"]-known_plan,debt=other_total-opaid,
            sob=month_card["fact"]-known_fact,pct=round(opaid/other_total*100) if other_total else 0,due=0,hidden=True))
    datasets=[("month",START,END,PERIODS[0][3],month_card,month_source,month_rows)]

    # Weekly count-first views from the exact month list. Amounts come from the same CRM row columns.
    for key,a,b,label in PERIODS[1:]:
        source=[x for x in month_source if a<=x["due"]<=b]
        rows=[]
        for full,(team,short,_cid) in CURATORS.items():
            rr=[x for x in source if x["admin"]==full]
            c=dict(total=len(rr),plan=sum(x["plan"] for x in rr),
                   fact=sum(x["paid"] for x in rr if status_key(x["status"])=="paid"))
            rows.append(dashboard_row(team,short,full,c,rr))
        rr=[x for x in source if x["admin"] not in CURATORS]
        if rr:
            c=dict(total=len(rr),plan=sum(x["plan"] for x in rr),fact=sum(x["paid"] for x in rr if status_key(x["status"])=="paid"))
            rows.append(dashboard_row("U","Biriktirilmagan","Boshqa adminlar",c,rr,True))
        c=dict(total=len(source),plan=sum(x["plan"] for x in source),fact=sum(x["paid"] for x in source if status_key(x["status"])=="paid"))
        datasets.append((key,a,b,label,c,source,rows))

    month_rows=datasets[0][5]; weeks=week_stats(month_rows)
    now=datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=5))).replace(tzinfo=None)
    ui.IS_CI=os.environ.get("GITHUB_ACTIONS")=="true"
    ui.PREVIEW=False
    ui.CUR=[(team,short,full) for full,(team,short,_cid) in CURATORS.items()]
    for key,a,b,label,c,source_rows,rows in datasets:
        ui.PERIOD=key; ui.DETAIL_DATA=detail(source_rows)
        due_total=sum(r["due"] for r in rows); due_paid=sum(min(r["paid"],r["due"]) for r in rows)
        ui.render(now,a,b,rows,c["total"],c["plan"],weeks,due_total,due_paid,PERIODS,label)

if __name__ == "__main__":
    main()
