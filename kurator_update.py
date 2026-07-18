#!/usr/bin/env python3
"""Kurator Tahlili — CRM Analitika modulidan jonli ma'lumot yig'ib, kurator.html ni yangilaydi.
Manba: crm.junior-it.uz Analitika AJAX API. Login: .crm_login (phone / pass).
Haftalik churn hodisalari: junior-lms MCP (student_status_logs).
Deploy: GitHub Pages (kurator.html)."""
import os, re, json, base64, pathlib, urllib.request, urllib.parse, http.cookiejar

HOME = pathlib.Path.home() / 'junior-dashboard'
REPO = 'UmarovAhmadjon/junior-dashboard'
CRM = 'https://crm.junior-it.uz'
MCP = 'https://myclinic.agc.uz/junior_mcp.php'
MONTH = os.environ.get('KURATOR_MONTH', '2026-07-01')  # oy boshidan

# admin_id lar (CRM admin-select)
CUR = {
 'Fot':('Fotimabonu Abdulkhakova','A','13799',6), 'Mad':('Madina Normatova','A','16005',5),
 'Dil':('Dilafruz Shokirova','A','14241',6), 'Jas':('Jasmina Tolibova','A','14974',4),
 'Dsh':('Dilshoda Parpiyeva','A','21511',4),
 'Mar':('Marjona Pardayeva','B','14451',5), 'Xal':('Xalima Ismoiljonova','B','16386',6),
 'Azi':('Aziza Qurvonaliyeva','B','17542',4), 'Sab':('Sabrina Salimova','B','18307',6),
 'Mrm':('Maryam Safarova','B','21186',5),
}
TEAM_A_IDS = '11274,13799,14241,14974,16005,16876,18784,21463,21511'
TEAM_B_IDS = '14451,16386,17542,18307,21186'

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
    try:
        r = json.load(urllib.request.urlopen(req, timeout=60))
        txt = r['result']['content'][0]['text']
        return json.loads(txt).get('data',{}).get('data',[])
    except Exception as e:
        print('MCP xato:', e); return []

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

    # haftalik (iyul: 4 hafta chegarasi)
    weeks = [('W1','2026-07-08','2026-07-01'),('W2','2026-07-15','2026-07-01'),('W3','2026-07-22','2026-07-01')]
    id2key = {v[2]:k for k,v in CUR.items()}
    wk_all = weekly([v[2] for v in CUR.values()], weeks)
    W = {}
    for aid,key in id2key.items():
        wd = wk_all.get(aid)
        W[key] = {w:wd.get(w,[0,0,0]) for w in ['W1','W2','W3']} if wd else None
    # umumiy hafta = yig'indi
    W['all'] = {w:[sum(wk_all.get(a,{}).get(w,[0,0,0])[i] for a in wk_all) for i in range(3)] for w in ['W1','W2','W3']}

    payload = {'M':M, 'W':W, 'all':alld, 'TA':ta, 'TB':tb,
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
    data = {k:d[k] for k in ('M','W','all','TA','TB')}
    data['snap'] = real_date()
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
