#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Kassirlar · kunlik avto-vazifalar dashboard generatori.
Ma'lumot: junior bazasi (ORG 6), MCP HTTP gateway orqali.
Har bir guruhda mas'ul kassir = group_list.CASHIER_ID. Har bir kassir o'zini
tanlaydi va faqat o'z guruhlaridagi vazifalarni ko'radi (kuratorlar paneli kabi).
3 trigger:
  1) To'lov sanasidan 3 kun oldin
  2) Debitor bo'lgan kundan muzlatilguncha har kuni
  3) Muzlatilgandan arxivga o'tguncha har kuni
Ishga tushirish: python3 refresh.py  ->  index.html hosil bo'ladi.
"""
import json, urllib.request, datetime, html, os

GATEWAY = "https://myclinic.agc.uz/new_junior_mcp.php"
ORG = 6
HERE = os.path.dirname(os.path.abspath(__file__))

def q(sql):
    body = json.dumps({"jsonrpc":"2.0","id":1,"method":"tools/call",
        "params":{"name":"query_db","arguments":{"sql":sql}}}).encode()
    req = urllib.request.Request(GATEWAY, data=body,
        headers={"Content-Type":"application/json"})
    raw = urllib.request.urlopen(req, timeout=60).read().decode()
    txt = json.loads(raw)["result"]["content"][0]["text"]
    d = json.loads(txt).get("data", {}).get("data", [])
    if isinstance(d, dict) and d.get("stat") == "error":
        raise RuntimeError("SQL error: " + d.get("error","") + "\n" + sql)
    return d

# ---- vaqt: bazadan ----
row = q("SELECT CURDATE() d, DAY(CURDATE()) dom, NOW() n")[0]
TODAY = datetime.date.fromisoformat(row["d"])
DOM = int(row["dom"]); NOW_TS = row["n"]
def day_of(off): return (TODAY + datetime.timedelta(days=off)).day
D_T3 = day_of(3)

# ---- kassirlar ----
cash_rows = q("SELECT ID, NAME, SURNAME FROM gl_sys_users WHERE ROLE_ID=20 AND STATUS=1")
CASH = {str(c["ID"]): (html.unescape(c["NAME"]).strip() + " " +
                       html.unescape(c.get("SURNAME") or "").strip()).strip()
        for c in cash_rows}
# Kassir tanlash ro'yxatida ko'rsatilmaydigan xodimlar. Ularning vazifalari
# nazorat yo'qolmasligi uchun "Все кассиры" umumiy doskasida qoladi.
EXCLUDED_CASHIERS = {"15024"}  # Shahzoda Isakulova

MONTH_START = "DATE_FORMAT(CURDATE(),'%Y-%m-01')"
PREV_MONTH_START = "DATE_FORMAT(CURDATE()-INTERVAL 1 MONTH,'%Y-%m-01')"
DUE_DATE = ("CASE WHEN sub.DAY<=DAY(CURDATE()) THEN "
            "DATE_ADD(%s,INTERVAL LEAST(sub.DAY,DAY(LAST_DAY(%s)))-1 DAY) ELSE "
            "DATE_ADD(%s,INTERVAL LEAST(sub.DAY,DAY(LAST_DAY(%s)))-1 DAY) END"
            % (MONTH_START, MONTH_START, PREV_MONTH_START, PREV_MONTH_START))
TARIFF_MONTHS = ("COALESCE(tf.tariff_months,CASE "
                 "WHEN LOWER(COALESCE(sub.PRICE_TYPE,'')) REGEXP 'upsell[[:space:]]*12|12[[:space:]]*oylik' THEN 12 "
                 "WHEN LOWER(COALESCE(sub.PRICE_TYPE,'')) REGEXP 'upsell[[:space:]]*6|6[[:space:]]*oylik' THEN 6 "
                 "WHEN LOWER(COALESCE(sub.PRICE_TYPE,'')) REGEXP 'upsell[[:space:]]*3|3[[:space:]]*oylik' THEN 3 "
                 "ELSE 0 END)")

PAY_SEL = ("s.ID sid, s.NAME nm, s.PHONE ph, sub.ID subid, sub.GROUP_ID gid, g.NAME grp, "
           "g.CASHIER_ID cid, sub.DAY chday, s.CURRENT_BALANCE bal, sub.SPECIAL_PRICE price, "
           "DATE(sub.END_OF_SUBSCRIPTION) expiry, " + TARIFF_MONTHS + " tariff_months, "
           "tf.tariff_name, (%s) due_date, "
           "COALESCE((SELECT SUM(tx.AMOUNT) FROM transaction_list tx WHERE tx.STUDENT_ID=s.ID "
           "AND tx.ACTION_TYPE='taken' AND tx.TRANSACTION_DATE>(%s)-INTERVAL 1 MONTH),0) charged_sum, "
           "nl.ID note_id, DATE_FORMAT(nl.CREATED_DATE,'%%H:%%i') note_time, "
           "nu.NAME note_author_name, nu.SURNAME note_author_surname")
PAY_SEL = PAY_SEL % (DUE_DATE, DUE_DATE)
PAY_FROM = ("FROM subscribe_list sub JOIN student_list s ON s.ID=sub.STUDENT_ID "
            "LEFT JOIN group_list g ON g.ID=sub.GROUP_ID "
            "LEFT JOIN (SELECT NAME_VALUE, AMOUNT, MAX(MONTH_AMOUNT) tariff_months, "
            "MAX(NAME) tariff_name FROM tariffs_list WHERE TYPE='tariff' "
            "GROUP BY NAME_VALUE, AMOUNT) tf "
            "ON tf.NAME_VALUE=sub.PRICE_TYPE AND tf.AMOUNT=sub.SPECIAL_PRICE "
            "LEFT JOIN note_list nl ON nl.ID=(SELECT MAX(n2.ID) FROM note_list n2 "
            " WHERE n2.STUDENT_ID=s.ID AND n2.ACTIVE=1 AND n2.CREATED_BY=g.CASHIER_ID "
            " AND n2.CREATED_DATE>=CURDATE() AND n2.CREATED_DATE<CURDATE()+INTERVAL 1 DAY) "
            "LEFT JOIN gl_sys_users nu ON nu.ID=nl.CREATED_BY "
            "WHERE sub.ORG_ID=%d AND sub.ACTIVE=1 AND sub.TYPE='monthly' "
            "AND sub.STATUS='active'" % ORG)

def pay_list(day, days_left):
    return q(
        "SELECT %s %s AND ("
        "((%s)<=1 "
        " AND s.CURRENT_BALANCE>=0 AND s.CURRENT_BALANCE < sub.SPECIAL_PRICE AND sub.DAY=%d)"
        " OR "
        "((%s)>1 "
        " AND DATEDIFF(DATE(sub.END_OF_SUBSCRIPTION),CURDATE())=%d)"
        ") ORDER BY (%s) DESC, s.CURRENT_BALANCE ASC"
        % (PAY_SEL, PAY_FROM, TARIFF_MONTHS, day, TARIFF_MONTHS, days_left, TARIFF_MONTHS))

t3 = pay_list(D_T3, 3)
debtors = q("SELECT %s %s AND (%s)<=1 "
            "AND sub.SPECIAL_PRICE>0 AND (%s)<=CURDATE() "
            "AND EXISTS (SELECT 1 FROM transaction_list oldtx "
            "WHERE oldtx.STUDENT_ID=s.ID AND oldtx.ACTION_TYPE='taken' "
            "AND oldtx.TRANSACTION_DATE<(%s)) "
            "AND (s.CURRENT_BALANCE<0 OR (s.CURRENT_BALANCE>=sub.SPECIAL_PRICE "
            "AND NOT EXISTS (SELECT 1 FROM transaction_list tx "
            "WHERE tx.STUDENT_ID=s.ID AND tx.ACTION_TYPE='taken' "
            "AND tx.TRANSACTION_DATE>(%s)-INTERVAL 1 MONTH))) "
            "ORDER BY (%s) DESC, s.CURRENT_BALANCE ASC"
            % (PAY_SEL, PAY_FROM, TARIFF_MONTHS, DUE_DATE, DUE_DATE, DUE_DATE, DUE_DATE))
frozen = q(
    "SELECT s.ID sid, s.NAME nm, s.PHONE ph, sub.GROUP_ID gid, g.NAME grp, g.CASHIER_ID cid, "
    "DATE(fs.START_DATE) fdate, fr.REASON reason, DATE(sub.END_OF_SUBSCRIPTION) expiry, "
    "%s tariff_months, tf.tariff_name, nl.ID note_id, "
    "DATE_FORMAT(nl.CREATED_DATE,'%%H:%%i') note_time, nu.NAME note_author_name, "
    "nu.SURNAME note_author_surname "
    "FROM subscribe_list sub JOIN student_list s ON s.ID=sub.STUDENT_ID "
    "LEFT JOIN group_list g ON g.ID=sub.GROUP_ID "
    "LEFT JOIN (SELECT NAME_VALUE, AMOUNT, MAX(MONTH_AMOUNT) tariff_months, "
    "MAX(NAME) tariff_name FROM tariffs_list WHERE TYPE='tariff' "
    "GROUP BY NAME_VALUE, AMOUNT) tf "
    "ON tf.NAME_VALUE=sub.PRICE_TYPE AND tf.AMOUNT=sub.SPECIAL_PRICE "
    "LEFT JOIN note_list nl ON nl.ID=(SELECT MAX(n2.ID) FROM note_list n2 "
    " WHERE n2.STUDENT_ID=s.ID AND n2.ACTIVE=1 AND n2.CREATED_BY=g.CASHIER_ID "
    " AND n2.CREATED_DATE>=CURDATE() AND n2.CREATED_DATE<CURDATE()+INTERVAL 1 DAY) "
    "LEFT JOIN gl_sys_users nu ON nu.ID=nl.CREATED_BY "
    "LEFT JOIN frozen_student_list fs ON fs.ID=(SELECT MAX(f2.ID) FROM frozen_student_list f2 WHERE f2.STUDENT_ID=s.ID) "
    "LEFT JOIN frozen_reason fr ON fr.ID=fs.REASON_ID "
    "WHERE sub.ORG_ID=%d AND sub.ACTIVE=1 AND sub.TYPE='monthly' AND sub.STATUS='freezed' "
    "AND NOT EXISTS (SELECT 1 FROM trial_lesson_list tl "
    " WHERE tl.STUDENT_ID=s.ID AND tl.ACTIVE=1 AND tl.STATUS='active' "
    " AND ABS(DATEDIFF(DATE(sub.START_DATE),tl.LESSON_DATE))<=7 "
    " AND NOT EXISTS (SELECT 1 FROM transaction_list pay "
    "  WHERE pay.STUDENT_ID=s.ID AND pay.ACTION_TYPE='add' "
    "  AND pay.TRANSACTION_DATE>=tl.LESSON_DATE)) "
    "ORDER BY fs.START_DATE DESC" % (TARIFF_MONTHS, ORG))

# ---- yordamchilar ----
def days_past(chday):
    chday = int(chday)
    if chday <= DOM: cd = TODAY.replace(day=chday)
    else:
        prev_last = TODAY.replace(day=1) - datetime.timedelta(days=1)
        try: cd = prev_last.replace(day=chday)
        except ValueError: cd = prev_last
    return (TODAY - cd).days

def nf(n):
    n = int(n or 0)
    return ("−" if n < 0 else "") + "{:,}".format(abs(n)).replace(",", " ")

def esc(s): return html.escape("" if s is None else str(s)).strip()
def tel(ph): return "".join(ch for ch in str(ph or "") if ch.isdigit() or ch == "+")
REASON_RU = {"auto_overdue":"Просрочка оплаты","auto_overdue_lead":"Просрочка · лид"}
MONTHS = ["","янв","фев","мар","апр","мая","июн","июл","авг","сен","окт","ноя","дек"]
def dm(iso):
    if not iso: return ""
    d = datetime.date.fromisoformat(str(iso)[:10]); return "%d %s" % (d.day, MONTHS[d.month])

def cid_of(r):
    c = str(r.get("cid") or "0")
    return c if c in CASH else "0"

CRM = "https://crm.junior-it.uz/account"
def stu_url(sid): return "%s/student_list/detail/%s" % (CRM, sid)
def grp_url(gid): return "%s/group_list/detail/%s" % (CRM, gid) if gid and str(gid) != "0" else None

def task_row(r, kind):
    sid = esc(r["sid"]); nm = esc(r["nm"]); grp = esc(r.get("grp") or "—"); phd = tel(r["ph"])
    gu = grp_url(r.get("gid"))
    name_html = ('<a class="tnm" href="%s" target="_blank" rel="noopener" title="Открыть карточку в CRM">%s</a>'
                 % (stu_url(sid), nm))
    grp_html = (('<a class="grp" href="%s" target="_blank" rel="noopener">%s</a>' % (gu, grp))
                if gu else ('<span class="grp">%s</span>' % grp))
    package_months = int(r.get("tariff_months") or 0)
    is_package = package_months > 1
    pay_kind = ("%d oylik paket" % package_months) if is_package else "Oylik to‘lov"
    note_ok = bool(r.get("note_id"))
    note_author = (html.unescape(r.get("note_author_name") or "").strip() + " " +
                   html.unescape(r.get("note_author_surname") or "").strip()).strip()
    cashier_name = CASH.get(cid_of(r), "—")
    if kind == "t3":
        off = 3
        if is_package:
            chdate = dm(r.get("expiry"))
            state = "bugun tugaydi" if off == 0 else "%d kun qoldi" % off
            detail = '<span class="taskbadge package">📦 %s · %s</span>' % (pay_kind, state)
            metric = ('<span class="m m-warn">%s</span>'
                      '<span class="m m-dim">paket summasi %s</span>'
                      '<span class="m m-dim">tugash sanasi %s</span>'
                      % (esc(r.get("tariff_name") or pay_kind), nf(r["price"]), chdate))
        else:
            mon = (TODAY + datetime.timedelta(days=off)).month
            chdate = "%02d.%02d" % (int(r["chday"]), mon)
            state = "bugun tugaydi" if off == 0 else "%d kun qoldi" % off
            detail = '<span class="taskbadge monthly">📅 %s · %s</span>' % (pay_kind, state)
            metric = ('<span class="m m-warn">баланс %s сум</span>'
                      '<span class="m m-dim">нужно %s</span>'
                      '<span class="m m-dim">списание %s</span>'
                      % (nf(r["bal"]), nf(r["price"]), chdate))
        key = "%s_%s" % (kind, sid)
    elif kind == "debtor":
        dp = days_past(r["chday"])
        fresh = "сегодня" if dp==0 else ("вчера" if dp==1 else "%d дн. назад" % dp)
        debit_bug = int(r.get("bal") or 0) >= int(r.get("price") or 0)
        detail = ('<span class="taskbadge overdue">⚠️ CRM автосписание amalga oshmadi</span>' if debit_bug else
                  '<span class="taskbadge overdue">📅 Oylik to‘lov muddati o‘tdi</span>')
        amount = int(r.get("bal") or 0) if debit_bug else abs(int(r.get("bal") or 0))
        amount_label = "баланс" if debit_bug else "долг"
        metric = ('<span class="m m-debt">%s %s сум</span>'
                  '<span class="m m-dim">списание %s</span>' % (amount_label, nf(amount), fresh))
        key = "debtor_%s" % sid
    else:
        reason = REASON_RU.get(r.get("reason"), esc(r.get("reason") or "—"))
        if is_package:
            detail = '<span class="taskbadge package">📦 %s tugadi · muzlatilgan</span>' % pay_kind
        else:
            detail = '<span class="taskbadge frozen">🧊 Oylik to‘lov tugadi · muzlatilgan</span>'
        metric = ('<span class="m m-froz">%s</span>'
                  '<span class="m m-dim">заморозка %s</span>' % (reason, dm(r.get("fdate"))))
        key = "frozen_%s" % sid
    return ('<div class="trow" data-k="%s" data-sid="%s" data-cid="%s" data-group="%s" data-note-ok="%s" data-note-author="%s" '
            'data-note-time="%s" data-cashier="%s"><span class="dot d-%s"></span>'
            '<div class="tmain">%s'
            '<div class="tmeta">%s %s</div></div>'
            '<div class="tright">%s</div>'
            '<a class="call" href="tel:%s">Позвонить</a>'
            '<button class="done" title="Готово">✓</button></div>'
            % (key, sid, cid_of(r), grp, "1" if note_ok else "0", esc(note_author), esc(r.get("note_time") or ""),
               esc(cashier_name), kind, name_html, grp_html, detail, metric, phd))

SECDEF = [
    ("t3","💳","To‘lovga 3 kun qoldi","to‘lov sanasidan aynan 3 kun oldin","b-t3", t3),
    ("debtor","📋","Qarzdor","qarzga kirgan kundan muzlatilguncha har kuni","b-debtor", debtors),
    ("frozen","🧊","Muzlatilgan → Arxiv","muzlatilgandan arxiv statusiga o‘tguncha har kuni","b-frozen", frozen),
]

def render_board(cash_id):
    """cash_id: kassir ID (str) yoki 'all'."""
    total = 0; sec_html = []
    for key, ic, title, sub, bcls, rows in SECDEF:
        rr = rows if cash_id=="all" else [r for r in rows if cid_of(r)==cash_id]
        total += len(rr)
        body = "".join(task_row(r, key) for r in rr) or '<div class="empty">Задач нет</div>'
        sec_html.append(
            '<section class="panel sec" data-sec="%s">'
            '<div class="banner %s"><span class="bi">%s</span><span class="bt">%s</span>'
            '<span class="bc">%d</span><small>%s</small></div>'
            '<div class="list">%s</div></section>' % (key, bcls, ic, title, len(rr), sub, body))
    board_rows = [r for s in SECDEF for r in s[5] if cash_id=="all" or cid_of(r)==cash_id]
    group_counts = {}
    for r in board_rows:
        gn = html.unescape(r.get("grp") or "—").strip() or "—"
        group_counts[gn] = group_counts.get(gn, 0) + 1
    group_buttons = ('<button class="gchip on" data-group="">Все группы <b>%d</b></button>' % total +
                     "".join('<button class="gchip" data-group="%s">%s <b>%d</b></button>' %
                             (esc(g), esc(g), n) for g,n in sorted(group_counts.items())))
    who = "Все кассиры" if cash_id=="all" else esc(CASH.get(cash_id, "—"))
    board = (
        '<div class="board" data-cash="%s" hidden>'
        '<div class="topbar"><button class="back">← Сменить</button>'
        '<div class="who">%s</div>'
        '<div class="pbar"><div class="pfill"></div></div>'
        '<span class="pnum">0 / %d</span></div>'
        '<div class="chips">%s<button class="group-toggle">Группы ▾</button></div>'
        '<div class="groupbar" hidden>%s</div>%s</div>' % (
            cash_id, who, total,
            "".join('<span class="chip" data-sec="%s"><i class="ci d-%s"></i>%s <b class="cbn">%d</b></span>'
                    % (s[0], s[0], s[2], len([r for r in s[5] if cash_id=="all" or cid_of(r)==cash_id]))
                    for s in SECDEF), group_buttons,
            "".join(sec_html)))
    return board, total

# har bir kassir uchun board + jami
boards = []; picks = []
order = sorted((c for c in CASH.keys() if c not in EXCLUDED_CASHIERS), key=lambda c: -sum(
    len([r for r in s[5] if cid_of(r)==c]) for s in SECDEF))
for cid in order:
    b, tot = render_board(cid)
    if tot == 0:  # ishlamayotgan kassir (Shahzoda) — ko'rsatmaymiz
        continue
    boards.append(b)
    nm = CASH[cid]; ini = "".join(w[0] for w in nm.split()[:2]).upper()
    picks.append(
        '<button class="pcard" data-cash="%s"><span class="ava">%s</span>'
        '<span class="pinfo"><span class="pnm">%s</span>'
        '<span class="psub">задач сегодня</span></span>'
        '<span class="pcnt">%d</span></button>' % (cid, esc(ini), esc(nm), tot))
# "Barchasi" board
allb, alltot = render_board("all")
boards.append(allb)
picks.append('<button class="pcard pall" data-cash="all"><span class="ava avall">Σ</span>'
             '<span class="pinfo"><span class="pnm">Все кассиры</span>'
             '<span class="psub">общий список</span></span>'
             '<span class="pcnt">%d</span></button>' % alltot)

STYLE = """
:root{--bg:#f1efe9;--panel:#fff;--panel2:#f4f2ec;--line:#d9dee6;--txt:#10151d;
--mut:#59626f;--dim:#7c8695;--volt:#ff4f28;--volttx:#e63912;
--yellow:#a16207;--orange:#c2410c;--red:#be123c;--blue:#2563eb;--cyan:#0e7490;--green:#047857;
--stripe:rgba(12,16,24,.035);color-scheme:light}
@media(prefers-color-scheme:dark){:root{--bg:#14171d;--panel:#1b1f27;--panel2:#232833;
--line:#2e343f;--txt:#eef2f7;--mut:#9aa4b2;--dim:#6b7788;--stripe:rgba(255,255,255,.02);color-scheme:dark}}
*{box-sizing:border-box}
body{margin:0;color:var(--txt);font:15px/1.5 Manrope,-apple-system,"Segoe UI",Roboto,Arial,sans-serif;
min-height:100vh;padding:18px 20px 80px;
background:repeating-linear-gradient(115deg,transparent 0 54px,var(--stripe) 54px 57px),
radial-gradient(1000px 420px at 0% -10%,rgba(255,79,40,.07),transparent 55%),var(--bg)}
.wrap{max-width:960px;margin:0 auto}
header{display:flex;align-items:baseline;gap:12px;flex-wrap:wrap;margin-bottom:14px}
h1{font:800 26px/1.1 'Barlow Condensed','Barlow',Manrope,sans-serif;margin:0;text-transform:uppercase;letter-spacing:.5px;font-style:italic}
h1 b{color:var(--volttx)}
.meta{color:var(--mut);font-size:12.5px}
.pill-upd{display:inline-flex;align-items:center;gap:6px;background:#e5f4ec;color:#1a7a45;font-size:12px;padding:4px 11px;border-radius:16px}
@media(prefers-color-scheme:dark){.pill-upd{background:#1e3a2a;color:#7fd6a0}}
/* selektor */
.pick{background:var(--panel);border:1px solid var(--line);border-left:4px solid var(--volt);border-radius:14px;padding:18px 20px;box-shadow:0 6px 22px rgba(16,21,29,.05)}
.pick h2{margin:0 0 3px;font:800 19px 'Barlow Condensed',Manrope,sans-serif}
.pick .ph{color:var(--mut);font-size:13px;margin-bottom:14px}
.pgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:11px}
.pcard{display:flex;align-items:center;gap:13px;padding:13px 15px;background:var(--panel2);
border:1px solid var(--line);border-radius:13px;cursor:pointer;font:inherit;color:var(--txt);text-align:left;transition:.14s}
.pcard:hover{border-color:var(--volt);transform:translateY(-1px);box-shadow:0 8px 20px rgba(255,79,40,.12)}
.ava{width:44px;height:44px;border-radius:50%;flex:none;display:flex;align-items:center;justify-content:center;
font:800 15px 'Barlow Condensed',sans-serif;color:#fff;background:linear-gradient(135deg,var(--volt),#ff9a3d)}
.avall{background:linear-gradient(135deg,#3b4252,#59626f)}
.pinfo{flex:1;min-width:0;display:flex;flex-direction:column}
.pnm{font-weight:700;font-size:15px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.psub{color:var(--mut);font-size:12px}
.pcnt{font:800 24px 'Barlow Condensed',sans-serif;color:var(--volttx)}
.pall{border-style:dashed}
/* board */
.topbar{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:12px}
.back{border:1px solid var(--line);background:var(--panel);color:var(--txt);border-radius:9px;padding:7px 13px;cursor:pointer;font:inherit;font-size:13px}
.back:hover{border-color:var(--volt);color:var(--volttx)}
.who{font:800 18px 'Barlow Condensed',Manrope,sans-serif}
.pbar{flex:1;min-width:160px;height:12px;background:var(--panel2);border:1px solid var(--line);border-radius:8px;overflow:hidden}
.pfill{height:100%;width:0;background:linear-gradient(90deg,var(--volt),#ff9a3d);transition:width .5s}
.pnum{font:800 15px 'Barlow Condensed',sans-serif;white-space:nowrap;color:var(--mut)}
.chips{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:16px}
.chip{display:inline-flex;align-items:center;gap:7px;background:var(--panel);border:1px solid var(--line);border-radius:20px;padding:5px 12px;font-size:12.5px;color:var(--mut);cursor:pointer}
.chip b{color:var(--txt);font-weight:800}.chip.off{opacity:.4}
.group-toggle,.gchip{border:1px solid var(--line);background:var(--panel);color:var(--mut);border-radius:20px;padding:5px 12px;font:700 12.5px Manrope,sans-serif;cursor:pointer}
.group-toggle{color:var(--volttx);border-color:rgba(255,79,40,.35)}
.groupbar{display:flex;flex-wrap:wrap;gap:7px;margin:-7px 0 16px;padding:12px;background:var(--panel2);border:1px solid var(--line);border-radius:12px}
.groupbar[hidden]{display:none}.gchip b{color:var(--txt);margin-left:4px}.gchip.on{background:var(--volt);border-color:var(--volt);color:#fff}.gchip.on b{color:#fff}
.ci{width:9px;height:9px;border-radius:50%}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:14px;overflow:hidden;margin-bottom:16px;box-shadow:0 6px 22px rgba(16,21,29,.05)}
.banner{display:flex;align-items:center;gap:10px;padding:12px 16px;font-weight:800;flex-wrap:wrap}
.banner .bi{font-size:18px}.banner .bt{font-size:16px}
.banner .bc{font:800 16px 'Barlow Condensed',sans-serif;background:rgba(0,0,0,.08);border-radius:20px;padding:1px 11px;min-width:34px;text-align:center}
.banner small{font-weight:500;opacity:.8;flex-basis:100%;font-size:12px}
.b-t3{background:rgba(234,179,8,.16);color:var(--yellow)}
.b-debtor{background:rgba(190,18,60,.12);color:var(--red)}
.b-frozen{background:rgba(8,145,178,.14);color:var(--cyan)}
.list{padding:4px 0}
.trow{display:flex;align-items:center;gap:11px;padding:10px 16px;border-top:1px solid var(--line)}
.list .trow:first-child{border-top:none}
.trow.done{display:none}
.trow.group-hidden{display:none}
.dot{width:9px;height:9px;border-radius:50%;flex:none}
.d-t3{background:#eab308}.d-debtor{background:#e11d48}.d-frozen{background:#06b6d4}
.tmain{flex:1;min-width:0}
.tnm{font-weight:700;font-size:14.5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;display:block}
a.tnm{color:var(--txt);text-decoration:none}
a.tnm:hover{color:var(--volttx);text-decoration:underline}
.tmeta{font-size:12px;color:var(--mut);margin-top:1px}
.grp{display:inline-block;background:var(--panel2);border:1px solid var(--line);border-radius:7px;padding:1px 8px;font-size:11.5px;color:var(--mut)}
a.grp{text-decoration:none}
a.grp:hover{border-color:var(--volt);color:var(--txt)}
.taskbadge{display:inline-block;margin-left:5px;border-radius:7px;padding:2px 8px;font-size:11.5px;font-weight:700}
.taskbadge.monthly{background:rgba(124,58,237,.10);color:var(--violet)}
.taskbadge.package{background:rgba(5,150,105,.11);color:var(--green)}
.taskbadge.overdue{background:rgba(190,18,60,.10);color:var(--red)}
.taskbadge.frozen{background:rgba(8,145,178,.11);color:var(--cyan)}
.tright{display:flex;flex-direction:column;align-items:flex-end;gap:2px;text-align:right;flex:none}
.m{font-size:11.5px;white-space:nowrap}
.m-warn{color:var(--yellow);font-weight:700}.m-debt{color:var(--red);font-weight:800}.m-froz{color:var(--cyan);font-weight:700}.m-dim{color:var(--dim)}
.call{flex:none;border:1px solid var(--line);background:var(--panel);color:var(--txt);border-radius:9px;padding:7px 12px;font-size:12.5px;text-decoration:none;white-space:nowrap}
.call:hover{border-color:var(--volt);color:var(--volttx)}
.done{flex:none;width:34px;height:34px;border:1px solid var(--line);background:var(--panel2);color:var(--green);border-radius:9px;cursor:pointer;font-size:15px;font-weight:800}
.done:hover{border-color:var(--green)}
.modal{position:fixed;inset:0;background:rgba(10,12,16,.58);display:flex;align-items:center;justify-content:center;padding:18px;z-index:1000}
.modal[hidden]{display:none}
.modalbox{width:min(520px,100%);background:var(--panel);border:1px solid var(--line);border-top:4px solid var(--green);border-radius:16px;padding:20px;box-shadow:0 24px 80px rgba(0,0,0,.28)}
.modaltitle{font:800 24px/1.1 'Barlow Condensed',Manrope,sans-serif;margin:0 0 7px}
.modalhint{font-size:12.5px;color:var(--mut);margin-bottom:14px}
.modalcard{background:var(--panel2);border:1px solid var(--line);border-radius:11px;padding:11px 13px;margin-bottom:13px}
.modalname{font-weight:800}.modaltask{font-size:12px;color:var(--mut);margin-top:2px}
.verifybox{border:1px solid var(--line);border-radius:11px;padding:12px 13px;margin-top:4px}
.verifybox.ok{background:rgba(5,150,105,.08);border-color:rgba(5,150,105,.28)}
.verifybox.bad{background:rgba(190,18,60,.07);border-color:rgba(190,18,60,.24)}
.verifytitle{font-size:13px;font-weight:800}.verifybox.ok .verifytitle{color:var(--green)}.verifybox.bad .verifytitle{color:var(--red)}
.verifytext{font-size:12px;color:var(--mut);margin-top:4px;line-height:1.55}
.crmopen{display:inline-block;margin-top:9px;color:var(--volttx);font-size:12px;font-weight:800;text-decoration:none}
.crmopen:hover{text-decoration:underline}
.modalactions{display:flex;justify-content:flex-end;gap:9px;margin-top:15px}
.modalcancel,.modalsave{border-radius:9px;padding:8px 14px;font:700 13px Manrope,sans-serif;cursor:pointer}
.modalcancel{border:1px solid var(--line);background:var(--panel2);color:var(--txt)}
.modalsave{border:1px solid var(--green);background:var(--green);color:#fff}
.modalsave:disabled{opacity:.45;cursor:not-allowed}
.empty{padding:22px 16px;color:var(--mut);font-size:13px}
.foot{margin-top:22px;color:var(--dim);font-size:11.5px;text-align:center}
@media(max-width:600px){.tright{max-width:42%}.tnm{font-size:14px}.call{padding:7px 9px}}
"""

JS = """
(function(){
 var DKEY='kassir_done_v3_'+DATE, PKEY='kassir_pick_'+DATE;
 var done={};try{done=JSON.parse(localStorage.getItem(DKEY)||'{}')}catch(e){}
 function save(){try{localStorage.setItem(DKEY,JSON.stringify(done))}catch(e){}}
 var pick=document.getElementById('pick');
 var modal=document.getElementById('closeModal'),saveBtn=document.getElementById('modalSave');
 var pendingKey=null,pendingAuthor='',pendingTime='';
 function closeModal(){modal.hidden=true;pendingKey=null;pendingAuthor='';pendingTime='';}
 async function liveNote(row){
  var sid=Number(row.dataset.sid),cid=Number(row.dataset.cid);
  if(!Number.isInteger(sid)||sid<1||!Number.isInteger(cid)||cid<1)throw new Error('invalid ids');
  var sql="SELECT nl.ID, DATE_FORMAT(nl.CREATED_DATE,'%H:%i') note_time, u.NAME, u.SURNAME "+
   "FROM note_list nl JOIN gl_sys_users u ON u.ID=nl.CREATED_BY "+
   "WHERE nl.STUDENT_ID="+sid+" AND nl.ACTIVE=1 AND nl.CREATED_BY="+cid+
   " AND nl.CREATED_DATE>=CURDATE() AND nl.CREATED_DATE<CURDATE()+INTERVAL 1 DAY ORDER BY nl.ID DESC LIMIT 1";
  var res=await fetch('https://myclinic.agc.uz/new_junior_mcp.php',{method:'POST',headers:{'Content-Type':'application/json'},
   body:JSON.stringify({jsonrpc:'2.0',id:1,method:'tools/call',params:{name:'query_db',arguments:{sql:sql}}})});
  if(!res.ok)throw new Error('CRM '+res.status);
  var outer=await res.json(),txt=outer.result.content[0].text,inner=JSON.parse(txt);
  var rows=inner.data&&inner.data.data||[];return Array.isArray(rows)&&rows.length?rows[0]:null;
 }
 async function openModal(row){
  pendingKey=row.dataset.k;
  document.getElementById('modalStudent').textContent=row.querySelector('.tnm').textContent;
  var sec=row.closest('.sec');
  document.getElementById('modalTask').textContent=sec.querySelector('.bt').textContent;
  var box=document.getElementById('verifyBox');
  box.className='verifybox';
  document.getElementById('verifyTitle').textContent='CRM eslatmasi tekshirilmoqda…';
  document.getElementById('verifyText').textContent='Iltimos, bir necha soniya kuting.';
  document.getElementById('crmOpen').href=row.querySelector('.tnm').href;
  saveBtn.disabled=true;modal.hidden=false;
  var note=null,failed=false;
  try{note=await liveNote(row)}catch(e){failed=true}
  if(pendingKey!==row.dataset.k)return;
  var ok=!!note;
  if(ok){
   pendingAuthor=((note.NAME||'')+' '+(note.SURNAME||'')).trim();pendingTime=note.note_time||'';
   row.dataset.noteOk='1';row.dataset.noteAuthor=pendingAuthor;row.dataset.noteTime=pendingTime;
  }else{pendingAuthor='';pendingTime='';}
  box.className='verifybox '+(ok?'ok':'bad');
  document.getElementById('verifyTitle').textContent=ok?'✓ CRM eslatmasi topildi':(failed?'CRM bilan aloqa bo‘lmadi':'CRM eslatmasi topilmadi');
  document.getElementById('verifyText').textContent=ok
   ?('Muallif: '+pendingAuthor+' · Bugun '+pendingTime)
   :(failed?'Tekshiruvni qayta boshlash uchun oynani yoping va galochkani yana bosing.':
    ('Bugun mas’ul kassir '+row.dataset.cashier+' tomonidan yozilgan eslatma kerak. CRM kartochkasiga eslatma yozing, so‘ng galochkani yana bosing.'));
  saveBtn.disabled=!ok;
 }
 function show(cash){
  pick.style.display='none';
  document.querySelectorAll('.board').forEach(function(b){b.hidden=(b.dataset.cash!==cash)});
  try{localStorage.setItem(PKEY,cash)}catch(e){}
  window.scrollTo(0,0); upd();
 }
 function back(){pick.style.display='';document.querySelectorAll('.board').forEach(function(b){b.hidden=true});
  try{localStorage.removeItem(PKEY)}catch(e){}window.scrollTo(0,0);}
 document.querySelectorAll('.pcard').forEach(function(c){c.onclick=function(){show(c.dataset.cash)}});
 document.querySelectorAll('.back').forEach(function(b){b.onclick=back});
 document.addEventListener('click',function(e){
  var d=e.target.closest('.done');if(!d)return;
  openModal(d.closest('.trow'));
 });
 saveBtn.onclick=function(){
  if(saveBtn.disabled||!pendingAuthor||!pendingTime)return;
  done[pendingKey]={verified_note:true,note_author:pendingAuthor,note_time:pendingTime,closed_at:new Date().toISOString()};
  save();closeModal();upd();
 };
 document.getElementById('modalCancel').onclick=closeModal;
 modal.addEventListener('click',function(e){if(e.target===modal)closeModal()});
 document.addEventListener('keydown',function(e){if(e.key==='Escape'&&!modal.hidden)closeModal()});
 document.querySelectorAll('.chip').forEach(function(ch){ch.onclick=function(){
  ch.classList.toggle('off');
  var b=ch.closest('.board');var sec=b.querySelector('.sec[data-sec="'+ch.dataset.sec+'"]');
  if(sec)sec.style.display=ch.classList.contains('off')?'none':'';
 }});
 document.querySelectorAll('.group-toggle').forEach(function(btn){btn.onclick=function(){
  var bar=btn.closest('.board').querySelector('.groupbar');bar.hidden=!bar.hidden;
  btn.textContent=bar.hidden?'Группы ▾':'Группы ▴';
 }});
 document.querySelectorAll('.gchip').forEach(function(ch){ch.onclick=function(){
  var b=ch.closest('.board'),group=ch.dataset.group||'';
  b.querySelectorAll('.gchip').forEach(function(x){x.classList.toggle('on',x===ch)});
  b.querySelectorAll('.trow').forEach(function(r){r.classList.toggle('group-hidden',!!group&&r.dataset.group!==group)});
 }});
 function upd(){
  document.querySelectorAll('.board').forEach(function(b){
   var rows=b.querySelectorAll('.trow'),tot=rows.length,cl=0;
   rows.forEach(function(r){if(done[r.dataset.k]&&done[r.dataset.k].verified_note){r.classList.add('done');cl++}else r.classList.remove('done')});
   var pct=tot?Math.round(cl/tot*100):0;
   b.querySelector('.pfill').style.width=pct+'%';
   b.querySelector('.pnum').textContent=cl+' / '+tot+' · '+pct+'%';
   b.querySelectorAll('.sec').forEach(function(s){
    var open=0;s.querySelectorAll('.trow').forEach(function(r){if(!(done[r.dataset.k]&&done[r.dataset.k].verified_note))open++});
    var c=s.querySelector('.bc');if(c)c.textContent=open;
    var chip=b.querySelector('.chip[data-sec="'+s.dataset.sec+'"] .cbn');if(chip)chip.textContent=open;
   });
   var card=document.querySelector('.pcard[data-cash="'+b.dataset.cash+'"] .pcnt');if(card)card.textContent=tot-cl;
  });
 }
 upd();
 var saved=null;try{saved=localStorage.getItem(PKEY)}catch(e){}
 if(saved&&document.querySelector('.board[data-cash="'+saved+'"]'))show(saved);
})();
"""

HTML = u"""<!doctype html><html lang="ru"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="refresh" content="300">
<title>Кассиры · задачи на сегодня</title>
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Barlow:ital,wght@0,700;0,800;1,800&family=Barlow+Condensed:ital,wght@0,700;0,800;1,800&family=Manrope:wght@500;700;800&display=swap" rel="stylesheet">
<style>%s</style></head><body>
<div class="wrap">
<header><h1>Кассиры · <b>задачи на сегодня</b></h1>
 <span class="meta">%s · ORG 6</span><span class="pill-upd">● Авто-сбор</span></header>
<div class="pick" id="pick">
 <h2>Кто вы?</h2><div class="ph">Выберите себя — откроется ваш список на сегодня</div>
 <div class="pgrid">%s</div></div>
%s
<div class="foot">Источник: junior базаси (ORG 6) · закрепление кассира по группе (CASHIER_ID) · MCP gateway.<br>
Закрытые задачи и заметки хранятся в этом браузере до конца дня.</div>
</div>
<div class="modal" id="closeModal" hidden>
 <div class="modalbox" role="dialog" aria-modal="true" aria-labelledby="modalTitle">
 <h2 class="modaltitle" id="modalTitle">Vazifani yopish</h2>
  <div class="modalhint">Vazifa faqat CRM kartochkasidagi bugungi eslatma tekshirilgandan keyin yopiladi.</div>
  <div class="modalcard"><div class="modalname" id="modalStudent"></div><div class="modaltask" id="modalTask"></div></div>
  <div class="verifybox bad" id="verifyBox"><div class="verifytitle" id="verifyTitle"></div><div class="verifytext" id="verifyText"></div>
   <a class="crmopen" id="crmOpen" href="#" target="_blank" rel="noopener">CRM kartochkasini ochish ↗</a></div>
  <div class="modalactions"><button class="modalcancel" id="modalCancel">Bekor qilish</button><button class="modalsave" id="modalSave" disabled>Vazifani yopish</button></div>
 </div>
</div>
<script>var DATE="%s";%s</script>
</body></html>""" % (STYLE, esc(NOW_TS), "".join(picks), "".join(boards), TODAY.isoformat(), JS)

# CI (GitHub Actions) rejimida repo ildiziga kassir-vazifalar.html yoziladi,
# lokalda esa preview uchun index.html.
if os.environ.get("GITHUB_ACTIONS") == "true":
    OUT = os.path.join(os.getcwd(), "kassir-vazifalar.html")
else:
    OUT = os.path.join(HERE, "index.html")
with open(OUT, "w", encoding="utf-8") as f:
    f.write(HTML)

print("OK ->", OUT)
print("today=%s dom=%d  t3=%d debtors=%d frozen=%d  TOTAL=%d"
      % (TODAY, DOM, len(t3), len(debtors), len(frozen), alltot))
for cid in order:
    tot = sum(len([r for r in s[5] if cid_of(r)==cid]) for s in SECDEF)
    if tot: print("  %-22s %d" % (CASH[cid], tot))
