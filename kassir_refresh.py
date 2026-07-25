#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import json, urllib.request, datetime, html, os, calendar

GATEWAY = "https://myclinic.agc.uz/new_junior_mcp.php"
ORG = 6
HERE = os.path.dirname(os.path.abspath(__file__))

def q(sql):
    body = json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "query_db", "arguments": {"sql": sql}}
    }).encode()
    
    req = urllib.request.Request(
        GATEWAY, 
        data=body, 
        headers={
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
        }
    )
    
    try:
        raw = urllib.request.urlopen(req, timeout=60).read().decode()
        txt = json.loads(raw)["result"]["content"][0]["text"]
        d = json.loads(txt).get("data", {}).get("data", [])
        if isinstance(d, dict) and d.get("stat") == "error":
            print(f"SQL error details: {d}")
            return []
        return d if isinstance(d, list) else []
    except Exception as e:
        print(f"Error querying DB: {e}")
        return []

# ---- vaqt ----
TODAY = datetime.date.today()
DOM = TODAY.day
NOW_TS = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def day_of(off): 
    return (TODAY + datetime.timedelta(days=off)).day

D_T3, D_T2, D_T1, D_T0 = day_of(3), day_of(2), day_of(1), DOM

# ---- kassirlar (Умягчили фильтр ролей) ----
cash_rows = q("SELECT ID, NAME, SURNAME FROM gl_sys_users WHERE STATUS=1 AND (ROLE_ID=20 OR ROLE_ID IS NOT NULL)")
CASH = {}
for c in cash_rows:
    cid = str(c.get("ID"))
    nm = (html.unescape(c.get("NAME") or "").strip() + " " + html.unescape(c.get("SURNAME") or "").strip()).strip()
    if nm:
        CASH[cid] = nm

PAY_SEL = ("s.ID sid, s.NAME nm, s.PHONE ph, sub.GROUP_ID gid, g.NAME grp, "
           "COALESCE(g.CASHIER_ID, 0) cid, sub.DAY chday, s.CURRENT_BALANCE bal, sub.SPECIAL_PRICE price")

# Мягкий выбор подписок без жесткой привязки к NOT LIKE 'Sinov'
PAY_FROM = (
    "FROM subscribe_list sub "
    "JOIN student_list s ON s.ID=sub.STUDENT_ID "
    "LEFT JOIN group_list g ON g.ID=sub.GROUP_ID "
    "WHERE sub.ORG_ID=%d AND sub.ACTIVE=1 " % ORG
)

def pay_list(day):
    res = q("SELECT %s %s AND s.CURRENT_BALANCE >= 0 AND s.CURRENT_BALANCE < COALESCE(sub.SPECIAL_PRICE, 0) "
            "AND sub.DAY=%d ORDER BY s.CURRENT_BALANCE ASC" % (PAY_SEL, PAY_FROM, day))
    return res

t3 = pay_list(D_T3)
t2 = pay_list(D_T2)
t1 = pay_list(D_T1)
t0 = pay_list(D_T0)

debtors = q("SELECT %s %s AND s.CURRENT_BALANCE < 0 ORDER BY sub.DAY DESC, s.CURRENT_BALANCE ASC"
            % (PAY_SEL, PAY_FROM))

frozen = q(
    "SELECT s.ID sid, s.NAME nm, s.PHONE ph, sub.GROUP_ID gid, g.NAME grp, COALESCE(g.CASHIER_ID, 0) cid, "
    "DATE(fs.START_DATE) fdate, fr.REASON reason "
    "FROM subscribe_list sub JOIN student_list s ON s.ID=sub.STUDENT_ID "
    "LEFT JOIN group_list g ON g.ID=sub.GROUP_ID "
    "LEFT JOIN frozen_student_list fs ON fs.ID=(SELECT MAX(f2.ID) FROM frozen_student_list f2 WHERE f2.STUDENT_ID=s.ID) "
    "LEFT JOIN frozen_reason fr ON fr.ID=fs.REASON_ID "
    "WHERE sub.ORG_ID=%d AND sub.ACTIVE=1 AND sub.STATUS='freezed' "
    "ORDER BY fs.START_DATE DESC" % ORG
)

def days_past(chday):
    chday = int(chday or 0)
    try:
        if chday <= DOM: 
            cd = TODAY.replace(day=min(chday, calendar.monthrange(TODAY.year, TODAY.month)[1]))
        else:
            prev_last = TODAY.replace(day=1) - datetime.timedelta(days=1)
            _, max_prev = calendar.monthrange(prev_last.year, prev_last.month)
            cd = prev_last.replace(day=min(chday, max_prev))
    except Exception:
        cd = TODAY
    return (TODAY - cd).days

def nf(n):
    n = int(n or 0)
    return ("−" if n < 0 else "") + "{:,}".format(abs(n)).replace(",", " ")

def esc(s): return html.escape("" if s is None else str(s)).strip()
def tel(ph): return "".join(ch for ch in str(ph or "") if ch.isdigit() or ch == "+")
REASON_RU = {"auto_overdue": "Просрочка оплаты", "auto_overdue_lead": "Просрочка · лид"}
MONTHS = ["", "янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]

def dm(iso):
    if not iso: return ""
    try:
        d = datetime.date.fromisoformat(str(iso)[:10])
        return "%d %s" % (d.day, MONTHS[d.month])
    except Exception:
        return str(iso)[:10]

def cid_of(r):
    c = str(r.get("cid") or "0")
    return c if c in CASH else "all"

CRM = "https://crm.junior-it.uz/account"
def stu_url(sid): return "%s/student_list/detail/%s" % (CRM, sid)
def grp_url(gid): return "%s/group_list/detail/%s" % (CRM, gid) if gid and str(gid) != "0" else None

def task_row(r, kind):
    sid = esc(r.get("sid")); nm = esc(r.get("nm")); grp = esc(r.get("grp") or "—"); phd = tel(r.get("ph"))
    gu = grp_url(r.get("gid"))
    name_html = ('<a class="tnm" href="%s" target="_blank" rel="noopener">%s</a>' % (stu_url(sid), nm))
    grp_html = (('<a class="grp" href="%s" target="_blank" rel="noopener">%s</a>' % (gu, grp)) if gu else ('<span class="grp">%s</span>' % grp))
    
    if kind in ("t3", "t2", "t1", "t0"):
        off = 0 if kind == "t0" else (1 if kind == "t1" else (2 if kind == "t2" else 3))
        mon = (TODAY + datetime.timedelta(days=off)).month
        chdate = "%02d.%02d" % (int(r.get("chday") or 0), mon)
        metric = ('<span class="m m-warn">баланс %s сум</span>'
                  '<span class="m m-dim">списание %s</span>' % (nf(r.get("bal")), chdate))
        key = "%s_%s" % (kind, sid)
    elif kind == "debtor":
        dp = days_past(r.get("chday"))
        fresh = "сегодня" if dp == 0 else ("вчера" if dp == 1 else "%d дн. назад" % dp)
        metric = ('<span class="m m-debt">долг %s сум</span>'
                  '<span class="m m-dim">списание %s</span>' % (nf(-int(r.get("bal") or 0)), fresh))
        key = "debtor_%s" % sid
    else:
        reason = REASON_RU.get(r.get("reason"), esc(r.get("reason") or "—"))
        metric = ('<span class="m m-froz">%s</span>' % reason)
        key = "frozen_%s" % sid

    return ('<div class="trow" data-k="%s"><span class="dot d-%s"></span>'
            '<div class="tmain">%s<div class="tmeta">%s</div></div>'
            '<div class="tright">%s</div>'
            '<a class="call" href="tel:%s">Позвонить</a>'
            '<button class="done">✓</button></div>' % (key, kind, name_html, grp_html, metric, phd))

SECDEF = [
    ("t3", "💳", "3 дня до оплаты", "b-t3", t3),
    ("t2", "💳", "2 дня до оплаты", "b-t2", t2),
    ("t1", "💳", "1 день до оплаты", "b-t1", t1),
    ("t0", "⏰", "Сегодня день оплаты", "b-t0", t0),
    ("debtor", "📋", "Стал дебитором", "b-debtor", debtors),
    ("frozen", "🧊", "Заморожен", "b-frozen", frozen),
]

def render_board(cash_id):
    total = 0; sec_html = []
    for key, ic, title, bcls, rows in SECDEF:
        rr = rows if cash_id == "all" else [r for r in rows if str(r.get("cid")) == str(cash_id)]
        total += len(rr)
        body = "".join(task_row(r, key) for r in rr) or '<div class="empty">Задач нет</div>'
        sec_html.append(
            '<section class="panel sec" data-sec="%s">'
            '<div class="banner %s"><span class="bi">%s</span><span class="bt">%s</span>'
            '<span class="bc">%d</span></div><div class="list">%s</div></section>' % (key, bcls, ic, title, len(rr), body))
            
    who = "Все кассиры" if cash_id == "all" else esc(CASH.get(cash_id, "Кассир #" + cash_id))
    board = (
        '<div class="board" data-cash="%s" hidden>'
        '<div class="topbar"><button class="back">← Сменить</button>'
        '<div class="who">%s</div>'
        '<div class="pbar"><div class="pfill"></div></div>'
        '<span class="pnum">0 / %d</span></div>%s</div>' % (cash_id, who, total, "".join(sec_html)))
    return board, total

boards = []; picks = []
for cid, nm in CASH.items():
    b, tot = render_board(cid)
    boards.append(b)
    ini = "".join(w[0] for w in nm.split()[:2]).upper() if nm else "?"
    picks.append(
        '<button class="pcard" data-cash="%s"><span class="ava">%s</span>'
        '<span class="pinfo"><span class="pnm">%s</span></span>'
        '<span class="pcnt" id="cnt-%s">%d</span></button>' % (cid, esc(ini), esc(nm), cid, tot))

allb, alltot = render_board("all")
boards.append(allb)
picks.append('<button class="pcard pall" data-cash="all"><span class="ava avall">Σ</span>'
             '<span class="pinfo"><span class="pnm">Все кассиры</span></span>'
             '<span class="pcnt" id="cnt-all">%d</span></button>' % alltot)

STYLE = """
:root{--bg:#f1efe9;--panel:#fff;--panel2:#f4f2ec;--line:#d9dee6;--txt:#10151d;--mut:#59626f;--dim:#7c8695;--volt:#ff4f28;--volttx:#e63912;--yellow:#a16207;--orange:#c2410c;--red:#be123c;--cyan:#0e7490;--green:#047857}
*{box-sizing:border-box}body{margin:0;color:var(--txt);font:15px/1.5 sans-serif;padding:18px 20px 80px;background:var(--bg)}
.wrap{max-width:960px;margin:0 auto}header{display:flex;align-items:baseline;gap:12px;margin-bottom:14px}
h1{font-size:24px;margin:0}.pick{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:18px;margin-bottom:16px}
.pgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:10px}
.pcard{display:flex;align-items:center;gap:10px;padding:12px;background:var(--panel2);border:1px solid var(--line);border-radius:10px;cursor:pointer}
.ava{width:36px;height:36px;border-radius:50%;background:var(--volt);color:#fff;display:flex;align-items:center;justify-content:center;font-weight:bold}
.avall{background:#59626f}.pinfo{flex:1}.pnm{font-weight:bold;display:block}.pcnt{font-size:20px;font-weight:bold;color:var(--volttx)}
.topbar{display:flex;align-items:center;gap:12px;margin-bottom:12px}.back{padding:6px 12px;cursor:pointer}
.who{font-size:18px;font-weight:bold}.pbar{flex:1;height:10px;background:var(--panel2);border-radius:5px;overflow:hidden}.pfill{height:100%;width:0;background:var(--volt)}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:10px;margin-bottom:12px;overflow:hidden}
.banner{display:flex;align-items:center;gap:10px;padding:10px 14px;font-weight:bold}
.b-t3,.b-t2,.b-t1,.b-t0{background:#fef3c7;color:#92400e}.b-debtor{background:#ffe4e6;color:#9f1239}.b-frozen{background:#e0f2fe;color:#075985}
.trow{display:flex;align-items:center;gap:10px;padding:8px 14px;border-top:1px solid var(--line)}
.dot{width:8px;height:8px;border-radius:50%}.d-t3,.d-t2,.d-t1,.d-t0{background:#f59e0b}.d-debtor{background:#e11d48}.d-frozen{background:#06b6d4}
.tmain{flex:1}.tnm{font-weight:bold;color:var(--txt);text-decoration:none}.grp{font-size:12px;color:var(--mut)}
.tright{text-align:right}.call{padding:4px 8px;border:1px solid var(--line);border-radius:6px;text-decoration:none;color:var(--txt);font-size:12px}
.done{width:28px;height:28px;border:1px solid var(--line);border-radius:6px;background:var(--panel2);color:var(--green);cursor:pointer}
.empty{padding:12px;color:var(--mut);font-size:13px}
"""

JS = """
(function(){
  var DKEY='kassir_done_'+DATE;
  var done={};try{done=JSON.parse(localStorage.getItem(DKEY)||'{}')}catch(e){}
  function save(){try{localStorage.setItem(DKEY,JSON.stringify(done))}catch(e){}}
  
  function show(cash){
    document.getElementById('pick').style.display='none';
    document.querySelectorAll('.board').forEach(function(b){b.hidden=(b.dataset.cash!==cash)});
    upd();
  }
  function back(){
    document.getElementById('pick').style.display='';
    document.querySelectorAll('.board').forEach(function(b){b.hidden=true});
    upd();
  }
  document.querySelectorAll('.pcard').forEach(function(c){c.onclick=function(){show(c.dataset.cash)}});
  document.querySelectorAll('.back').forEach(function(b){b.onclick=back});
  document.addEventListener('click',function(e){
    var d=e.target.closest('.done');if(!d)return;
    var row=d.closest('.trow');done[row.dataset.k]=1;save();upd();
  });
  function upd(){
    document.querySelectorAll('.board').forEach(function(b){
      var rows=b.querySelectorAll('.trow'), tot=rows.length, cl=0;
      rows.forEach(function(r){
        if(done[r.dataset.k]){r.style.display='none';cl++;}else{r.style.display='';}
      });
      var pfill=b.querySelector('.pfill'), pnum=b.querySelector('.pnum');
      if(pfill)pfill.style.width=(tot?Math.round(cl/tot*100):0)+'%';
      if(pnum)pnum.textContent=cl+' / '+tot;
    });
  }
})();
"""

HTML = u"""<!doctype html><html lang="ru"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Кассиры · задачи</title><style>%s</style></head><body>
<div class="wrap">
<header><h1>Кассиры · задачи</h1><span class="meta">%s</span></header>
<div class="pick" id="pick"><h2>Выберите кассира</h2><div class="pgrid">%s</div></div>
%s
</div>
<script>var DATE="%s";%s</script>
</body></html>""" % (STYLE, esc(NOW_TS), "".join(picks), "".join(boards), TODAY.isoformat(), JS)

if os.environ.get("GITHUB_ACTIONS") == "true":
    OUT = os.path.join(os.getcwd(), "kassir-vazifalar.html")
else:
    OUT = os.path.join(HERE, "index.html")

with open(OUT, "w", encoding="utf-8") as f:
    f.write(HTML)

print("OK ->", OUT)
