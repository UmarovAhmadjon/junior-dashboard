#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Junior · Kuratorlar QARZ YIG'ISH dashboardi — MANBA: GOOGLE SHEETS (baza emas).

Har soatda: Google Tabladan o'qiydi -> kuratorlar.html yaratadi -> Netlifyga chiqaradi.
Manba: kuratorlar o'zlari yuritadigan jadval (To'lagan/Qarzdor statuslari — aniq).
  Лист12  = reja (qarzdorlar soni + summa, TEAM A/B)
  har kurator varag'i = student-status ro'yxati (C=status, D=summa)

TAMOYIL: SON birinchi, summa ikkinchi.
"""
import json, re, os, csv, io, datetime, sys, hashlib, urllib.request, urllib.parse, calendar, email.utils, base64, zipfile
import xml.etree.ElementTree as ET

BASE = os.path.dirname(os.path.abspath(__file__))
SHEET_ID = "1NOAYemdD1y2SvE7W6mEvO5ty80sqq01i1iNWbku5Ei4"
SUMMARY_TAB = "Лист12"
PREVIEW = ("preview" in sys.argv)   # `python3 live_update.py preview` -> preview.html (jonli index.html tegilmaydi)
IS_CI = os.environ.get("GITHUB_ACTIONS")=="true"   # GitHub Actions (bulut) rejimi: fayl yoziladi, commitni workflow qiladi

# Kassir -> qaysi kuratorlar bilan ishlaydi (Лист12 dan, foydalanuvchi bergan)
CASHIERS = [
 ("Muxlisa","A",["Dilafruz","Munisa"]),
 ("Nozima","A",["Madina","Jasmina","Fotima"]),
 ("Islom","B",["Marjona","Halima"]),
 ("Abror","B",["Sabrina","Maryam","Aziza"]),
]

# kurator: (team, tab/short nomi, to'liq ism) — tartib = ko'rsatish tartibi
CUR = [
 ("A","Fotima","Fotimabonu Abdulkhakova"),
 ("A","Dilafruz","Dilafruz Shokirova"),
 ("A","Madina","Madina Normatova"),
 ("A","Jasmina","Jasmina Tolibova"),
 ("A","Munisa","Munisa Sobirjonova"),
 ("B","Sabrina","Sabrina Salimova"),
 ("B","Marjona","Marjona Pardayeva"),
 ("B","Halima","Xalima Ismoiljonova"),
 ("B","Maryam","Maryam Safarova"),
 ("B","Aziza","Aziza Qurvonaliyeva"),
]

# ================== Google Sheets o'qish (TO'LIQ xlsx — filtrga bog'liq emas) ==================
# MUHIM: gviz/CSV faqat FILTRLANGAN (ko'rinadigan) qatorlarni qaytaradi. Xodimlar ish jarayonida
# filtr qo'yadi, shuning uchun butun kitobni xlsx sifatida yuklab, BARCHA qatorlarni o'qiymiz.
_M='{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
_R='{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'
def _colnum(ref):
    n=0
    for ch in ref:
        if ch.isalpha(): n=n*26+(ord(ch.upper())-64)
        else: break
    return n
def _parse_sheet(z, path, ss):
    root=ET.fromstring(z.read(path)); grid={}; maxr=maxc=0
    for c in root.iter(_M+'c'):
        ref=c.get('r') or ""
        m=re.match(r'([A-Z]+)(\d+)',ref)
        if not m: continue
        col=_colnum(m.group(1)); row=int(m.group(2))
        v=c.find(_M+'v'); val=""
        if v is not None:
            val=ss[int(v.text)] if c.get('t')=='s' else (v.text or "")
        else:
            isf=c.find(_M+'is')
            if isf is not None: val="".join(t.text or "" for t in isf.iter(_M+'t'))
        grid[(row,col)]=val; maxr=max(maxr,row); maxc=max(maxc,col)
    return [[grid.get((r,cc),"") for cc in range(1,maxc+1)] for r in range(1,maxr+1)]

def load_workbook():
    url=f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=xlsx"
    data=urllib.request.urlopen(urllib.request.Request(url,headers={"User-Agent":"junior-dash"}),timeout=120).read()
    z=zipfile.ZipFile(io.BytesIO(data))
    ss=["".join(t.text or "" for t in si.iter(_M+'t')) for si in ET.fromstring(z.read('xl/sharedStrings.xml')).findall(_M+'si')] if 'xl/sharedStrings.xml' in z.namelist() else []
    rels={r.get('Id'):r.get('Target') for r in ET.fromstring(z.read('xl/_rels/workbook.xml.rels'))}
    wb=ET.fromstring(z.read('xl/workbook.xml'))
    out={}
    for s in wb.iter(_M+'sheet'):
        tgt=rels[s.get(_R+'id')]
        path='xl/'+tgt if not tgt.startswith('/') else tgt[1:]
        out[s.get('name')]=_parse_sheet(z, path, ss)
    return out

def norm(s): return (s or "").strip().lower().replace('`',"'").replace('ʻ',"'").replace('’',"'")
def num(s):
    # "74,812,000" / "188" / "188.0" / "390000.0" -> to'g'ri butun son
    s=str(s or "").replace(",","").replace(" ","")
    m=re.search(r'-?\d+(?:\.\d+)?', s)
    return int(float(m.group())) if m else 0
def pdate(s):
    s=(s or "").strip()
    for fmt in ("%d.%m.%Y","%d,%m,%Y","%Y-%m-%d","%d/%m/%Y"):
        try: return datetime.datetime.strptime(s,fmt).date()
        except Exception: pass
    try:
        n=int(float(s))
        if 30000<n<80000: return datetime.date(1899,12,30)+datetime.timedelta(days=n)  # excel serial
    except Exception: pass
    return None

# PLAN (jami qarzdorlar soni + summa) — Лист12 dan.
# FAKT (oplatili SONI) = To'lagan + Bitirdi + Sarafan — kurator varaqlaridan (statuslar).
#   "Собрано"(summa) faqat To'lagan'dan — Bitirdi/Sarafan pul to'lamaydi.
# "Ещё должны" = PLAN - oplatili.
def read_weeks(WB, c_start, c_end):
    """Sikl bo'yicha haftalik hisobot: har student A-ustun sanasi bo'yicha haftaga kiradi.
    Haftalar: 26.06–04.07, so'ng yakshanba boshlaydigan 7 kunliklar (05–11, 12–18, 19–25)."""
    # haftalarni qurish: birinchisi c_start dan birinchi shanba oxirigacha, keyin 7 kunlik
    weeks=[]; ws=c_start
    while ws<=c_end:
        we=ws+datetime.timedelta(days=(5-ws.weekday())%7)   # shanba (weekday 5)
        if (we-ws).days<4: we+=datetime.timedelta(days=7)   # juda kalta bo'lsa keyingi shanbagacha cho'zamiz
        if we>c_end: we=c_end
        weeks.append((ws,we))
        ws=we+datetime.timedelta(days=1)
    def blank(a,b): return dict(ws=a,we=b,total=0,paid=0,qarz=0,muz=0,arx=0,sob=0,
                                A_total=0,A_paid=0,A_sob=0,B_total=0,B_paid=0,B_sob=0)
    agg=[blank(a,b) for a,b in weeks]
    from collections import Counter
    for team,short,_full in CUR:
        rows=WB.get(short,[])
        cc=Counter()
        for r in rows:
            for ci,v in enumerate(r):
                if norm(v)=="qarzdor": cc[ci]+=1
        sc=cc.most_common(1)[0][0] if cc else 2
        for r in rows:
            st=norm(r[sc]) if len(r)>sc else ""
            ok_paid = st in ("to'lagan","to'ladi","bitirdi") or st.startswith("sarafan")
            if not ok_paid and st not in ("qarzdor","muzlagan","arxiv"): continue
            d=pdate(r[0] if r else "")
            if not d: continue
            for A in agg:
                if A['ws']<=d<=A['we']:
                    amt=num(r[sc+1]) if len(r)>sc+1 else 0
                    A['total']+=1; A[team+'_total']+=1
                    if ok_paid:
                        A['paid']+=1; A[team+'_paid']+=1
                        if st in ("to'lagan","to'ladi"):
                            A['sob']+=amt; A[team+'_sob']+=amt
                    elif st=="qarzdor": A['qarz']+=1
                    elif st=="muzlagan": A['muz']+=1
                    else: A['arx']+=1
                    break
    return agg

def read_deadline(WB, c_start, c_end, today):
    """Grafik bo'yicha holat: muddati (A-ustun) o'tgan studentlar soni (reja) va ulardan to'laganlar (fakt)."""
    cut = min(today, c_end)
    from collections import Counter
    due_total=due_paid=0; per={}
    for team,short,_full in CUR:
        rows=WB.get(short,[])
        cc=Counter()
        for r in rows:
            for ci,v in enumerate(r):
                if norm(v)=="qarzdor": cc[ci]+=1
        sc=cc.most_common(1)[0][0] if cc else 2
        dt=0
        for r in rows:
            st=norm(r[sc]) if len(r)>sc else ""
            ok_paid = st in ("to'lagan","to'ladi","bitirdi") or st.startswith("sarafan")
            if not ok_paid and st not in ("qarzdor","muzlagan","arxiv"): continue
            d=pdate(r[0] if r else "")
            if not d or not (c_start<=d<=cut): continue
            due_total+=1; dt+=1
            if ok_paid: due_paid+=1
        per[short]=dt
    return due_total, due_paid, per

def deadline_section(due_total, due_paid, today, c_end):
    overdue = due_total-due_paid
    pct = round(due_paid/due_total*100) if due_total else 0
    on_track = pct>=90
    if pct>=100: verdict, vcol = "ИДЁМ ПО ПЛАНУ ✓", "#3dffa2"
    elif pct>=90: verdict, vcol = f"почти по плану · не хватает {overdue}", "#ffd21e"
    else: verdict, vcol = f"ОТСТАЁМ на {overdue} оплат", "#ff4f28"
    fill="goldf" if pct>=100 else ("okf" if pct>=90 else "lagf")
    return f"""<div class="panel lbcard"><div class="ph"><span class="ptitle">Дедлайн <i class="sl">//</i> идём ли по графику</span><span class="lbleg">срок оплаты уже прошёл → должны были оплатить · факт · просрочка</span></div>
  <div class="sbwrap">
    <div class="sbrow"><span class="sbl" style="width:180px">План к {today.strftime('%d.%m')}</span><span class="sbtrack"><span class="sbfill" style="width:100%;background:var(--baridle)"></span></span><span class="sbn" style="width:70px">{due_total}</span></div>
    <div class="sbrow"><span class="sbl" style="width:180px">Факт оплатили</span><span class="sbtrack"><span class="sbfill" style="width:{pct if pct<=100 else 100}%;background:#3dffa2"></span></span><span class="sbn" style="width:70px">{due_paid}</span></div>
    <div class="sbrow"><span class="sbl" style="width:180px">Просрочка</span><span class="sbtrack"><span class="sbfill" style="width:{round(overdue/due_total*100) if due_total else 0}%;background:#ff4f28"></span></span><span class="sbn" style="width:70px">{overdue}</span></div>
    <div class="sbrow"><span class="sbl" style="width:180px">Выполнение графика</span><b style="font:800 1.6em/1 'Barlow Condensed';color:{vcol}">{pct}%</b><span style="font:800 .95em/1.2 Manrope;color:{vcol};margin-left:14px">{verdict}</span></div>
  </div></div>"""

def week_section(weeks_agg, today):
    def lbl(a): return f"{a['ws'].strftime('%d.%m')}–{a['we'].strftime('%d.%m')}"
    def bar(pct):
        fill="goldf" if pct>=100 else ("okf" if pct>=40 else "lagf")
        return f'<span class="track"><span class="fill {fill}" style="--w:{min(100,pct)}%"></span><span class="tfin"></span></span>'
    def gapc(pct): return "goldg" if pct>=100 else ("okg" if pct>=40 else "badg")
    rows=""
    for i,A in enumerate(weeks_agg,1):
        cur = A['ws']<=today<=A['we']
        pct = round(A['paid']/A['total']*100) if A['total'] else 0
        mark = ' style="outline:2px solid var(--volt);outline-offset:-2px"' if cur else ""
        nm = f"Неделя {i}" + (" · тек." if cur else "")
        rows+=f"""<div class="lrow"{mark}>
    <span class="pos"><i>{i}</i></span>
    <span class="lnm">{nm} <i style="color:var(--mut);font-weight:600;font-size:.78em">· {lbl(A)}</i></span>
    {bar(pct)}
    <span class="fact"><b>{A['paid']}</b><i>/{A['total']} чел</i></span>
    <span class="gap {gapc(pct)}">{pct}%</span><span class="wk">{mln(A['sob'])}м</span></div>"""
        # jamoalar bo'yicha ichki qatorlar
        for T in ("A","B"):
            t_tot=A[T+'_total']; t_paid=A[T+'_paid']; t_sob=A[T+'_sob']
            tpct=round(t_paid/t_tot*100) if t_tot else 0
            rows+=f"""<div class="lrow" style="opacity:.92">
    <span class="pos"><i></i></span>
    <span class="lnm"><span class="tbadge t{T}">{T}</span>TEAM {T}</span>
    {bar(tpct)}
    <span class="fact"><b>{t_paid}</b><i>/{t_tot} чел</i></span>
    <span class="gap {gapc(tpct)}">{tpct}%</span><span class="wk">{mln(t_sob)}м</span></div>"""
    tp=sum(A['paid'] for A in weeks_agg); tt=sum(A['total'] for A in weeks_agg); ts=sum(A['sob'] for A in weeks_agg)
    return f"""<div class="panel lbcard"><div class="ph"><span class="ptitle">Недельный отчёт <i class="sl">//</i> по сроку оплаты</span><span class="lbleg">оплатили / всего со сроком в неделе · % · собрано · внутри — TEAM A/B</span></div>
  <div class="lhead"><span>№</span><span>Период</span><span>Прогресс</span><span>Оплат./всего</span><span>%</span><span>Собр.</span></div>
  {rows}
  <div class="ltot">За цикл: оплатили <b>{tp}</b> из <b>{tt}</b> со сроком внутри цикла · собрано <b>{mln(ts)} млн</b></div></div>"""

def read_curator(rows, win_start=None, win_end=None):
    """kurator varag'idan FAKTni sanaydi: oplatili(To'lagan+Bitirdi+Sarafan) + собрано(To'lagan).
    Muzlagan/Arxiv — faqat sanasi [win_start..win_end] oralig'ida (statuslar panelida ko'rsatish uchun)."""
    from collections import Counter
    colcnt=Counter()
    for r in rows:
        for ci,val in enumerate(r):
            if norm(val)=="qarzdor": colcnt[ci]+=1
    scol = colcnt.most_common(1)[0][0] if colcnt else 2
    tol=bit=sar=muz=arx=0; collected=0
    for r in rows:
        st = norm(r[scol]) if len(r)>scol else ""
        amt = num(r[scol+1]) if len(r)>scol+1 else 0
        if st in ("to'lagan","to'ladi"): tol+=1; collected+=amt
        elif st=="bitirdi": bit+=1
        elif st.startswith("sarafan"): sar+=1
        elif st in ("muzlagan","muzladi","arxiv"):
            d=pdate(r[0] if r else "")
            if win_start is None or (d and win_start<=d<=win_end):
                if st=="arxiv": arx+=1
                else: muz+=1
    return dict(paid=tol+bit+sar, tol=tol, bit=bit, sar=sar, muz=muz, arx=arx, collected=collected)

def read_plan(rows):
    """Лист12 dan reja: per-kurator (soni,summa) + JAMI (umumiy qarzdorlar soni, umumiy summa)."""
    want = {norm(c[1]) for c in CUR}
    per={}; gcnt=0; gsum=0
    for r in rows:
        if not r: continue
        nm = norm(r[0])
        if nm in want and len(r)>=3 and num(r[1])>0:
            per[nm]=(num(r[1]),num(r[2]))
        joined = norm(" ".join(x or "" for x in r))
        nums = [num(x) for x in r if num(x)>0]
        if "umumiy qarzdor" in joined and nums: gcnt=max(nums)   # 1501
        elif "umumiy summa" in joined and nums: gsum=max(nums)   # 585 630 000
    # zaxira: agar JAMI topilmasa — per-curator yig'indisi
    if not gcnt: gcnt=sum(v[0] for v in per.values())
    if not gsum: gsum=sum(v[1] for v in per.values())
    return per, gcnt, gsum

# ================== Netlify avtopublikatsiya ==================
NETLIFY_SITE = "dynamic-dango-9dbf89"
NETLIFY_TOKEN_FILE = os.path.join(BASE, ".netlify_token")
SIG_FILE = os.path.join(BASE, ".last_deploy")

def _netlify_token():
    t = os.environ.get("NETLIFY_TOKEN","").strip()
    if not t and os.path.isfile(NETLIFY_TOKEN_FILE):
        t = open(NETLIFY_TOKEN_FILE,encoding="utf-8").read().strip()
    return t

def _api(url, token, data=None, ctype="application/json", method=None):
    req = urllib.request.Request(url, data=data, method=method,
            headers={"Authorization":"Bearer "+token, "Content-Type":ctype, "User-Agent":"junior-dash"})
    return urllib.request.urlopen(req, timeout=90).read()

def deploy_netlify(html_path, sig=None):
    token = _netlify_token()
    if not token:
        print("  netlify: token yo'q — o'tkazib yuborildi"); return False
    if sig is not None and os.path.isfile(SIG_FILE) and open(SIG_FILE,encoding="utf-8").read().strip()==sig:
        print("  netlify: ma'lumot o'zgarmadi — deploy o'tkazib yuborildi (limit tejaldi)"); return False
    site_id=None
    for s in json.loads(_api("https://api.netlify.com/api/v1/sites?per_page=100", token)):
        if s.get("name")==NETLIFY_SITE or NETLIFY_SITE in (s.get("url","") or ""):
            site_id=s["id"]; break
    if not site_id:
        print(f"  netlify: '{NETLIFY_SITE}' topilmadi"); return False
    with open(html_path,"rb") as f: content=f.read()
    sha1=hashlib.sha1(content).hexdigest()
    dep=json.loads(_api(f"https://api.netlify.com/api/v1/sites/{site_id}/deploys",
                        token, data=json.dumps({"files":{"/index.html":sha1}}).encode()))
    if sha1 in (dep.get("required") or []):
        _api(f"https://api.netlify.com/api/v1/deploys/{dep['id']}/files/index.html",
             token, data=content, ctype="application/octet-stream", method="PUT")
    if sig is not None:
        with open(SIG_FILE,"w",encoding="utf-8") as f: f.write(sig)
    print(f"  netlify: deploy OK -> https://{NETLIFY_SITE}.netlify.app/")
    return True

# ================== GitHub Pages avtopublikatsiya ==================
GH_OWNER = "UmarovAhmadjon"
GH_REPO  = "junior-dashboard"
GH_TOKEN_FILE = os.path.join(BASE, ".github_token")

def _gh_token():
    t = os.environ.get("GITHUB_TOKEN","").strip()
    if not t and os.path.isfile(GH_TOKEN_FILE):
        t = open(GH_TOKEN_FILE,encoding="utf-8").read().strip()
    return t

def deploy_github(html_path, sig=None, path="index.html"):
    token=_gh_token()
    if not token:
        print("  github: token yo'q — o'tkazib yuborildi"); return False
    if sig is not None and os.path.isfile(SIG_FILE) and open(SIG_FILE,encoding="utf-8").read().strip()==sig:
        print("  github: ma'lumot o'zgarmadi — deploy o'tkazib yuborildi"); return False
    api=f"https://api.github.com/repos/{GH_OWNER}/{GH_REPO}/contents/{path}"
    hdr={"Authorization":"Bearer "+token,"Accept":"application/vnd.github+json","User-Agent":"junior-dash"}
    # mavjud faylning sha si (yangilash uchun kerak)
    sha=None
    try:
        r=urllib.request.urlopen(urllib.request.Request(api,headers=hdr),timeout=40)
        sha=json.load(r).get("sha")
    except Exception: pass
    with open(html_path,"rb") as f: content=base64.b64encode(f.read()).decode()
    body={"message":"update "+path,"content":content}
    if sha: body["sha"]=sha
    req=urllib.request.Request(api,data=json.dumps(body).encode(),method="PUT",headers=hdr)
    urllib.request.urlopen(req,timeout=60)
    if sig is not None:
        with open(SIG_FILE,"w",encoding="utf-8") as f: f.write(sig)
    suffix="" if path=="index.html" else path
    print(f"  github: deploy OK -> https://{GH_OWNER.lower()}.github.io/{GH_REPO}/{suffix}")
    return True

# ================== asosiy ==================
# Toshkent vaqti (UTC+5, fiksatsiyalangan). Mac soati noto'g'ri bo'lishi mumkin,
# shuning uchun real vaqtni internetdan (HTTP Date sarlavhasi) olamiz.
def real_utc():
    for host in ("https://www.cloudflare.com","https://www.google.com"):
        try:
            r=urllib.request.urlopen(urllib.request.Request(host,method="HEAD",headers={"User-Agent":"junior-dash"}),timeout=15)
            d=r.headers.get("Date")
            tt=email.utils.parsedate(d) if d else None
            if tt: return datetime.datetime(*tt[:6])
        except Exception: pass
    return datetime.datetime.utcnow()  # zaxira (kamdan-kam)
def tash_now(): return real_utc() + datetime.timedelta(hours=5)

def cycle_window(today):
    y,m,dd = today.year, today.month, today.day
    if dd>=26:
        start=datetime.date(y,m,26); em=m+1; ey=y
        if em>12: em=1;ey+=1
        end=datetime.date(ey,em,25)
    else:
        end=datetime.date(y,m,25); sm=m-1; sy=y
        if sm<1: sm=12;sy-=1
        start=datetime.date(sy,sm,26)
    return start,end

def mln(v): return f"{v/1e6:.1f}".rstrip("0").rstrip(".")
def mlrd(v): return f"{v/1e9:.2f}"
def esc(s): return str(s).replace("&","&amp;").replace("<","&lt;")

def main():
    tnow=tash_now()                 # real Toshkent vaqti (internetdan)
    today=tnow.date()
    c_start,c_end=cycle_window(today)
    WB = load_workbook()                               # butun kitob (filtrsiz, barcha qatorlar)
    per_plan, GPLAN, GPLANSUM = read_plan(WB.get(SUMMARY_TAB, []))   # Лист12: per-curator + JAMI
    win_start=c_start-datetime.timedelta(days=2); win_end=c_end      # Muzlagan/Arxiv oynasi: 24.06–25.07
    rows=[]
    for team,short,full in CUR:
        st=read_curator(WB.get(short, []), win_start, win_end)       # FAKT statuslardan (to'liq varaq)
        plan_c,_=per_plan.get(norm(short),(0,0))       # PLAN Лист12 dan (per-curator qarzdorlar soni)
        rows.append(dict(team=team,short=short,full=full,
            paid=st['paid'], tol=st['tol'], bit=st['bit'], sar=st['sar'], muz=st['muz'], arx=st['arx'],
            plan=plan_c, debt=max(0,plan_c-st['paid']), sob=st['collected'],
            pct=round(st['paid']/plan_c*100) if plan_c else 0))
    weeks_agg=read_weeks(WB, c_start, c_end)
    due_total,due_paid,due_per=read_deadline(WB, c_start, c_end, tnow.date())
    for r in rows: r['due']=due_per.get(r['short'],0)
    render(tnow,c_start,c_end,rows,GPLAN,GPLANSUM,weeks_agg,due_total,due_paid)

def render(tnow,c_start,c_end,rows,GPLAN,GPLANSUM,weeks_agg,due_total,due_paid):
    today=tnow.date()
    TOTAL_PAID=sum(r['paid'] for r in rows)
    TOTAL_PLAN=GPLAN                                   # JAMI Лист12 dan (1501)
    TOTAL_DEBT=max(0,GPLAN-TOTAL_PAID)                 # Ещё должны = PLAN - oplatili
    TOTAL_TOL=sum(r['tol'] for r in rows)
    TOTAL_BIT=sum(r['bit'] for r in rows)
    TOTAL_SAR=sum(r['sar'] for r in rows)
    TOTAL_MUZ=sum(r['muz'] for r in rows)
    TOTAL_ARX=sum(r['arx'] for r in rows)
    TOTAL_SOB=sum(r['sob'] for r in rows)
    TOTAL_PLANSUM=GPLANSUM                             # JAMI summa Лист12 dan (585 630 000)
    PCT=round(TOTAL_PAID/TOTAL_PLAN*100) if TOTAL_PLAN else 0
    PCT_SUM=round(TOTAL_SOB/TOTAL_PLANSUM*100) if TOTAL_PLANSUM else 0
    CYCLE_LABEL=f"{c_start.strftime('%d.%m')} – {c_end.strftime('%d.%m')}"
    CYCLE_LEN=(c_end-c_start).days+1; CYCLE_DAY=min(CYCLE_LEN,max(1,(today-c_start).days+1))
    UPD=tnow.strftime("%d.%m %H:%M")
    SRV_MS=calendar.timegm(tnow.timetuple())*1000   # Toshkent devor-vaqti UTC-epoch sifatida

    with open(os.path.join(BASE,"plan_source.html"),encoding="utf-8") as f: src=f.read()
    CSS=re.search(r"<style>.*?</style>",src,re.S).group(0)

    # VARIANT 3: reyting = grafik bajarilishi % (fakt / bugungacha muddati kelganlar)
    for x in rows:
        x['pace_pct'] = round(x['paid']/x['due']*100) if x.get('due',0)>0 else (100 if x['paid']>=0 else 0)
    ALL=sorted(rows,key=lambda x:(-x['pace_pct'],-x['paid']))
    for i,x in enumerate(ALL,1): x['pos']=i
    def team_tot(t):
        rr=[x for x in rows if x['team']==t]
        p=sum(x['paid'] for x in rr); pl=sum(x['plan'] for x in rr); sb=sum(x['sob'] for x in rr)
        return dict(paid=p,plan=pl,sob=sb,pct=round(p/pl*100) if pl else 0)

    # hero
    hero=f"""
<div class="hero"><div class="panel heropanel iscur">
    <div class="ph"><span class="ptitle">ЦИКЛ {CYCLE_LABEL} <i class="sl">//</i> сбор долгов</span><span class="pday">день {CYCLE_DAY} / {CYCLE_LEN}</span></div>
    <div class="hp-main">
      <span class="hp-pct" data-cnt="{PCT}" data-suf="%">0%</span>
      <span class="hp-nums"><b data-cnt="{TOTAL_PAID}">0</b><i>из {TOTAL_PLAN} должников оплатили</i></span>
    </div>
    <div class="gauge"><span class="gfill" style="--w:{PCT}%"></span><span class="gseg"></span><span class="gfin"></span></div>
    <div class="hp-stats">
      <div class="stat"><span class="st-l">Нужно собрать</span><b>{mln(TOTAL_PLANSUM)} млн</b><span class="st-s">всего за цикл</span></div>
      <div class="stat"><span class="st-l">Собрано</span><b>{mln(TOTAL_SOB)} млн</b><span class="st-s">{PCT_SUM}% · осталось {mln(TOTAL_PLANSUM-TOTAL_SOB)}м</span></div>
      <div class="stat"><span class="st-l">Оплатили</span><b>{TOTAL_PAID}</b><span class="st-s">чел. из {TOTAL_PLAN}</span></div>
      <div class="stat"><span class="st-l">Ещё должны</span><b>{TOTAL_DEBT}</b><span class="st-s">{TOTAL_PLAN} − {TOTAL_PAID}</span></div>
    </div>
  </div>
  <div class="panel week"><div class="ph"><span class="ptitle">Статусы <i class="sl">//</i> все кураторы</span><span class="wnow">оплатили <b>{TOTAL_PAID}</b> · должны <b>{TOTAL_DEBT}</b></span></div>
  {status_bars(TOTAL_TOL,TOTAL_BIT,TOTAL_SAR,TOTAL_MUZ,TOTAL_ARX,TOTAL_DEBT)}
  </div></div>
"""
    ta,tb=team_tot("A"),team_tot("B"); lead="A" if ta['pct']>=tb['pct'] else "B"
    def plate(t,tt,cls):
        return f"""<div class="cplate {cls}"><div class="cp-l"><div class="cp-lab">{'🏆 ' if t==lead else ''}TEAM {t}</div><div class="cp-nm">{tt['paid']} / {tt['plan']} оплатили · {mln(tt['sob'])} млн</div></div><div class="cp-n"><b>{tt['pct']}</b><span>% оплат</span></div></div>"""
    champ=f'<div class="champs" style="grid-template-columns:1fr 1fr">{plate("A",ta,"gold" if lead=="A" else "silver")}{plate("B",tb,"gold" if lead=="B" else "silver")}</div>'

    best=max(ALL,key=lambda r:r['pct'])
    tk=[f"🎯 Оплатили {TOTAL_PAID} из {TOTAL_PLAN} должников ({PCT}%)",
        "Har bir to'lov — yopilgan qarz!",
        f"🔥 Лучший: {best['short']} — {best['paid']}/{best['plan']} ({best['pct']}%)",
        f"🏆 Впереди TEAM {lead}",
        f"👥 Ещё должны: {TOTAL_DEBT}",
        f"📦 Собрано {mln(TOTAL_SOB)} млн из {mlrd(TOTAL_PLANSUM)} млрд"]
    tki="".join(f'<span class="tk-i">{esc(x)}</span><i class="tk-sep">//</i>' for x in tk)
    ticker=f'<div class="ticker"><span class="tk-lab">Live //</span><div class="tk-view"><div class="tk-inner">{tki}</div><div class="tk-inner" aria-hidden="true">{tki}</div></div></div>'

    def row_html(r):
        posc=f"p{r['pos']}" if r['pos']<=3 else ""; tpc=f"tp{r['pos']}" if r['pos']<=3 else ""
        pace=r.get('pace_pct',0)
        fill="goldf" if r['pct']>=100 else ("okf" if r['pct']>=40 else "lagf")
        gap="goldg" if pace>=100 else ("okg" if pace>=80 else "badg")
        badge=f'<span class="tbadge t{r["team"]}">{r["team"]}</span>'
        # grafik: bugungacha muddati kelganlar (due) — oq marker; otryv = fakt - due
        due=r.get('due',0); plan=max(1,r['plan'])
        pacepct=min(100, round(due/plan*100))
        marker=f'<span style="position:absolute;left:{pacepct}%;top:-2px;bottom:-2px;width:3px;background:#fff;box-shadow:0 0 4px rgba(0,0,0,.55);z-index:3"></span>' if due>0 else ""
        gapn=r['paid']-due
        if gapn>0: chip=f'<span style="display:inline-block;padding:4px 10px;font:800 .95em \'Barlow Condensed\';background:rgba(61,255,162,.16);color:var(--greentx);clip-path:polygon(7px 0,100% 0,calc(100% - 7px) 100%,0 100%)">+{gapn}</span>'
        elif gapn<0: chip=f'<span style="display:inline-block;padding:4px 10px;font:800 .95em \'Barlow Condensed\';background:rgba(255,79,40,.16);color:var(--redtx);clip-path:polygon(7px 0,100% 0,calc(100% - 7px) 100%,0 100%)">−{-gapn}</span>'
        else: chip=f'<span style="display:inline-block;padding:4px 10px;font:800 .95em \'Barlow Condensed\';color:var(--mut)">0</span>'
        return f"""<div class="lrow {tpc}">
    <span class="pos {posc}"><i>{r['pos']}</i></span>
    <span class="lnm">{badge}{esc(r['short'])}</span>
    <span class="track"><span class="fill {fill}" style="--w:{min(100,r['pct'])}%"></span>{marker}<span class="tfin"></span></span>
    <span class="fact"><b>{r['paid']}</b><i>/{r['plan']} · {mln(r['sob'])}м</i></span>
    <span class="gap {gap}">{pace}%</span><span class="wk">{chip}</span></div>"""
    boards=f"""<div class="panel lbcard"><div class="ph"><span class="ptitle">Рейтинг кураторов <i class="sl">//</i> по выполнению графика</span><span class="lbleg">% = факт ÷ график к сегодня · белая метка = график · отрыв = факт − график</span></div>
  <div class="lhead"><span>Поз</span><span>Куратор</span><span>Трасса к плану</span><span>Факт/план·собр</span><span>% граф.</span><span>Отрыв</span></div>
  {"".join(row_html(r) for r in ALL)}
  <div class="ltot">Всего: оплатили <b>{TOTAL_PAID}</b> из <b>{TOTAL_PLAN}</b> · <b>{PCT}%</b> · по графику к {today.strftime('%d.%m')} должно быть <b>{due_total}</b> · отрыв <b>{TOTAL_PAID-due_total:+d}</b> · собрано <b>{mln(TOTAL_SOB)} млн</b></div></div>"""

    cashier_html = cashier_section(rows)
    week_html = week_section(weeks_agg, today)
    deadline_html = ""   # alohida blok kerak emas — grafik/otryv kurator qatorlarida

    pstate=json.dumps({"v":"sheet1","upd":UPD,"paid":TOTAL_PAID,"debt":TOTAL_DEBT,"sob":TOTAL_SOB,"pct":PCT},ensure_ascii=False)
    JS=open_js(SRV_MS)

    # --- ko'p sahifali: har sahifa o'z nav-tab bilan ---
    PAGES=[("index.html","Кураторы"),("weeks.html","Недели"),("cashiers.html","Кассиры")]
    def nav(active):
        links="".join(f'<a href="{fn}" class="{"on" if fn==active else ""}">{ttl}</a>' for fn,ttl in PAGES)
        return f'<nav class="rnav">{links}</nav>'
    def page(active, body, subtitle):
        return f"""<!DOCTYPE html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="refresh" content="3600"><title>Junior · {subtitle} · Долги</title>
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin><link href="https://fonts.googleapis.com/css2?family=Barlow:wght@600;700;800&family=Barlow+Condensed:ital,wght@0,600;0,700;0,800;1,700;1,800&family=Manrope:wght@500;700;800&display=swap" rel="stylesheet">
{CSS}
<style>.tbadge{{display:inline-flex;align-items:center;justify-content:center;width:1.15em;height:1.15em;border-radius:5px;font:800 .62em/1 Barlow,sans-serif;margin-right:.5em;vertical-align:middle;color:#12131a}}.tbadge.tA{{background:#ffd21e}}.tbadge.tB{{background:#b9c2d0}}
.sbwrap{{padding:14px 26px 22px;display:flex;flex-direction:column;gap:12px}}.sbrow{{display:flex;align-items:center;gap:12px}}.sbl{{width:120px;font:800 .82em/1 Manrope;color:var(--mut);text-transform:uppercase;letter-spacing:.03em}}.sbtrack{{flex:1;height:22px;background:var(--trackbg);border-radius:6px;overflow:hidden;display:block}}.sbfill{{display:block;height:100%;min-width:3px;border-radius:6px}}.sbn{{width:54px;text-align:right;font:800 1.1em/1 'Barlow Condensed';color:var(--txt)}}</style>
</head><body>
<script>try{{var _t=localStorage.getItem('jTheme')||'light';if(_t!=='dark')document.body.classList.add('light');}}catch(e){{document.body.classList.add('light');}}</script>
<script id="pstate" type="application/json">{pstate}</script>
<header><div class="brand"><span class="b-volt">⚡ Долги</span><span class="b-dark">{subtitle}</span><span class="upd">обновлено {UPD} · ⟳ live</span></div>
{nav(active)}
<div class="mswitch"><button class="mbtn on">Цикл {CYCLE_LABEL} · live</button></div>
<button class="tbtn" id="tbtn" title="Тема">☀️</button>
<span class="clkwrap"><span class="clk" id="clk">--:--</span><span class="cdate" id="cdate"></span></span></header>
{body}
{JS}
</body></html>"""
    FILES={
      "index.html":    page("index.html",    f"{hero}\n{deadline_html}\n{champ}\n{ticker}\n{boards}", "Кураторы"),
      "weeks.html":    page("weeks.html",    f"{champ}\n{week_html}",                "Недели"),
      "cashiers.html": page("cashiers.html", f"{champ}\n{cashier_html}",             "Кассиры"),
    }
    for fn,html in FILES.items():
        local = fn if (IS_CI or fn!="index.html") else "kuratorlar.html"
        with open(os.path.join(BASE,local),"w",encoding="utf-8") as f: f.write(html)
    print(f"[{datetime.datetime.now():%Y-%m-%d %H:%M}] OK{' [PREVIEW]' if PREVIEW else ''} -> оплатили {TOTAL_PAID}/{TOTAL_PLAN}={PCT}% · должны {TOTAL_DEBT} · собрано {mln(TOTAL_SOB)}млн")
    for r in ALL: print(f"  {r['pos']:2} {r['short']:9} {r['team']} {r['paid']:3}/{r['plan']:3}={r['pct']:3}% qarzdor {r['debt']:3} собр {mln(r['sob'])}м")
    if IS_CI:
        print("  CI: fayllar yozildi — commit/push ni workflow bajaradi"); return
    if PREVIEW:
        try: deploy_github(os.path.join(BASE,"kuratorlar.html"), None, path="preview.html")
        except Exception as e: print(f"  github: ERROR {e}", file=sys.stderr)
        return
    # sig: o'zgarmagan bo'lsa 3 sahifani ham o'tkazib yuboramiz
    sig=json.dumps({"p":[(r['short'],r['paid'],r['debt'],r['sob'],r['plan']) for r in ALL]},ensure_ascii=False,sort_keys=True)
    if os.path.isfile(SIG_FILE) and open(SIG_FILE,encoding="utf-8").read().strip()==sig:
        print("  github: ma'lumot o'zgarmadi — deploy o'tkazib yuborildi"); return
    try:
        deploy_github(os.path.join(BASE,"kuratorlar.html"), None, path="index.html")
        deploy_github(os.path.join(BASE,"weeks.html"),      None, path="weeks.html")
        deploy_github(os.path.join(BASE,"cashiers.html"),   None, path="cashiers.html")
        with open(SIG_FILE,"w",encoding="utf-8") as f: f.write(sig)
    except Exception as e: print(f"  github: ERROR {e}", file=sys.stderr)

def cashier_section(rows):
    by={r['short']:r for r in rows}
    data=[]
    for cname,team,curs in CASHIERS:
        cs=[by[c] for c in curs if c in by]
        paid=sum(x['paid'] for x in cs); plan=sum(x['plan'] for x in cs); sob=sum(x['sob'] for x in cs)
        data.append(dict(name=cname,team=team,curs=curs,paid=paid,plan=plan,sob=sob,pct=round(paid/plan*100) if plan else 0))
    data.sort(key=lambda x:(-x['pct'],-x['paid']))
    for i,d in enumerate(data,1): d['pos']=i
    tp=sum(d['paid'] for d in data); tpl=sum(d['plan'] for d in data); tsob=sum(d['sob'] for d in data)
    def crow(d):
        posc=f"p{d['pos']}" if d['pos']<=3 else ""; tpc=f"tp{d['pos']}" if d['pos']<=3 else ""
        fill="goldf" if d['pct']>=100 else ("okf" if d['pct']>=40 else "lagf")
        gap="goldg" if d['pct']>=100 else ("okg" if d['pct']>=40 else "badg")
        badge=f'<span class="tbadge t{d["team"]}">{d["team"]}</span>'
        sub=", ".join(d['curs'])
        return f"""<div class="lrow {tpc}">
    <span class="pos {posc}"><i>{d['pos']}</i></span>
    <span class="lnm">{badge}{esc(d['name'])} <i style="color:var(--mut);font-weight:600;font-size:.78em">· {esc(sub)}</i></span>
    <span class="track"><span class="fill {fill}" style="--w:{min(100,d['pct'])}%"></span><span class="tfin"></span></span>
    <span class="fact"><b>{d['paid']}</b><i>/{d['plan']} чел</i></span>
    <span class="gap {gap}">{d['pct']}%</span><span class="wk">{mln(d['sob'])}м</span></div>"""
    return f"""<div class="panel lbcard"><div class="ph"><span class="ptitle">Кассиры <i class="sl">//</i> сбор по кассирам</span><span class="lbleg">оплатили / должников · % · собрано</span></div>
  <div class="lhead"><span>Поз</span><span>Кассир · кураторы</span><span>Прогресс</span><span>Оплат./долж</span><span>%</span><span>Собр.</span></div>
  {"".join(crow(d) for d in data)}
  <div class="ltot">Всего по кассирам: оплатили <b>{tp}</b> из <b>{tpl}</b> · собрано <b>{mln(tsob)} млн</b></div></div>"""

def status_bars(tol,bit,sar,muz,arx,debt):
    tot=max(1,tol+bit+sar+debt)
    items=[("To'lagan",tol,"#3dffa2"),("Bitirdi",bit,"#39b0ff"),("Sarafan",sar,"#b26bff"),
           ("Muzlagan",muz,"#ffb020"),("Arxiv",arx,"#8a94a6"),("Ещё должны",debt,"#ff4f28")]
    rows=""
    for lab,v,col in items:
        rows+=f'<div class="sbrow"><span class="sbl">{lab}</span><span class="sbtrack"><span class="sbfill" style="width:{round(v/tot*100)}%;background:{col}"></span></span><span class="sbn">{v}</span></div>'
    return f'<div class="sbwrap">{rows}</div>'

def open_js(srv_ms):
    return ("""
<script>
(function(){var QS=new URLSearchParams(location.search);
 var SRV=__SRVMS__, P0=(window.performance&&performance.now)?performance.now():0;
 function nowd(){return new Date(SRV+((window.performance&&performance.now)?(performance.now()-P0):0));}
 var WD=['вс','пн','вт','ср','чт','пт','сб'];
 var MO=['января','февраля','марта','апреля','мая','июня','июля','августа','сентября','октября','ноября','декабря'];
 function stateNow(){var e=document.getElementById('pstate');if(!e)return null;try{return JSON.parse(e.textContent);}catch(x){return null;}}
 function initPage(){
  function clk(){var e=document.getElementById('clk');if(e){var d=nowd();e.textContent=('0'+d.getUTCHours()).slice(-2)+':'+('0'+d.getUTCMinutes()).slice(-2);}}
  clk();setInterval(clk,10000);
  var cd=document.getElementById('cdate');if(cd){var d=nowd();cd.textContent=WD[d.getUTCDay()]+', '+d.getUTCDate()+' '+MO[d.getUTCMonth()];}
  var tb=document.getElementById('tbtn');function ticon(){if(tb)tb.textContent=document.body.classList.contains('light')?'🌙':'☀️';}ticon();
  if(tb)tb.onclick=function(){document.body.classList.toggle('light');try{localStorage.setItem('jTheme',document.body.classList.contains('light')?'light':'dark');}catch(e){}ticon();};
  document.querySelectorAll('[data-cnt]').forEach(function(el){var v=+el.getAttribute('data-cnt'),suf=el.getAttribute('data-suf')||'',t0=null;
   function st(ts){if(!t0)t0=ts;var p=Math.min(1,(ts-t0)/1200),e2=1-Math.pow(1-p,3);el.textContent=Math.round(v*e2)+suf;if(p<1)requestAnimationFrame(st);}requestAnimationFrame(st);});
  requestAnimationFrame(function(){requestAnimationFrame(function(){document.body.classList.add('go');});});
 }initPage();
 var lastOk=Date.now();
 function checkUpdate(){fetch(location.pathname+'?ts='+Date.now(),{cache:'no-store'}).then(function(r){if(!r.ok)throw 0;return r.text();}).then(function(html){lastOk=Date.now();var doc=new DOMParser().parseFromString(html,'text/html');var ne=doc.getElementById('pstate');if(!ne)return;var S;try{S=JSON.parse(ne.textContent);}catch(x){return;}var C=stateNow();if(!C)return;if(JSON.stringify(S)!==JSON.stringify(C))location.reload();}).catch(function(x){if(Date.now()-lastOk>3*3600*1000)location.reload();});}
 setInterval(checkUpdate,Math.max(60,+(QS.get('refresh')||240))*1000);
}());
</script>""".replace("__SRVMS__", str(srv_ms)))

if __name__=="__main__":
    try: main()
    except Exception as e:
        print(f"[{datetime.datetime.now():%Y-%m-%d %H:%M}] ERROR: {e}", file=sys.stderr); sys.exit(1)
