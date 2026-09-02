#!/usr/bin/env python3
"""Dashboard source: CRM Qarzdorlar module, using the module's own POST filters."""
import os, re, html, json, time, datetime, urllib.request, urllib.parse, http.cookiejar, shutil, glob
import live_update as ui

CRM = "https://crm.junior-it.uz"
def current_cycle(today=None):
    today=today or datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=5))).date()
    if today.day >= 25:
        start=today.replace(day=25)
        next_month=(start.replace(day=28)+datetime.timedelta(days=4)).replace(day=1)
        end=next_month.replace(day=24)
    else:
        end=today.replace(day=24)
        previous=end.replace(day=1)-datetime.timedelta(days=1)
        start=previous.replace(day=25)
    return start,end

def cycle_periods(start,end):
    first_next=(start.replace(day=28)+datetime.timedelta(days=4)).replace(day=1)
    cuts=[(start,min(first_next-datetime.timedelta(days=1),end))]
    cursor=cuts[-1][1]+datetime.timedelta(days=1)
    while cursor<=end and len(cuts)<4:
        finish=end if len(cuts)==3 else min(cursor+datetime.timedelta(days=6),end)
        cuts.append((cursor,finish)); cursor=finish+datetime.timedelta(days=1)
    return [("month",start,end,f"{start.strftime('%d.%m')}–{end.strftime('%d.%m')} · текущий цикл")]+[
        (f"w{i}",a,b,f"{a.strftime('%d.%m')}–{b.strftime('%d.%m')} · неделя {i}")
        for i,(a,b) in enumerate(cuts,1)
    ]

START,END=current_cycle()
GATEWAY = os.environ.get("JUNIOR_MCP_GATEWAY", "https://myclinic.agc.uz/new_junior_mcp.php")
PERIODS = cycle_periods(START,END)
CURATORS = {
    "Fotimabonu Abdulkhakova": ("A", "Fotima", "13799"),
    "Dilafruz Shokirova": ("A", "Dilafruz", "14241"),
    "Shaxlo Ziyodova": ("A", "Shaxlo", "21463"),
    "Marjona Pardayeva": ("B", "Marjona", "14451"),
    "Xalima Ismoiljonova": ("B", "Halima", "16386"),
    "Jasmina Tolibova": ("B", "Jasmina", "14974"),
    "Madina Normatova": ("B", "Madina", "16005"),
}
TEST_ADMIN_IDS = {21453}  # MK admin — test account, never show in cashier ranking
CURRENT_ADMIN_IDS = {int(value[2]) for value in CURATORS.values()}
CURATOR_BY_ID = {int(cid):(full,team,short) for full,(team,short,cid) in CURATORS.items()}

def archive_end(start):
    next_month=(start.replace(day=28)+datetime.timedelta(days=4)).replace(day=1)
    return next_month.replace(day=24)

def archive_label(start):
    end=archive_end(start)
    return f"{start.strftime('%d.%m.%Y')}–{end.strftime('%d.%m.%Y')} · прошлый цикл"

def archived_cycles():
    result=[]
    for path in glob.glob(os.path.join(os.path.dirname(__file__), "index-cycle-????-??-??.html")):
        match=re.search(r"index-cycle-(\d{4}-\d{2}-\d{2})\.html$",path)
        if match:
            start=datetime.date.fromisoformat(match.group(1))
            result.append((match.group(1),archive_label(start)))
    return sorted(result,reverse=True)

def existing_cycle(path):
    if not os.path.isfile(path): return None
    raw=open(path,encoding="utf-8").read(120000)
    match=re.search(r"ЦИКЛ\s+(\d{2})\.(\d{2})\s*[-–—]\s*(\d{2})\.(\d{2})",raw,re.I)
    if not match: return None
    day,month=int(match.group(1)),int(match.group(2))
    candidates=[datetime.date(START.year+delta,month,day) for delta in (-1,0,1)]
    return min(candidates,key=lambda value:abs((value-START).days))

def archive_previous_cycle():
    base=os.path.dirname(__file__)
    old_start=existing_cycle(os.path.join(base,"index.html"))
    if not old_start or old_start==START: return
    if old_start>START:
        raise RuntimeError(f"Existing dashboard cycle {old_start} is newer than requested {START}")
    key=old_start.isoformat()
    for kind in ("index","weeks","cashiers"):
        source=os.path.join(base,f"{kind}.html")
        target=os.path.join(base,f"{kind}-cycle-{key}.html")
        if os.path.isfile(source) and not os.path.exists(target):
            shutil.copyfile(source,target)
    print(f"ARCHIVED cycle {old_start}–{archive_end(old_start)}")

def refresh_archive_navigation():
    base=os.path.dirname(__file__)
    archives=archived_cycles()
    current_label=f"{START.strftime('%d.%m.%Y')}–{END.strftime('%d.%m.%Y')} · текущий цикл"
    for key,_label in archives:
        for kind,title in (("index","Кураторы"),("weeks","Недели"),("cashiers","Кассиры")):
            path=os.path.join(base,f"{kind}-cycle-{key}.html")
            if not os.path.isfile(path): continue
            raw=open(path,encoding="utf-8").read()
            nav='<nav class="rnav">'+''.join(
                f'<a href="{nav_kind}-cycle-{key}.html" class="{"on" if nav_kind==kind else ""}">{nav_title}</a>'
                for nav_kind,nav_title in (("index","Кураторы"),("weeks","Недели"),("cashiers","Кассиры")))+'</nav>'
            raw=re.sub(r'<nav class="rnav">.*?</nav>',nav,raw,count=1,flags=re.S)
            options=[f'<option value="{kind}.html">{html.escape(current_label)}</option>',
                     '<option disabled>──────── История ────────</option>']
            for archive_key,label in archives:
                selected=' selected' if archive_key==key else ''
                options.append(f'<option value="{kind}-cycle-{archive_key}.html"{selected}>{html.escape(label)}</option>')
            select=f'<select aria-label="Выберите период" onchange="location.href=this.value">{"".join(options)}</select>'
            raw=re.sub(r'<select aria-label="Выберите период"[^>]*>.*?</select>',select,raw,count=1,flags=re.S)
            with open(path,"w",encoding="utf-8") as out: out.write(raw)

def strip_tags(value):
    value = re.sub(r"<script.*?</script>|<style.*?</style>", " ", value, flags=re.I|re.S)
    value = re.sub(r"<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", html.unescape(value)).strip()

def number(value):
    m = re.search(r"-?[\d\s]+", str(value or ""))
    return int(re.sub(r"\D", "", m.group(0))) if m else 0

def mcp_query(sql):
    body=json.dumps({"jsonrpc":"2.0","id":1,"method":"tools/call",
        "params":{"name":"query_db","arguments":{"sql":sql}}}).encode()
    last=None
    for attempt in range(4):
        try:
            req=urllib.request.Request(GATEWAY,data=body,headers={"Content-Type":"application/json"})
            raw=urllib.request.urlopen(req,timeout=120).read().decode()
            txt=json.loads(raw)["result"]["content"][0]["text"]
            data=json.loads(txt).get("data",{}).get("data",[])
            if isinstance(data,dict) and data.get("stat")=="error": raise RuntimeError(data.get("error","SQL error"))
            if data in (["empty"],"empty"): return []
            return data if isinstance(data,list) else [data]
        except Exception as exc:
            last=exc
            if attempt==3: raise RuntimeError(f"MCP query failed: {last}")
            time.sleep(2*(attempt+1))

def chunks(values,n=300):
    values=list(values)
    for i in range(0,len(values),n): yield values[i:i+n]

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
    last_error=None
    for attempt in range(3):
        try:
            raw = op.open(CRM + "/account/debtors/list", body, timeout=90).read().decode(errors="ignore")
            if 'id="customtable"' in raw or "id='customtable'" in raw:
                return raw
            last_error=RuntimeError("CRM Qarzdorlar table is unavailable")
        except Exception as exc:
            last_error=exc
        if attempt < 2:
            time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"CRM Qarzdorlar table is unavailable after 3 attempts: {last_error}")

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
    table=m.group(1)
    head=re.search(r'<thead[^>]*>(.*?)</thead>',table,re.I|re.S)
    headers=[strip_tags(x).lower() for x in re.findall(r'<th[^>]*>(.*?)</th>',head.group(1),re.I|re.S)] if head else []
    def col(fragment, occurrence=0):
        hits=[i for i,x in enumerate(headers) if fragment in x]
        return hits[occurrence] if len(hits)>occurrence else -1
    ix_date=col("qorz bo'lish"); ix_name=col("ism"); ix_student_status=col("status",0)
    ix_admin=col("admin"); ix_tariff=col("tarif",0); ix_plan=col("standart")
    ix_status=col("status",1); ix_debt=col("qarzdorlik"); ix_paid=col("summasi")
    required=[ix_date,ix_name,ix_student_status,ix_admin,ix_tariff,ix_plan,ix_status,ix_debt,ix_paid]
    if min(required)<0: raise RuntimeError("CRM Qarzdorlar columns changed")
    out = []
    for tr in re.findall(r'<tr[^>]*>(.*?)</tr>', table, re.I|re.S):
        raw_cells = re.findall(r'<t[dh][^>]*>(.*?)</t[dh]>', tr, re.I|re.S)
        cells = [strip_tags(x) for x in raw_cells]
        if len(cells) < len(headers) or not any(x.isdigit() for x in cells[:2]): continue
        sid_match=re.search(r'/account/student_list/detail/(\d+)',raw_cells[ix_name],re.I)
        student_id=int(sid_match.group(1)) if sid_match else 0
        try: due = datetime.datetime.strptime(cells[ix_date], "%d.%m.%Y").date()
        except ValueError: due = START
        out.append({"student_id":student_id,"due":due,"name":cells[ix_name],"student_status":cells[ix_student_status],"admin":cells[ix_admin],
                    "tariff":cells[ix_tariff],"plan":number(cells[ix_plan]),"status":cells[ix_status],
                    "debt":number(cells[ix_debt]),"paid":number(cells[ix_paid])})
    return out

def status_key(value):
    s = value.lower().replace("‘", "'").replace("’", "'")
    if "bitirdi" in s: return "bit"
    if any(x in s for x in ("referral","referal","sarafan")): return "referral"
    if any(x in s for x in ("to'landi","tolandi","to'lagan","tolagan","to'langan","tolangan","paid")): return "paid"
    if "muz" in s: return "frozen"
    if "arxiv" in s or "o'ch" in s: return "deleted"
    return "debt"

def row_bucket(row):
    return row.get("_bucket") or status_key(row["status"])

def is_effective_paid(row):
    return row_bucket(row) in ("paid","bit","referral")

def validate(label, c, rows):
    counts = {k:0 for k in ("paid","bit","referral","debt","frozen","deleted")}
    for row in rows: counts[row_bucket(row)] += 1
    if len(rows) != c["total"] or sum(counts.values()) != c["total"]:
        raise RuntimeError(f"{label}: CRM card/table mismatch")
    return counts

def dashboard_row(team, short, full, c, source_rows, hidden=False):
    counts = validate(short, c, source_rows)
    paid = counts["paid"]+counts["bit"]+counts["referral"]
    today = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=5))).date()
    return dict(team=team, short=short, full=full, paid=paid, tol=counts["paid"], bit=counts["bit"], sar=counts["referral"],
        muz=counts["frozen"], arx=counts["deleted"], plan=c["total"], plansum=c["plan"],
        debt=counts["debt"], sob=c["fact"],
        pct=round(paid/c["total"]*100) if c["total"] else 0,
        due=sum(1 for x in source_rows if x["due"]<=today), hidden=hidden)

def detail(source_rows):
    out=[]
    for i,x in enumerate(source_rows):
        team_short=CURATORS.get(x["admin"])
        curator=x.get("_curator_short") or (team_short[1] if team_short else "Biriktirilmagan")
        bucket=row_bucket(x); paid=bucket in ("paid","bit","referral")
        out.append(dict(id=x.get("student_id") or i+1,name=x["name"],curator=curator,
            cashier=x.get("cashier","Biriktirilmagan"),
            bucket=bucket,
            status="To'lagan" if bucket=="paid" else x["status"],
            paid=x["paid"] if bucket=="paid" else 0,debt=x["debt"],period=x["due"].strftime("%d.%m.%Y")))
    return out

def student_group_cashiers(source_rows):
    """MCP: student -> preferred group -> group's admin and cashier."""
    ids={int(x.get("student_id") or 0) for x in source_rows if int(x.get("student_id") or 0)>0}
    best={}
    for part in chunks(ids):
        sql=("SELECT sub.STUDENT_ID sid,sub.ID sub_id,sub.ACTIVE sub_active,g.ID group_id,g.NAME group_name,"
             "g.ADMIN_ID admin_id,g.CASHIER_ID cashier_id,"
             "CONCAT_WS(' ',a.NAME,a.SURNAME) admin_name,CONCAT_WS(' ',c.NAME,c.SURNAME) cashier_name "
             "FROM subscribe_list sub LEFT JOIN group_list g ON g.ID=sub.GROUP_ID "
             "LEFT JOIN gl_sys_users a ON a.ID=g.ADMIN_ID LEFT JOIN gl_sys_users c ON c.ID=g.CASHIER_ID "
             "WHERE sub.ORG_ID=6 AND sub.STUDENT_ID IN (%s) "
             "ORDER BY sub.STUDENT_ID,sub.ACTIVE DESC,sub.ID DESC" % ",".join(map(str,part)))
        for row in mcp_query(sql):
            sid=int(row.get("sid") or 0)
            if sid and sid not in best: best[sid]=row
    return best

def cashier_dataset(source_rows, group_meta):
    grouped={}
    for row in source_rows:
        meta=group_meta.get(int(row.get("student_id") or 0),{})
        admin_id=int(meta.get("admin_id") or 0)
        if admin_id in TEST_ADMIN_IDS:
            row["cashier"]="Test akkaunt"
            continue
        if admin_id not in CURRENT_ADMIN_IDS:
            row["cashier"]="Boshqa admin"
            continue
        cid=int(meta.get("cashier_id") or 0)
        if not cid:
            row["cashier"]="Biriktirilmagan"
            continue
        cname=re.sub(r"\s+"," ",html.unescape(str(meta.get("cashier_name") or "")).strip())
        key=str(cid)
        label=cname or ("Kassir "+key)
        d=grouped.setdefault(key,dict(name=label,admins=set(),source=[]))
        admin_name=re.sub(r"\s+"," ",html.unescape(str(meta.get("admin_name") or "")).strip())
        if admin_name: d["admins"].add(admin_name.split()[0])
        row["cashier"]=label
        d["source"].append(row)
    synthetic=[]; catalog=[]
    today=datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=5))).date()
    for cid,d in grouped.items():
        rr=d["source"]; paid=[x for x in rr if is_effective_paid(x)]
        actual_paid=[x for x in rr if row_bucket(x)=="paid"]
        short="cashier_"+cid
        teams=[x.get("_curator_team") or CURATORS.get(x["admin"],("B",))[0] for x in rr]
        team="A" if teams.count("A")>=teams.count("B") else "B"
        synthetic.append(dict(team=team,short=short,full=d["name"],paid=len(paid),tol=len(actual_paid),
            bit=sum(row_bucket(x)=="bit" for x in rr),sar=sum(row_bucket(x)=="referral" for x in rr),
            muz=sum(row_bucket(x)=="frozen" for x in rr),arx=sum(row_bucket(x)=="deleted" for x in rr),
            plan=len(rr),plansum=sum(x["plan"] for x in rr),debt=sum(row_bucket(x)=="debt" for x in rr),sob=sum(x["paid"] for x in actual_paid),
            pct=round(len(paid)/len(rr)*100) if rr else 0,due=sum(x["due"]<=today for x in rr)))
        labels=sorted(d["admins"])
        catalog.append((d["name"].split()[0] if d["name"]!="Biriktirilmagan" else d["name"],team,[short],labels,d["name"]))
    return synthetic,catalog

def week_stats(month_rows):
    result=[]
    for _key,a,b,_label in PERIODS[1:]:
        rr=[x for x in month_rows if a<=x["due"]<=b]
        paid=[x for x in rr if is_effective_paid(x)]
        actual_paid=[x for x in rr if row_bucket(x)=="paid"]
        def team_of(x): return x.get("_curator_team") or CURATORS.get(x["admin"],("U",))[0]
        result.append(dict(ws=a,we=b,total=len(rr),paid=len(paid),
            qarz=sum(row_bucket(x)=="debt" for x in rr),
            muz=sum(row_bucket(x)=="frozen" for x in rr),
            arx=sum(row_bucket(x)=="deleted" for x in rr),
            sob=sum(x["paid"] for x in actual_paid),
            A_total=sum(team_of(x)=="A" for x in rr),A_paid=sum(team_of(x)=="A" for x in paid),A_sob=sum(x["paid"] for x in actual_paid if team_of(x)=="A"),
            B_total=sum(team_of(x)=="B" for x in rr),B_paid=sum(team_of(x)=="B" for x in paid),B_sob=sum(x["paid"] for x in actual_paid if team_of(x)=="B")))
    return result

def main():
    archive_previous_cycle()
    ui.MONTH_ARCHIVES=archived_cycles()
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
        status_select=re.search(r'<select[^>]*(?:name|id)=["\']filter_status["\'][^>]*>(.*?)</select>',raw,re.I|re.S)
        if status_select:
            options=[]
            for value,label in re.findall(r'<option[^>]*value=["\']([^"\']*)["\'][^>]*>(.*?)</option>',status_select.group(1),re.I|re.S):
                options.append(f"{value}={strip_tags(label)}")
            print("CRM_STATUS_OPTIONS", " | ".join(options))
        status_counts={}
        for item in rows:
            key=item["status"].strip() or "(empty)"
            status_counts[key]=status_counts.get(key,0)+1
        print("CRM_ROW_STATUSES", " | ".join(f"{k}={v}" for k,v in sorted(status_counts.items())))
        print("CRM_CHECK", " ".join(f"{k}={v}" for k,v in summary.items()))
        if summary["total"] != summary["paid"]+summary["debt"]+summary["frozen"]+summary["deleted"]:
            raise RuntimeError("CRM status totals do not match")
        if rows and len(rows) != summary["total"]:
            raise RuntimeError("CRM table row count does not match cards")
        return

    # Exact month: one full-module request plus one request per ranked curator.
    # Week pages are slices of this same validated 1331-row CRM response, avoiding 40 slow requests.
    all_raw=fetch(op,START,END); month_card=card(all_raw); month_source=table_rows(all_raw)
    # The unfiltered table's visible STATUS text is not the authoritative CRM
    # filter bucket. Build the exact bucket map from the four Qarzdorlar filters.
    bucket_by_student={}
    for bucket,crm_status in (("paid","paid"),("debt","qarzdor"),("frozen","frozen"),("deleted","deleted")):
        filtered_raw=fetch(op,START,END,status=crm_status)
        filtered_card=card(filtered_raw)
        filtered_rows=table_rows(filtered_raw)
        if len(filtered_rows) != filtered_card["total"]:
            raise RuntimeError(f"CRM {bucket} filter card/table mismatch")
        for item in filtered_rows:
            sid=item.get("student_id")
            if not sid or sid in bucket_by_student:
                raise RuntimeError(f"CRM duplicate/missing student in {bucket} filter")
            bucket_by_student[sid]=bucket
    if len(bucket_by_student) != month_card["total"]:
        raise RuntimeError("CRM filtered status total does not match month total")
    for item in month_source:
        base_bucket=bucket_by_student.get(item.get("student_id"))
        visible=status_key(item["status"])
        item["_bucket"]=visible if visible in ("bit","referral") else base_bucket
        if not item["_bucket"]:
            raise RuntimeError("CRM student missing from status filters")
    validate("month",month_card,month_source)
    if sum(bool(x.get("student_id")) for x in month_source) != len(month_source):
        raise RuntimeError("CRM student IDs are missing; refusing to publish cashier data")
    group_meta=student_group_cashiers(month_source)
    if len(group_meta) != len({x["student_id"] for x in month_source}):
        missing=len({x["student_id"] for x in month_source})-len(group_meta)
        raise RuntimeError(f"MCP group lookup incomplete ({missing} students); refusing to publish")
    # Canonical curator identity comes from the student's assigned group's ADMIN_ID.
    # CRM display names can differ from CURATORS keys (for example Fotima).
    for x in month_source:
        meta=group_meta.get(x["student_id"],{})
        curator=CURATOR_BY_ID.get(int(meta.get("admin_id") or 0))
        # CRM currently displays this curator as "Fotima", while its filter
        # record is stored as "Fotimabonu Abdulkhakova".
        if not curator and x["admin"].strip().lower().startswith("fotima"):
            curator=CURATOR_BY_ID[int(CURATORS["Fotimabonu Abdulkhakova"][2])]
        if curator:
            x["_curator_full"],x["_curator_team"],x["_curator_short"]=curator
    month_rows=[]; known_total=known_plan=known_fact=known_frozen=known_deleted=0; known_counts={k:0 for k in ("paid","bit","referral","debt","frozen","deleted")}
    for full,(team,short,cid) in CURATORS.items():
        cr=fetch(op,START,END,curator=cid); cc=card(cr); tr=table_rows(cr)
        for x in tr:
            base_bucket=bucket_by_student.get(x.get("student_id"))
            visible=status_key(x["status"])
            x["_bucket"]=visible if visible in ("bit","referral") else base_bucket
            if not x["_bucket"]: raise RuntimeError("Curator student missing from CRM status filters")
            known_counts[row_bucket(x)]+=1
        month_rows.append(dashboard_row(team,short,full,cc,tr))
        known_total+=cc["total"]; known_plan+=cc["plan"]; known_fact+=cc["fact"]
        known_frozen+=cc["frozen"]; known_deleted+=cc["deleted"]
    all_counts=validate("month",month_card,month_source)
    other_total=month_card["total"]-known_total
    if other_total:
        otol=all_counts["paid"]-known_counts["paid"]
        obit=all_counts["bit"]-known_counts["bit"]
        oref=all_counts["referral"]-known_counts["referral"]
        opaid=otol+obit+oref
        month_rows.append(dict(team="U",short="Biriktirilmagan",full="Boshqa adminlar",paid=opaid,tol=otol,bit=obit,sar=oref,
            muz=month_card["frozen"]-known_frozen,arx=month_card["deleted"]-known_deleted,
            plan=other_total,plansum=month_card["plan"]-known_plan,
            debt=all_counts["debt"]-known_counts["debt"],
            sob=month_card["fact"]-known_fact,pct=round(opaid/other_total*100) if other_total else 0,due=0,hidden=True))
    datasets=[("month",START,END,PERIODS[0][3],month_card,month_source,month_rows)]

    # Weekly count-first views from the exact month list. Amounts come from the same CRM row columns.
    for key,a,b,label in PERIODS[1:]:
        source=[x for x in month_source if a<=x["due"]<=b]
        rows=[]
        for full,(team,short,_cid) in CURATORS.items():
            rr=[x for x in source if x.get("_curator_full")==full]
            c=dict(total=len(rr),plan=sum(x["plan"] for x in rr),
                   fact=sum(x["paid"] for x in rr if row_bucket(x)=="paid"))
            rows.append(dashboard_row(team,short,full,c,rr))
        rr=[x for x in source if not x.get("_curator_full")]
        if rr:
            c=dict(total=len(rr),plan=sum(x["plan"] for x in rr),fact=sum(x["paid"] for x in rr if row_bucket(x)=="paid"))
            rows.append(dashboard_row("U","Biriktirilmagan","Boshqa adminlar",c,rr,True))
        c=dict(total=len(source),plan=sum(x["plan"] for x in source),fact=sum(x["paid"] for x in source if row_bucket(x)=="paid"))
        datasets.append((key,a,b,label,c,source,rows))

    month_rows=datasets[0][5]; weeks=week_stats(month_rows)
    now=datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=5))).replace(tzinfo=None)
    ui.IS_CI=os.environ.get("GITHUB_ACTIONS")=="true"
    ui.PREVIEW=False
    ui.CUR=[(team,short,full) for full,(team,short,_cid) in CURATORS.items()]
    for key,a,b,label,c,source_rows,rows in datasets:
        cashier_rows,cashier_catalog=cashier_dataset(source_rows,group_meta)
        ui.PERIOD=key; ui.DETAIL_DATA=detail(source_rows)
        ui.CASHIERS=cashier_catalog; ui.CASHIER_ROWS=cashier_rows
        due_total=sum(r["due"] for r in rows); due_paid=sum(min(r["paid"],r["due"]) for r in rows)
        ui.render(now,a,b,rows,c["total"],c["plan"],weeks,due_total,due_paid,PERIODS,label)
    refresh_archive_navigation()

if __name__ == "__main__":
    main()
