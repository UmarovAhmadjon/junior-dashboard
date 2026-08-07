#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""25.07–24.08 qarzdorlar dashboardi. Manba: Junior MCP / QARZDORLAR."""
import os, sys, json, datetime, urllib.request, time
import live_update as ui

GATEWAY=os.environ.get("JUNIOR_MCP_GATEWAY","https://myclinic.agc.uz/new_junior_mcp.php")
ORG=6
TEST_ADMIN_IDS={21453}  # MK super teacher — test akkaunt
CYCLE_START=datetime.date(2026,7,25)
CYCLE_END=datetime.date(2026,8,24)
PERIOD=next((x for x in sys.argv[1:] if x in ("month","w1","w2","w3","w4","all")),"month")
PERIODS=[
 ("month",CYCLE_START,CYCLE_END,"25.07–24.08 · весь цикл"),
 ("w1",datetime.date(2026,7,25),datetime.date(2026,7,31),"25.07–31.07 · неделя 1"),
 ("w2",datetime.date(2026,8,1),datetime.date(2026,8,7),"01.08–07.08 · неделя 2"),
 ("w3",datetime.date(2026,8,8),datetime.date(2026,8,14),"08.08–14.08 · неделя 3"),
 ("w4",datetime.date(2026,8,15),datetime.date(2026,8,24),"15.08–24.08 · неделя 4"),
]

def q(sql):
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
            if data==["empty"] or data=="empty": return []
            return data if isinstance(data,list) else [data]
        except Exception as e:
            last=e
            if attempt==3: raise
            time.sleep(2*(attempt+1))

def chunks(values,n=350):
    values=list(values)
    for i in range(0,len(values),n): yield values[i:i+n]

def in_sql(ids): return ",".join(str(int(x)) for x in ids) or "0"

def plan_ids():
    rr=q("SELECT ID,DATA FROM debtors_plan WHERE ID IN (79,80)")
    data={int(r["ID"]):set(map(int,json.loads(r["DATA"]))) for r in rr}
    july=data.get(79,set()); aug=data.get(80,set())
    # 26–31 iyulda yopilgan eski qarzdorlar avgust snapshotidan tushib qolishi mumkin.
    paid_early=set()
    for ch in chunks(july):
        for r in q("SELECT DISTINCT STUDENT_ID sid FROM transaction_list WHERE ACTION_TYPE='add' "
                   "AND TRANSACTION_DATE>='2026-07-25' AND TRANSACTION_DATE<'2026-08-01' "
                   "AND STUDENT_ID IN (%s)"%in_sql(ch)):
            paid_early.add(int(r["sid"]))
    return aug | paid_early

def student_meta(ids):
    """Latest active subscription -> group Admin, payment day/status/balance."""
    best={}
    for ch in chunks(ids):
        sql=("SELECT sub.ID sub_id,sub.STUDENT_ID sid,sub.DAY pay_day,sub.STATUS sub_status,"
             "g.ADMIN_ID aid,g.CASHIER_ID cid,s.NAME student_name,s.CURRENT_BALANCE bal,u.NAME admin_name,u.SURNAME admin_surname,u.TEAM_ID team_id,"
             "cu.NAME cashier_name,cu.SURNAME cashier_surname "
             "FROM subscribe_list sub JOIN student_list s ON s.ID=sub.STUDENT_ID "
             "LEFT JOIN group_list g ON g.ID=sub.GROUP_ID "
             "LEFT JOIN gl_sys_users u ON u.ID=g.ADMIN_ID "
             "LEFT JOIN gl_sys_users cu ON cu.ID=g.CASHIER_ID "
             "WHERE sub.ORG_ID=%d AND sub.ACTIVE=1 AND sub.STUDENT_ID IN (%s) "
             "ORDER BY sub.STUDENT_ID,sub.ID DESC"%(ORG,in_sql(ch)))
        for r in q(sql):
            sid=int(r["sid"])
            if sid not in best: best[sid]=r
    return best

def payments(ids,start,end):
    out={}
    endx=end+datetime.timedelta(days=1)
    for ch in chunks(ids):
        sql=("SELECT STUDENT_ID sid,SUM(AMOUNT) amount FROM transaction_list "
             "WHERE ACTION_TYPE='add' AND TRANSACTION_DATE>='%s' AND TRANSACTION_DATE<'%s' "
             "AND STUDENT_ID IN (%s) GROUP BY STUDENT_ID"%(start,endx,in_sql(ch)))
        for r in q(sql):
            amount=int(r.get("amount") or 0)
            if amount>0: out[int(r["sid"])]=amount
    return out

def due_date(day):
    try: d=int(float(day or 0))
    except Exception: return CYCLE_START
    if d>=25:
        d=min(d,31); return datetime.date(2026,7,d)
    d=max(1,min(d,24)); return datetime.date(2026,8,d)

def curator_catalog(target_ids,meta):
    cat={}
    for sid in target_ids:
        m=meta.get(sid) or {}
        aid=int(m.get("aid") or 0)
        if aid in TEST_ADMIN_IDS: aid=0
        if aid in cat: continue
        if not m or not aid:
            cat[0]=("U","Biriktirilmagan","Biriktirilmagan")
            continue
        name=(str(m.get("admin_name") or "").strip()+" "+str(m.get("admin_surname") or "").strip()).strip()
        short=(str(m.get("admin_name") or "Admin").strip().split() or ["Admin"])[0]
        team="A" if int(m.get("team_id") or 0)==2 else "B"
        cat[aid]=(team,short,name or short)
    return cat

def cashier_catalog(target_ids,meta,catalog):
    links={}; names={}
    for sid in target_ids:
        m=meta.get(sid) or {}; cid=int(m.get("cid") or 0); aid=int(m.get("aid") or 0)
        if aid in TEST_ADMIN_IDS: aid=0
        if not cid or not aid or aid not in catalog: continue
        links.setdefault(cid,set()).add(aid)
        nm=(str(m.get("cashier_name") or "").strip()+" "+str(m.get("cashier_surname") or "").strip()).strip()
        names[cid]=nm or ("Kassir %s"%cid)
    out=[]
    for cid,aids in links.items():
        shorts=[catalog[a][1] for a in aids]
        teams=[catalog[a][0] for a in aids]
        team="A" if teams.count("A")>=teams.count("B") else "B"
        out.append((names[cid].split()[0],team,shorts))
    return out

def aggregate(target_ids,meta,pays,p_start,p_end,catalog):
    by={aid:dict(plan=0,plansum=0,paid=0,tol=0,bit=0,sar=0,muz=0,arx=0,sob=0,due=0)
        for aid in catalog}
    for sid in target_ids:
        m=meta.get(sid) or {}
        aid=int(m.get("aid") or 0)
        if aid in TEST_ADMIN_IDS: aid=0
        if aid not in by: aid=0
        if aid not in by: continue
        dd=due_date(m.get("pay_day"))
        a=by[aid]; a["plan"]+=1
        paid_amt=pays.get(sid,0)
        remaining=max(0,-int(m.get("bal") or 0))
        a["plansum"]+=paid_amt+remaining
        if paid_amt>0:
            a["paid"]+=1; a["tol"]+=1; a["sob"]+=paid_amt
        st=str(m.get("sub_status") or "").lower()
        if st in ("freezed","frozen"): a["muz"]+=1
        elif st in ("archive","archived","inactive"): a["arx"]+=1
        if dd<=min(datetime.date.today(),p_end): a["due"]+=1
    rows=[]
    for aid,(team,short,full) in catalog.items():
        a=by[aid]; plan=a["plan"]; paid=a["paid"]
        rows.append(dict(team=team,short=short,full=full,paid=paid,tol=a["tol"],bit=0,sar=0,
            muz=a["muz"],arx=a["arx"],plan=plan,plansum=a["plansum"],debt=max(0,plan-paid),
            sob=a["sob"],pct=round(paid/plan*100) if plan else 0,due=a["due"],hidden=(aid==0)))
    return rows

def detail_data(target_ids,meta,pays,p_start,p_end,catalog):
    out=[]
    for sid in target_ids:
        m=meta.get(sid) or {}; aid=int(m.get("aid") or 0)
        if aid in TEST_ADMIN_IDS: aid=0
        dd=due_date(m.get("pay_day"))
        curator=catalog.get(aid,catalog.get(0,("B","Biriktirilmagan","")))[1]
        amount=int(pays.get(sid,0)); debt=max(0,-int(m.get("bal") or 0))
        out.append(dict(id=sid,name=str(m.get("student_name") or ("O‘quvchi %s"%sid)).strip(),
            curator=curator,status="To'lagan" if amount>0 else "Qarzdor",paid=amount,debt=debt,
            period=dd.strftime("%d.%m.%Y")))
    return sorted(out,key=lambda x:(x["status"]!="To'lagan",x["curator"],x["name"]))

def main():
    ui.PREVIEW=("preview" in sys.argv); ui.IS_CI=os.environ.get("GITHUB_ACTIONS")=="true"
    ids=plan_ids(); meta=student_meta(ids); catalog=curator_catalog(ids,meta)
    ui.CUR=list(catalog.values())
    ui.CASHIERS=cashier_catalog(ids,meta,catalog)
    # QARZDOR soni sana bilan kesilmaydi: u moduldagi joriy Qarzdor filtridan.
    # Sana faqat FAKT (tanlangan davrda To'lagan) uchun ishlaydi.
    paymaps={k:payments(ids,a,b) for k,a,b,_l in PERIODS}
    # Bitta snapshot: qarzdorlar soni barcha haftalarda aynan bir xil bazadan chiqadi.
    # Month uchun qayta so'rov yuborilmaydi — yangilanish o'rtasida 1-2 ta farq paydo bo'lmaydi.
    cycle_pays=paymaps["month"]
    for key in ("w1","w2","w3","w4"):
        paymaps[key]={sid:amount for sid,amount in paymaps[key].items() if sid in cycle_pays}
    debt_ids=set(ids)-set(cycle_pays)
    weeks=[]
    for _k,a,b,_l in PERIODS[1:]:
        wp=paymaps[_k]; wids=debt_ids | set(wp)
        wr=aggregate(wids,meta,wp,a,b,catalog)
        weeks.append(dict(ws=a,we=b,total=sum(x["plan"] for x in wr),paid=sum(x["paid"] for x in wr),
            qarz=sum(x["debt"] for x in wr),muz=sum(x["muz"] for x in wr),arx=sum(x["arx"] for x in wr),sob=sum(x["sob"] for x in wr),
            A_total=sum(x["plan"] for x in wr if x["team"]=="A"),A_paid=sum(x["paid"] for x in wr if x["team"]=="A"),A_sob=sum(x["sob"] for x in wr if x["team"]=="A"),
            B_total=sum(x["plan"] for x in wr if x["team"]=="B"),B_paid=sum(x["paid"] for x in wr if x["team"]=="B"),B_sob=sum(x["sob"] for x in wr if x["team"]=="B")))
    nowrow=q("SELECT NOW() n")[0]["n"]
    tnow=datetime.datetime.fromisoformat(str(nowrow))
    selected=PERIODS if PERIOD=="all" else [x for x in PERIODS if x[0]==PERIOD]
    for key,p_start,p_end,label in selected:
        ui.PERIOD=key
        pays=paymaps[key]
        period_ids=debt_ids | set(pays)
        rows=aggregate(period_ids,meta,pays,p_start,p_end,catalog)
        ui.DETAIL_DATA=detail_data(period_ids,meta,pays,p_start,p_end,catalog)
        due_total=sum(x["due"] for x in rows); due_paid=sum(min(x["paid"],x["due"]) for x in rows)
        ui.render(tnow,p_start,p_end,rows,sum(x["plan"] for x in rows),sum(x["plansum"] for x in rows),
                  weeks,due_total,due_paid,PERIODS,label)

if __name__=="__main__": main()
