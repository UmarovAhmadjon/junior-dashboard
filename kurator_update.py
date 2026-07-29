#!/usr/bin/env python3
"""Kurator Tahlili — CRM Analitika modulidan jonli ma'lumot yig'ib, kurator.html ni yangilaydi.
Manba: crm.junior-it.uz Analitika AJAX API. Login: .crm_login (phone / pass).
Haftalik churn hodisalari: junior-lms MCP (student_status_logs).
Deploy: GitHub Pages (kurator.html)."""
import os, re, json, base64, pathlib, urllib.request, urllib.parse, http.cookiejar, time, datetime, calendar

HOME = pathlib.Path.home() / 'junior-dashboard'
REPO = 'UmarovAhmadjon/junior-dashboard'
CRM = 'https://crm.junior-it.uz'
MCP = 'https://myclinic.agc.uz/new_junior_mcp.php'
TASHKENT_NOW = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=5)))
MONTH = os.environ.get('KURATOR_MONTH', TASHKENT_NOW.strftime('%Y-%m-01'))

# admin_id lar (CRM admin-select)
CUR = {
 'Fot':('Fotimabonu Abdulkhakova','A','13799',5), 'Mad':('Madina Normatova','A','16005',5),
 'Dil':('Dilafruz Shokirova','A','14241',6), 'Jas':('Jasmina Tolibova','A','14974',4),
 'Nai':('Naima Ikramova','A','16876',4), 'Shx':('Shaxlo Ziyodova','A','21463',3),
 'Mar':('Marjona Pardayeva','B','14451',5), 'Xal':('Xalima Ismoiljonova','B','16386',6),
 'Azi':('Aziza Qurvonaliyeva','B','17542',5), 'Sab':('Sabrina Salimova','B','18307',6),
 'Mun':('Munisa Sobirjonova','B','18784',7),
}
JULY_BASELINE = {
    'date':'2026-07-01', 'total':2127,
    'curators': {
        'Fot':171, 'Dil':280, 'Mad':212, 'Jas':250, 'Nai':187,
        'Mun':171, 'Mar':238, 'Xal':246, 'Azi':175, 'Sab':197,
    },
    'note':'Foydalanuvchi bergan 01.07.2026 ro‘yxati; eski Munisa → Naima, Maryam → Munisa. New kurator olib tashlangan.'
}
TEAM_A_IDS = '13799,14241,14974,16005,16876,21463'
TEAM_B_IDS = '14451,16386,17542,18307,18784'

CI = bool(os.environ.get('GITHUB_ACTIONS'))
BASE = pathlib.Path('.') if CI else HOME  # CIda repo checkout, lokalda ~/junior-dashboard

# ---------- CRM sessiya ----------
def crm_session():
    if os.environ.get('CRM_PHONE') and os.environ.get('CRM_PASS'):
        phone, pw = os.environ['CRM_PHONE'].strip(), os.environ['CRM_PASS'].strip()
    else:
        phone, pw = [x.strip() for x in (HOME/'.crm_login').read_text().strip().splitlines()[:2]]
    cj = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    op.addheaders = [('User-Agent','Mozilla/5.0'),('X-Requested-With','XMLHttpRequest')]
    op.open(CRM+'/account/', timeout=40).read()
    op.open(CRM+'/account/', urllib.parse.urlencode({'phone':phone,'pass':pw}).encode(), timeout=40).read()
    return op

def strip(html):
    return re.sub(r'\s+',' ',re.sub(r'<[^>]+>',' ',html)).strip()

def api(op, ep, admin, month=None):
    d = urllib.parse.urlencode({'admin_id':admin,'month':month or MONTH}).encode()
    r = op.open(CRM+'/account/ajax/analytics/'+ep+'.php', d, timeout=40)
    return strip(r.read().decode(errors='ignore'))

def prev_month(m):
    y, mo, _ = m.split('-')
    y, mo = int(y), int(mo)
    mo -= 1
    if mo == 0: mo = 12; y -= 1
    return f'{y:04d}-{mo:02d}-01'

def churn_pct(op, admin, month):
    pl = p_plan(api(op, 'plan-cards/churn-student-plan-card', admin, month))
    return pl[1] if pl else None

def p_status(t):
    m = re.search(r'([\d\s]+)\(([\d.]+)%\)', t)
    return [int(m.group(1).replace(' ','')), float(m.group(2))] if m else [0,0.0]
def p_plan(t):
    m = re.search(r'\(([\d\s]+)\).*Fakt\s*([\d.]+)%\s*\(([\d\s]+)\)', t) or re.search(r'Fakt\s*([\d.]+)%\s*\(([\d\s]+)\)', t)
    if not m: return None
    g = m.groups()
    return [int(g[0].replace(' ','')), float(g[1]), int(g[2].replace(' ',''))] if len(g)==3 else [None, float(g[0]), int(g[1].replace(' ',''))]
def p_int(t):
    m = re.search(r'([\d\s]+)', t); return int(m.group(1).replace(' ','')) if m else 0

def grab(op, admin):
    g = lambda ep: api(op, ep, admin)
    return {
        'a': p_status(g('status-student/status-student-active')),
        'p': p_status(g('status-student/status-student-passive')),
        'x': p_status(g('status-student/status-student-noactive')),
        'y': p_status(g('status-student/status-student-new')),
        'chu': p_plan(g('plan-cards/churn-student-plan-card')),
        'fao': p_plan(g('plan-cards/activate-student-plan-card')),
        'b': p_int(g('top-cards/active-student-card')),
        'kassa': p_int(g('plan-cards/cashier-plan-card').replace('Student Kassa','')),
        'yangi': (p_plan(g('plan-cards/new-student-plan-card')) or [None,0,0])[2],
        'qayta': (p_plan(g('plan-cards/reactivated-student-plan-card')) or [None,0,0])[2],
        'ota': p_status(g('top-cards/active-parent-student-card')),
        'froz': p_int(g('top-cards/frozen-student-card')),
        'qarz': p_int(g('top-cards/debtors-student-card')),
    }

# ---------- haftalik churn (MCP) ----------
def mcp(sql):
    body = json.dumps({'jsonrpc':'2.0','id':1,'method':'tools/call',
                       'params':{'name':'query_db','arguments':{'sql':sql}}}).encode()
    req = urllib.request.Request(MCP, body, {'Content-Type':'application/json'})
    for attempt in range(4):
        try:
            r = json.load(urllib.request.urlopen(req, timeout=90))
            txt = r['result']['content'][0]['text']
            rows = json.loads(txt).get('data',{}).get('data',[])
            if rows:
                return rows
            raise RuntimeError('MCP bo‘sh javob qaytardi')
        except Exception as e:
            print(f'MCP urinish {attempt+1}/4 xato:', e)
            if attempt < 3:
                time.sleep(8)
    return []

def weekly(admin_ids_list, weeks):
    """har hafta: [faollashdi, passivga, noaktivga] guruh ADMIN_ID orqali."""
    ids = ','.join(admin_ids_list)
    when = " ".join([f"WHEN l.changed_at<'{w[1]}' THEN '{w[0]}'" for w in weeks[:-1]])
    last = weeks[-1][0]
    sql = (f"SELECT t.admin, CASE {when} ELSE '{last}' END wk, l.to_status, COUNT(DISTINCT l.student_id) c "
           f"FROM (SELECT s.STUDENT_ID, MIN(g.ADMIN_ID) admin FROM group_list g "
           f"JOIN subscribe_list s ON s.GROUP_ID=g.ID WHERE g.ADMIN_ID IN ({ids}) GROUP BY s.STUDENT_ID) t "
           f"JOIN student_status_logs l ON l.student_id=t.STUDENT_ID "
           f"WHERE l.changed_at>='{weeks[0][2]}' AND l.to_status IN ('active','passive','not_active') "
           f"GROUP BY t.admin, wk, l.to_status")
    rows = mcp(sql)
    st = {'active':0,'passive':1,'not_active':2}
    out = {}
    for r in rows:
        a=str(r['admin']); wk=r['wk']; s=st[r['to_status']]
        out.setdefault(a,{}).setdefault(wk,[0,0,0])[s]=int(r['c'])
    return out

def weekly_noaktiv_net(admin_ids_list, weeks):
    """Hafta ichidagi Noaktiv qoldig'i o'zgarishi: kirganlar minus chiqqanlar."""
    ids = ','.join(admin_ids_list)
    when = " ".join([f"WHEN l.changed_at<'{w['end_exclusive']}' THEN '{w['key']}'" for w in weeks[:-1]])
    last = weeks[-1]['key']
    sql = (
        f"SELECT t.admin, CASE {when} ELSE '{last}' END wk, "
        f"SUM(CASE WHEN l.to_status='not_active' AND l.from_status<>'not_active' THEN 1 "
        f"WHEN l.from_status='not_active' AND l.to_status<>'not_active' THEN -1 ELSE 0 END) net "
        f"FROM (SELECT s.STUDENT_ID, MIN(g.ADMIN_ID) admin FROM group_list g "
        f"JOIN subscribe_list s ON s.GROUP_ID=g.ID "
        f"WHERE g.ADMIN_ID IN ({ids}) AND s.STATUS IN ('active','freezed') GROUP BY s.STUDENT_ID) t "
        f"JOIN student_status_logs l ON l.student_id=t.STUDENT_ID "
        f"WHERE l.changed_at>='{weeks[0]['start']}' AND l.changed_at<'{weeks[-1]['end_exclusive']}' "
        f"AND (l.to_status='not_active' OR l.from_status='not_active') GROUP BY t.admin,wk"
    )
    out = {}
    for r in mcp(sql):
        out.setdefault(str(r['admin']), {})[r['wk']] = int(r['net'] or 0)
    return out

def period_kpis(admin_ids_list, weeks):
    """Oy/hafta bo'yicha yangi va qayta faollashtirilgan unik o'quvchilar."""
    ids = ','.join(admin_ids_list)
    when = " ".join([f"WHEN l.changed_at<'{w['end_exclusive']}' THEN '{w['key']}'" for w in weeks[:-1]])
    last = weeks[-1]['key']
    base = (
        f"FROM (SELECT s.STUDENT_ID,MIN(g.ADMIN_ID) admin FROM subscribe_list s "
        f"JOIN group_list g ON g.ID=s.GROUP_ID WHERE g.ADMIN_ID IN ({ids}) "
        f"AND s.STATUS IN ('active','freezed') GROUP BY s.STUDENT_ID) t "
        f"JOIN student_status_logs l ON l.student_id=t.STUDENT_ID "
        f"WHERE l.changed_at>='{weeks[0]['start']}' AND l.changed_at<'{weeks[-1]['end_exclusive']}' "
    )
    counts = (
        f"COUNT(DISTINCT CASE WHEN l.to_status='new' THEN l.student_id END) yangi, "
        f"COUNT(DISTINCT CASE WHEN l.from_status IN ('passive','not_active') "
        f"AND l.to_status='active' THEN l.student_id END) faollashgan "
    )
    sql = (
        f"SELECT t.admin,CASE {when} ELSE '{last}' END wk,{counts}{base} GROUP BY t.admin,wk "
        f"UNION ALL SELECT t.admin,'ALL' wk,{counts}{base} GROUP BY t.admin"
    )
    out = {}
    for r in mcp(sql):
        out.setdefault(str(r['admin']), {})[r['wk']] = {
            'yangi': int(r['yangi'] or 0),
            'fao': int(r['faollashgan'] or 0),
        }
    return out

def monthly_debtor_plan(admin_ids_list, month):
    """debtors_plan.DATA dagi oylik reja, amaldagi oylik obunalar bo'yicha."""
    ids = ','.join(admin_ids_list)
    sql = (
        f"SELECT g.ADMIN_ID admin,COUNT(DISTINCT s.STUDENT_ID) qarz_plan "
        f"FROM group_list g JOIN subscribe_list s ON s.GROUP_ID=g.ID "
        f"JOIN debtors_plan d ON d.START_DATE='{month}' "
        f"WHERE g.ADMIN_ID IN ({ids}) AND s.ACTIVE=1 AND s.TYPE='monthly' AND s.STATUS='active' "
        f"AND JSON_CONTAINS(d.DATA,JSON_QUOTE(CAST(s.STUDENT_ID AS CHAR)),'$') "
        f"GROUP BY g.ADMIN_ID"
    )
    return {str(r['admin']):int(r['qarz_plan']) for r in mcp(sql)}

def current_student_counts(admin_ids_list):
    """Analitika API emas: amaldagi active obunalar bo'yicha unik o'quvchilar."""
    ids = ','.join(admin_ids_list)
    sql = (
        f"SELECT g.ADMIN_ID admin,COUNT(DISTINCT s.STUDENT_ID) students "
        f"FROM subscribe_list s JOIN group_list g ON g.ID=s.GROUP_ID "
        f"WHERE g.ADMIN_ID IN ({ids}) AND s.ACTIVE=1 AND s.STATUS='active' "
        f"GROUP BY g.ADMIN_ID"
    )
    return {str(r['admin']):int(r['students']) for r in mcp(sql)}

def monthly_churn_counts(admin_ids_list, month, month_end):
    """Oyda not_active'ga o'tgan unik student; oxirgi relevant guruhi bo'yicha bir marta."""
    ids = ','.join(admin_ids_list)
    sql = (
        f"SELECT g.ADMIN_ID admin,COUNT(DISTINCT l.student_id) churn_students "
        f"FROM student_status_logs l "
        f"JOIN subscribe_list s ON s.ID=(SELECT MAX(s2.ID) FROM subscribe_list s2 "
        f"JOIN group_list g2 ON g2.ID=s2.GROUP_ID WHERE s2.STUDENT_ID=l.student_id "
        f"AND g2.ADMIN_ID IN ({ids})) "
        f"JOIN group_list g ON g.ID=s.GROUP_ID "
        f"WHERE l.changed_at>='{month}' AND l.changed_at<'{month_end}' "
        f"AND l.to_status='not_active' GROUP BY g.ADMIN_ID"
    )
    return {str(r['admin']):int(r['churn_students']) for r in mcp(sql)}

def old_snapshots():
    try:
        old = (BASE/'kurator.html').read_text()
        match = re.search(r'const __DATA__=(\{.*?\});', old)
        return json.loads(match.group(1)).get('snapshots',{}) if match else {}
    except Exception:
        return {}

def month_weeks(month):
    """Oyni 1–7, 8–14, 15–21, 22–oy oxiri ko'rinishida avtomatik yaratadi."""
    y, mo = map(int, month[:7].split('-'))
    last = calendar.monthrange(y, mo)[1]
    starts = (1, 8, 15, 22)
    weeks = []
    for i, day in enumerate(starts, 1):
        end_day = starts[i] if i < 4 else last + 1
        end_exclusive = (datetime.date(y, mo, last) + datetime.timedelta(days=1)) if end_day > last else datetime.date(y, mo, end_day)
        weeks.append({
            'key': f'W{i}',
            'start': f'{y:04d}-{mo:02d}-{day:02d}',
            'end_exclusive': end_exclusive.isoformat(),
            'from_day': day,
            'to_day': (starts[i] - 1) if i < 4 else last,
        })
    return weeks

def main():
    print('CRM login...')
    op = crm_session()
    pm = prev_month(MONTH)
    M = {}
    for k,(name,team,aid,grp) in CUR.items():
        d = grab(op, aid)
        d['cj'] = churn_pct(op, aid, pm)   # iyun churn — trend uchun
        d.update(name=name, team=team, grp=grp)
        M[k] = d
        print(f'  {name}: baza {d["b"]}, churn {d["chu"]}, aktiv {d["a"]}, cj {d["cj"]}')
    # umumiy/team (KPI hero uchun)
    alld = grab(op,'all'); ta = grab(op,TEAM_A_IDS); tb = grab(op,TEAM_B_IDS)
    alld['cj'] = churn_pct(op, 'all', pm)

    weeks_meta = month_weeks(MONTH)
    weeks = [(w['key'], w['end_exclusive'], weeks_meta[0]['start']) for w in weeks_meta]
    id2key = {v[2]:k for k,v in CUR.items()}
    wk_all = weekly([v[2] for v in CUR.values()], weeks)
    if wk_all:
        W = {}
        for aid,key in id2key.items():
            wd = wk_all.get(aid)
            W[key] = {w:wd.get(w,[0,0,0]) for w in ['W1','W2','W3','W4']} if wd else None
        # umumiy hafta = yig'indi
        W['all'] = {w:[sum(wk_all.get(a,{}).get(w,[0,0,0])[i] for a in wk_all) for i in range(3)] for w in ['W1','W2','W3','W4']}
    else:
        # MCP vaqtincha ishlamasa, saytdagi oxirgi to'g'ri haftalik tarixni o'chirmaymiz.
        W = {}
        try:
            old = (BASE/'kurator.html').read_text()
            match = re.search(r'const __DATA__=(\{.*?\});', old)
            W = json.loads(match.group(1)).get('W',{}) if match else {}
            print('MCP bo‘sh: oldingi W saqlandi')
        except Exception as e:
            print('Oldingi W ham olinmadi:', e)
        for key in CUR:
            W.setdefault(key, None)
        W.setdefault('all', {w:[0,0,0] for w in ['W1','W2','W3','W4']})

    # Noaktiv qoldig'i: joriy CRM snapshotidan orqaga, haftalik sof o'zgarishlar orqali.
    net = weekly_noaktiv_net([v[2] for v in CUR.values()], weeks_meta)
    N = {}
    for key,(_,_,aid,_) in CUR.items():
        current = int(M[key]['x'][0])
        vals = {}
        cursor = current
        for wk in reversed(['W1','W2','W3','W4']):
            vals[wk] = {'count': cursor, 'delta': int(net.get(aid,{}).get(wk,0))}
            cursor -= vals[wk]['delta']
        N[key] = vals
    N['all'] = {}
    for wk in ['W1','W2','W3','W4']:
        N['all'][wk] = {
            'count': sum(N[k][wk]['count'] for k in CUR),
            'delta': sum(N[k][wk]['delta'] for k in CUR),
        }

    # Filtrga bog'liq yangi/faollashtirilgan va oylik qarzdorlik rejasi.
    events = period_kpis([v[2] for v in CUR.values()], weeks_meta)
    debt_plan = monthly_debtor_plan([v[2] for v in CUR.values()], MONTH)
    db_students = current_student_counts([v[2] for v in CUR.values()])
    direct_churn = monthly_churn_counts(
        [v[2] for v in CUR.values()], MONTH, weeks_meta[-1]['end_exclusive']
    )
    E = {}
    for key,(_,_,aid,_) in CUR.items():
        wk_data = {wk:events.get(aid,{}).get(wk,{'yangi':0,'fao':0}) for wk in ['W1','W2','W3','W4']}
        E[key] = {
            'weeks': wk_data,
            'month': {
                'yangi': events.get(aid,{}).get('ALL',{}).get('yangi',0),
                'fao': events.get(aid,{}).get('ALL',{}).get('fao',0),
                'qarz_plan': debt_plan.get(aid,0),
            }
        }
        M[key]['yangi'] = E[key]['month']['yangi']
        M[key]['qayta'] = E[key]['month']['fao']
        M[key]['qarz_plan'] = E[key]['month']['qarz_plan']
        M[key]['db_students'] = db_students.get(aid,0)
        M[key]['db_churn'] = direct_churn.get(aid,0)

    C = {'curators':{},'teams':{'A':{'count':0,'base':0},'B':{'count':0,'base':0}}}
    for key,(_,team,aid,_) in CUR.items():
        cnt,base = direct_churn.get(aid,0),db_students.get(aid,0)
        C['curators'][key] = {'count':cnt,'base':base,'pct':round(cnt/base*100,2) if base else 0}
        C['teams'][team]['count'] += cnt
        C['teams'][team]['base'] += base
    for team in ('A','B'):
        x=C['teams'][team]
        x['pct']=round(x['count']/x['base']*100,2) if x['base'] else 0
    C['all']={
        'count':C['teams']['A']['count']+C['teams']['B']['count'],
        'base':C['teams']['A']['base']+C['teams']['B']['base'],
    }
    C['all']['pct']=round(C['all']['count']/C['all']['base']*100,2) if C['all']['base'] else 0

    today = TASHKENT_NOW.strftime('%Y-%m-%d')
    snapshots = old_snapshots()
    snapshots[today] = {
        'total': sum(db_students.values()),
        'curators': {key:db_students.get(aid,0) for key,(_,_,aid,_) in CUR.items()}
    }
    # Iyulning yo'qolgan boshlang'ich nuqtasini foydalanuvchi bergan raqamlar bilan tiklaymiz.
    snapshots.setdefault(JULY_BASELINE['date'], {
        'total': JULY_BASELINE['total'], 'curators': JULY_BASELINE['curators'],
        'manual': True, 'note': JULY_BASELINE['note']
    })

    payload = {'M':M, 'W':W, 'N':N, 'E':E, 'C':C, 'snapshots':snapshots, 'weeks':weeks_meta, 'all':alld, 'TA':ta, 'TB':tb,
               'total_base': alld['b'], 'churn': alld['chu'], 'fao': alld['fao'],
               'qarz': grab.__self__ if False else None}
    payload['qarz_total'] = api(op,'top-cards/debtors-student-card','all')
    payload['month'] = MONTH
    render_and_deploy(payload)

def real_date():
    import email.utils, datetime
    for host in ('https://api.github.com', 'https://www.google.com', 'https://crm.junior-it.uz'):
        try:
            req = urllib.request.Request(host, method='HEAD')
            dt = urllib.request.urlopen(req, timeout=25).headers['Date']
            u = email.utils.parsedate_to_datetime(dt)
            tk = u + datetime.timedelta(hours=5)  # Toshkent UTC+5
            return tk.strftime('%d.%m.%Y')
        except Exception:
            continue
    return MONTH

def render_and_deploy(d):
    tpl = (BASE/'kurator_template.html').read_text()
    data = {k:d[k] for k in ('M','W','N','E','C','snapshots','weeks','all','TA','TB')}
    data['snap'] = real_date()
    data['month'] = MONTH
    js = ("const __DATA__=" + json.dumps(data, ensure_ascii=False) + ";")
    html = tpl.replace('/*__DATA__*/', js)
    (BASE/'kurator.html').write_text(html)
    print('kurator.html yozildi:', len(html), 'belgi')
    if CI:
        print('CI: git commit workflow tomonidan qilinadi (API deploy o\'tkazib yuborildi)')
    else:
        deploy(html)

def deploy(html):
    token = (HOME/'.github_token').read_text().strip()
    api_url = f'https://api.github.com/repos/{REPO}/contents/kurator.html'
    hdr = {'Authorization':f'Bearer {token}','Accept':'application/vnd.github+json','User-Agent':'kurator-bot'}
    sha=None
    try:
        req=urllib.request.Request(api_url,headers=hdr)
        sha=json.load(urllib.request.urlopen(req,timeout=40))['sha']
    except Exception: pass
    payload={'message':f'Kurator tahlili auto-update ({MONTH})','content':base64.b64encode(html.encode()).decode()}
    if sha: payload['sha']=sha
    req=urllib.request.Request(api_url,json.dumps(payload).encode(),{**hdr,'Content-Type':'application/json'},method='PUT')
    res=json.load(urllib.request.urlopen(req,timeout=40))
    print('Deploy OK:', res['commit']['sha'][:8])

if __name__=='__main__':
    main()
