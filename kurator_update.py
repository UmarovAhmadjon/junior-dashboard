#!/usr/bin/env python3
"""Kurator Tahlili — CRM Analitika modulidan jonli ma'lumot yig'ib, kurator.html ni yangilaydi.
Manba: crm.junior-it.uz Analitika AJAX API. Login: .crm_login (phone / pass).
Haftalik churn hodisalari: junior-lms MCP (student_status_logs).
Deploy: GitHub Pages (kurator.html)."""
import os, re, json, base64, pathlib, urllib.request, urllib.parse, http.cookiejar, time, datetime, calendar

HOME = pathlib.Path.home() / 'junior-dashboard'
REPO = 'UmarovAhmadjon/junior-dashboard'
CRM = 'https://crm.junior-it.uz'
MCP = os.environ.get('JUNIOR_MCP_GATEWAY') or 'https://myclinic.agc.uz/new_junior_mcp.php'
TASHKENT_NOW = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=5)))
MONTH = os.environ.get('KURATOR_MONTH', TASHKENT_NOW.strftime('%Y-%m-01'))

# admin_id lar (CRM admin-select)
CUR = {
 'Fot':('Fotimabonu Abdulkhakova','A','13799',5), 'Mad':('Madina Normatova','B','16005',5),
 'Dil':('Dilafruz Shokirova','A','14241',6), 'Jas':('Jasmina Tolibova','B','14974',4),
 'Shx':('Shaxlo Ziyodova','A','21463',3),
 'Mar':('Marjona Pardayeva','B','14451',5), 'Xal':('Xalima Ismoiljonova','B','16386',6),
}
JULY_BASELINE = {
    'date':'2026-07-01', 'total':2127,
    'curators': {
        'Fot':171, 'Dil':280, 'Mad':212, 'Jas':250, 'Nai':187,
        'Mun':171, 'Mar':238, 'Xal':246, 'Azi':175, 'Sab':197,
    },
    'note':'Foydalanuvchi bergan 01.07.2026 ro‘yxati; eski Munisa → Naima, Maryam → Munisa. New kurator olib tashlangan.'
}
AUGUST_BASELINE = {
    'date':'2026-08-01', 'total':2135,
    'note':'Foydalanuvchi tasdiqlagan 01.08.2026 umumiy baza. Kurator kesimi uchun oyning eng erta avtomatik snapshoti ishlatiladi.'
}
TEAM_A_IDS = '13799,14241,21463'
TEAM_B_IDS = '14451,16386,14974,16005'

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
        'churn_frozen': p_int(g('left-students-cards/frozen-left-card')),
        'churn_archive': p_int(g('left-students-cards/center-invite-left-card')),
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
            if isinstance(rows, list) and rows:
                return rows
            if rows == 'empty' or rows == []:
                return []
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
    """Oy/hafta bo'yicha to'liq to'lovdan so'ng aktivlashgan yangi va qayta faollashganlar."""
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
        f"0 yangi, "
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

    # Yangi: o'quvchining birinchi oylik obunasi tanlangan davrda boshlangan,
    # hozir aktiv guruhga biriktirilgan va new_student to'lovlari tarif narxini
    # to'liq qoplagan bo'lishi kerak. Sinov/demo obunalari bu hisobga kirmaydi.
    wk_case = " ".join([f"WHEN f.activated_at<'{w['end_exclusive']}' THEN '{w['key']}'" for w in weeks[:-1]])
    paid_new_base = (
        f"FROM (SELECT s.STUDENT_ID,MIN(g.ADMIN_ID) admin FROM subscribe_list s "
        f"JOIN group_list g ON g.ID=s.GROUP_ID WHERE g.ADMIN_ID IN ({ids}) "
        f"AND g.STATUS='active' AND LOWER(g.NAME) NOT LIKE '%test%' "
        f"AND s.ACTIVE=1 AND s.STATUS='active' AND s.TYPE='monthly' GROUP BY s.STUDENT_ID) t "
        f"JOIN (SELECT STUDENT_ID,MIN(START_DATE) activated_at,"
        f"MIN(CASE WHEN SPECIAL_PRICE>0 THEN SPECIAL_PRICE END) required_amount "
        f"FROM subscribe_list WHERE TYPE='monthly' GROUP BY STUDENT_ID) f ON f.STUDENT_ID=t.STUDENT_ID "
        f"JOIN (SELECT STUDENT_ID,MIN(TRANSACTION_DATE) first_paid_at,SUM(AMOUNT) paid_amount FROM transaction_list "
        f"WHERE ACTION_TYPE='add' AND STUDENT_TYPE='new_student' GROUP BY STUDENT_ID) p ON p.STUDENT_ID=t.STUDENT_ID "
        f"WHERE f.activated_at>='{weeks[0]['start']}' AND f.activated_at<'{weeks[-1]['end_exclusive']}' "
        f"AND p.first_paid_at>='{weeks[0]['start']}' AND p.first_paid_at<'{weeks[-1]['end_exclusive']}' "
        f"AND p.paid_amount>=COALESCE(f.required_amount,0) "
    )
    paid_sql = (
        f"SELECT t.admin,CASE {wk_case} ELSE '{last}' END wk,COUNT(DISTINCT t.STUDENT_ID) yangi "
        f"{paid_new_base} GROUP BY t.admin,wk UNION ALL "
        f"SELECT t.admin,'ALL' wk,COUNT(DISTINCT t.STUDENT_ID) yangi {paid_new_base} GROUP BY t.admin"
    )
    for r in mcp(paid_sql):
        slot = out.setdefault(str(r['admin']), {}).setdefault(r['wk'], {'yangi':0,'fao':0})
        slot['yangi'] = int(r['yangi'] or 0)
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
        f"WHERE g.ADMIN_ID IN ({ids}) AND g.STATUS='active' "
        f"AND LOWER(g.NAME) NOT LIKE '%test%' AND s.ACTIVE=1 "
        f"AND s.STATUS='active' AND s.TYPE='monthly' "
        f"GROUP BY g.ADMIN_ID"
    )
    return {str(r['admin']):int(r['students']) for r in mcp(sql)}

def current_new_counts(admin_ids_list):
    """Yangi = oxirgi statusi new, lekin guruh kartasida Holat Aktiv/Oylik; demo kirmaydi."""
    ids = ','.join(admin_ids_list)
    sql = (
        f"SELECT g.ADMIN_ID admin,COUNT(DISTINCT s.STUDENT_ID) yangi "
        f"FROM subscribe_list s JOIN group_list g ON g.ID=s.GROUP_ID "
        f"JOIN student_status_logs l ON l.id=(SELECT MAX(l2.id) FROM student_status_logs l2 "
        f"WHERE l2.student_id=s.STUDENT_ID) "
        f"WHERE g.ADMIN_ID IN ({ids}) AND g.STATUS='active' "
        f"AND LOWER(g.NAME) NOT LIKE '%test%' AND s.ACTIVE=1 "
        f"AND s.STATUS='active' AND s.TYPE='monthly' AND l.to_status='new' "
        f"GROUP BY g.ADMIN_ID"
    )
    return {str(r['admin']):int(r['yangi']) for r in mcp(sql)}

def module_progress(admin_ids_list):
    """Amaldagi o'quvchilarning asosiy onlayn kursidagi eng oxirgi ko'rilgan moduli."""
    ids = ','.join(admin_ids_list)
    students = (
        f"(SELECT s.STUDENT_ID user_id,MIN(g.ADMIN_ID) admin FROM subscribe_list s "
        f"JOIN group_list g ON g.ID=s.GROUP_ID WHERE s.ACTIVE=1 AND s.STATUS='active' "
        f"AND g.ADMIN_ID IN ({ids}) GROUP BY s.STUDENT_ID)"
    )
    progress = (
        "(SELECT lp.user_id,sl.course_id,MAX(sm.`order`) module_order "
        "FROM lesson_progress lp JOIN student_lessons sl ON sl.id=lp.lesson_id AND sl.status='active' "
        "JOIN student_modules sm ON sm.id=sl.module_id AND sm.status='active' "
        "WHERE lp.watched=1 GROUP BY lp.user_id,sl.course_id)"
    )
    rows_sql = (
        f"SELECT t.admin,p.course_id,sc.name course_name,p.module_order,sm.title module_name,"
        f"COUNT(DISTINCT p.user_id) students FROM {students} t JOIN {progress} p ON p.user_id=t.user_id "
        f"JOIN student_course_access ca ON ca.user_id=p.user_id AND ca.course_id=p.course_id AND ca.type='main' "
        f"JOIN student_courses sc ON sc.id=p.course_id "
        f"JOIN student_modules sm ON sm.course_id=p.course_id AND sm.`order`=p.module_order AND sm.status='active' "
        f"GROUP BY t.admin,p.course_id,sc.name,p.module_order,sm.title "
        f"ORDER BY sc.name,p.module_order,t.admin"
    )
    coverage_sql = (
        f"SELECT t.admin,COUNT(DISTINCT p.user_id) linked FROM {students} t "
        f"JOIN {progress} p ON p.user_id=t.user_id "
        f"JOIN student_course_access ca ON ca.user_id=p.user_id AND ca.course_id=p.course_id AND ca.type='main' "
        f"GROUP BY t.admin"
    )
    return mcp(rows_sql), {str(r['admin']):int(r['linked']) for r in mcp(coverage_sql)}

def group_attendance(admin_ids_list, month):
    """Asosiy kurs guruhlari bo'yicha oylik, shaxssizlantirilgan davomat matritsasi."""
    ids = ','.join(admin_ids_list)
    sql = (
        f"SELECT g.ID group_id,g.NAME group_name,g.ADMIN_ID admin,g.START_DATE start_date,"
        f"g.COURSE_STUDENT_ID course_ids,COALESCE(sc.students,0) students,gls.data "
        f"FROM group_list g "
        f"LEFT JOIN (SELECT GROUP_ID,COUNT(DISTINCT STUDENT_ID) students FROM subscribe_list "
        f"WHERE ACTIVE=1 AND STATUS='active' GROUP BY GROUP_ID) sc ON sc.GROUP_ID=g.ID "
        f"LEFT JOIN group_lesson_statuses gls ON gls.id=("
        f"SELECT MAX(x.id) FROM group_lesson_statuses x WHERE x.group_id=g.ID) "
        f"WHERE g.STATUS='active' AND g.ADMIN_ID IN ({ids}) AND COALESCE(sc.students,0)>0 "
        f"ORDER BY g.ADMIN_ID,g.NAME"
    )
    course_rows = mcp("SELECT id,name FROM student_courses WHERE status='active'")
    course_names = {str(r['id']):r['name'].strip() for r in course_rows}
    y, mo = map(int, month[:7].split('-'))
    out = []
    for r in mcp(sql):
        name = (r.get('group_name') or '').strip()
        low = name.lower()
        family = ('Junior' if 'junior' in low else 'Kids' if 'kid' in low else
                  'Senior' if 'senior' in low else 'Dizayn' if 'dizayn' in low or 'design' in low
                  else (name.split()[0].title() if name else 'Boshqa'))
        ids_list = [x.strip() for x in str(r.get('course_ids') or '').split(',') if x.strip()]
        tracks = [course_names[x] for x in ids_list if x in course_names and
                  not any(skip in course_names[x].lower() for skip in ('english','matematika','typing'))]
        if not tracks:
            tracks = [course_names[x] for x in ids_list if x in course_names]
        try:
            raw = json.loads(r.get('data') or '{}')
        except Exception:
            raw = {}
        days = {}
        totals = {'active':0,'passive':0,'noactive':0,'new':0,'total':0}
        for date_s, students in raw.items():
            if not date_s.startswith(month[:7]) or not isinstance(students,dict):
                continue
            counts = {'active':0,'passive':0,'noactive':0,'new':0,'total':0}
            for code in students.values():
                field = {'4':'active','2':'passive','3':'noactive','1':'new'}.get(str(code))
                if field:
                    counts[field] += 1
                    counts['total'] += 1
            if counts['total']:
                day = int(date_s[-2:])
                counts['pct'] = round(counts['active']*100/counts['total'],1)
                days[str(day)] = counts
                for f in totals:
                    totals[f] += counts[f]
        totals['pct'] = round(totals['active']*100/totals['total'],1) if totals['total'] else 0
        start = str(r.get('start_date') or '')[:10]
        try:
            sy, sm = map(int,start[:7].split('-'))
            stage = max(1,(y-sy)*12+mo-sm+1)
        except Exception:
            stage = 1
        out.append({
            'id':int(r['group_id']),'name':name,'family':family,'k_admin':str(r['admin']),
            'students':int(r.get('students') or 0),'tracks':tracks,'stage':stage,
            'days':days,'totals':totals
        })
    return out

def status_churn_counts(admin_ids_list, weeks):
    """Churn = davrda muzlatilib hozir frozen turgan + davrda terminal archive bo'lgan."""
    ids = ','.join(admin_ids_list)
    when = " ".join([f"WHEN x.event_date<'{w['end_exclusive']}' THEN '{w['key']}'" for w in weeks[:-1]])
    last = weeks[-1]['key']
    sql = (
        f"SELECT x.admin,CASE {when} ELSE '{last}' END wk,x.kind,"
        f"COUNT(DISTINCT x.student_id) students FROM ("
        f"SELECT g.ADMIN_ID admin,s.STUDENT_ID student_id,'frozen' kind,f.START_DATE event_date "
        f"FROM frozen_student_list f JOIN subscribe_list s ON s.STUDENT_ID=f.STUDENT_ID "
        f"AND s.ACTIVE=1 AND s.STATUS='freezed' JOIN group_list g ON g.ID=s.GROUP_ID "
        f"WHERE g.ADMIN_ID IN ({ids}) AND f.START_DATE>='{weeks[0]['start']}' "
        f"AND f.START_DATE<'{weeks[-1]['end_exclusive']}' "
        f"AND EXISTS (SELECT 1 FROM transaction_list p WHERE p.STUDENT_ID=s.STUDENT_ID "
        f"AND p.ACTION_TYPE='add' AND p.TRANSACTION_DATE<=f.START_DATE GROUP BY p.STUDENT_ID "
        f"HAVING SUM(p.AMOUNT)>=COALESCE(NULLIF(s.SPECIAL_PRICE,0),1)) "
        f"AND NOT EXISTS (SELECT 1 FROM subscribe_list d WHERE d.STUDENT_ID=s.STUDENT_ID "
        f"AND d.ACTIVE=1 AND d.STATUS='demo') "
        f"UNION ALL "
        f"SELECT g.ADMIN_ID admin,s.STUDENT_ID student_id,'archive' kind,s.END_DATE event_date "
        f"FROM subscribe_list s JOIN group_list g ON g.ID=s.GROUP_ID "
        f"WHERE g.ADMIN_ID IN ({ids}) AND s.STATUS='archive' AND s.END_DATE>='{weeks[0]['start']}' "
        f"AND s.END_DATE<'{weeks[-1]['end_exclusive']}' "
        f"AND (s.END_OF_SUBSCRIPTION IS NULL OR s.END_OF_SUBSCRIPTION='0000-00-00 00:00:00' "
        f"OR DATE(s.END_DATE)<DATE(s.END_OF_SUBSCRIPTION)) "
        f"AND NOT EXISTS (SELECT 1 FROM subscribe_list a JOIN group_list ga ON ga.ID=a.GROUP_ID "
        f"WHERE a.STUDENT_ID=s.STUDENT_ID AND ga.ADMIN_ID=g.ADMIN_ID "
        f"AND a.ACTIVE=1 AND a.STATUS IN ('active','freezed','demo'))"
        f") x GROUP BY x.admin,wk,x.kind"
    )
    out = {}
    for r in mcp(sql):
        aid,wk,kind=str(r['admin']),r['wk'],r['kind']
        out.setdefault(aid,{}).setdefault(wk,{'frozen':0,'archive':0})
        out[aid][wk][kind]=int(r['students'])
    return out

def churn_student_rows(admin_ids_list, weeks):
    """Churn sonini tashkil qilgan o'quvchilar: har o'quvchining eng so'nggi churn hodisasi."""
    ids = ','.join(admin_ids_list)
    sql = (
        "SELECT x.admin,x.student_id,st.NAME name,x.kind,x.event_date FROM ("
        f"SELECT g.ADMIN_ID admin,s.STUDENT_ID student_id,'frozen' kind,f.START_DATE event_date "
        f"FROM frozen_student_list f JOIN subscribe_list s ON s.STUDENT_ID=f.STUDENT_ID "
        f"AND s.ACTIVE=1 AND s.STATUS='freezed' JOIN group_list g ON g.ID=s.GROUP_ID "
        f"WHERE g.ADMIN_ID IN ({ids}) AND f.START_DATE>='{weeks[0]['start']}' "
        f"AND f.START_DATE<'{weeks[-1]['end_exclusive']}' "
        f"AND EXISTS (SELECT 1 FROM transaction_list p WHERE p.STUDENT_ID=s.STUDENT_ID "
        f"AND p.ACTION_TYPE='add' AND p.TRANSACTION_DATE<=f.START_DATE GROUP BY p.STUDENT_ID "
        f"HAVING SUM(p.AMOUNT)>=COALESCE(NULLIF(s.SPECIAL_PRICE,0),1)) "
        f"AND NOT EXISTS (SELECT 1 FROM subscribe_list d WHERE d.STUDENT_ID=s.STUDENT_ID "
        f"AND d.ACTIVE=1 AND d.STATUS='demo') UNION ALL "
        f"SELECT g.ADMIN_ID admin,s.STUDENT_ID student_id,'archive' kind,s.END_DATE event_date "
        f"FROM subscribe_list s JOIN group_list g ON g.ID=s.GROUP_ID "
        f"WHERE g.ADMIN_ID IN ({ids}) AND s.STATUS='archive' AND s.END_DATE>='{weeks[0]['start']}' "
        f"AND s.END_DATE<'{weeks[-1]['end_exclusive']}' "
        f"AND (s.END_OF_SUBSCRIPTION IS NULL OR s.END_OF_SUBSCRIPTION='0000-00-00 00:00:00' "
        f"OR DATE(s.END_DATE)<DATE(s.END_OF_SUBSCRIPTION)) "
        f"AND NOT EXISTS (SELECT 1 FROM subscribe_list a JOIN group_list ga ON ga.ID=a.GROUP_ID "
        f"WHERE a.STUDENT_ID=s.STUDENT_ID AND ga.ADMIN_ID=g.ADMIN_ID "
        f"AND a.ACTIVE=1 AND a.STATUS IN ('active','freezed','demo'))"
        ") x JOIN student_list st ON st.ID=x.student_id ORDER BY x.event_date DESC"
    )
    latest = {}
    for r in mcp(sql):
        key = int(r['student_id'])
        date = str(r.get('event_date') or '')[:10]
        if key not in latest or date > latest[key]['date']:
            week = next((w['key'] for w in weeks if w['start'] <= date < w['end_exclusive']), weeks[-1]['key'])
            latest[key] = {'admin':str(r['admin']), 'id':int(r['student_id']), 'name':r.get('name') or f"O‘quvchi #{r['student_id']}",
                           'kind':r['kind'], 'date':date, 'wk':week}
    return {(row['admin'], sid):{k:v for k,v in row.items() if k!='admin'} for sid,row in latest.items()}

def old_snapshots():
    try:
        old = (BASE/'kurator.html').read_text()
        match = re.search(r'const __DATA__=(\{.*?\});', old)
        return json.loads(match.group(1)).get('snapshots',{}) if match else {}
    except Exception:
        return {}

def month_weeks(month):
    """Oy ichidagi davrlarni yakshanba–shanba kalendar haftalariga ajratadi."""
    y, mo = map(int, month[:7].split('-'))
    last = calendar.monthrange(y, mo)[1]
    weeks = []
    month_start = datetime.date(y, mo, 1)
    month_end = datetime.date(y, mo, last)
    # Haftalik filtrlar oyning birinchi yakshanbasidan boshlanadi. Oyning undan
    # oldingi kunlari faqat "oy boshidan" kesimiga kiradi. Oxirgi hafta oy
    # oxirigacha uzayadi (masalan Avgust 2026: 02–08, 09–15, 16–22, 23–31).
    first_sunday = month_start + datetime.timedelta(days=(6 - month_start.weekday()) % 7)
    for i in range(1, 5):
        cursor = first_sunday + datetime.timedelta(days=(i - 1) * 7)
        if cursor > month_end:
            break
        end = month_end if i == 4 else min(month_end, cursor + datetime.timedelta(days=6))
        weeks.append({
            'key': f'W{i}',
            'start': cursor.isoformat(),
            'end_exclusive': (end + datetime.timedelta(days=1)).isoformat(),
            'from_day': cursor.day,
            'to_day': end.day,
            'from_label': cursor.strftime('%d.%m'),
            'to_label': end.strftime('%d.%m'),
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
    week_keys = [w['key'] for w in weeks_meta]
    weeks = [(w['key'], w['end_exclusive'], weeks_meta[0]['start']) for w in weeks_meta]
    id2key = {v[2]:k for k,v in CUR.items()}
    wk_all = weekly([v[2] for v in CUR.values()], weeks)
    if wk_all:
        W = {}
        for aid,key in id2key.items():
            wd = wk_all.get(aid)
            W[key] = {w:wd.get(w,[0,0,0]) for w in week_keys} if wd else None
        # umumiy hafta = yig'indi
        W['all'] = {w:[sum(wk_all.get(a,{}).get(w,[0,0,0])[i] for a in wk_all) for i in range(3)] for w in week_keys}
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
        W.setdefault('all', {w:[0,0,0] for w in week_keys})

    # Noaktiv qoldig'i: joriy CRM snapshotidan orqaga, haftalik sof o'zgarishlar orqali.
    net = weekly_noaktiv_net([v[2] for v in CUR.values()], weeks_meta)
    N = {}
    for key,(_,_,aid,_) in CUR.items():
        current = int(M[key]['x'][0])
        vals = {}
        cursor = current
        for wk in reversed(week_keys):
            vals[wk] = {'count': cursor, 'delta': int(net.get(aid,{}).get(wk,0))}
            cursor -= vals[wk]['delta']
        N[key] = vals
    N['all'] = {}
    for wk in week_keys:
        N['all'][wk] = {
            'count': sum(N[k][wk]['count'] for k in CUR),
            'delta': sum(N[k][wk]['delta'] for k in CUR),
        }

    # Filtrga bog'liq yangi/faollashtirilgan va oylik qarzdorlik rejasi.
    events = period_kpis([v[2] for v in CUR.values()], weeks_meta)
    debt_plan = monthly_debtor_plan([v[2] for v in CUR.values()], MONTH)
    db_students = current_student_counts([v[2] for v in CUR.values()])
    # MCP bo'sh yoki qisman javob qaytarsa 0/1 bazali buzilgan saytni yozmaymiz.
    expected_admins = {v[2] for v in CUR.values()}
    missing_admins = sorted(expected_admins - set(db_students))
    implausible = {aid:n for aid,n in db_students.items() if n < 50 or n > 1000}
    if missing_admins or implausible:
        raise RuntimeError(
            f"MCP student bazasi yaroqsiz; missing={missing_admins}, implausible={implausible}. "
            "Oxirgi to'g'ri sayt saqlanadi."
        )
    db_new = current_new_counts([v[2] for v in CUR.values()])
    churn_status = status_churn_counts([v[2] for v in CUR.values()], weeks_meta)
    churn_students = churn_student_rows([v[2] for v in CUR.values()], weeks_meta)
    attendance_groups = group_attendance([v[2] for v in CUR.values()], MONTH)
    E = {}
    for key,(_,_,aid,_) in CUR.items():
        wk_data = {wk:events.get(aid,{}).get(wk,{'yangi':0,'fao':0}) for wk in week_keys}
        E[key] = {
            'weeks': wk_data,
            'month': {
                'yangi': events.get(aid,{}).get('ALL',{}).get('yangi',0),
                'fao': events.get(aid,{}).get('ALL',{}).get('fao',0),
                'qarz_plan': debt_plan.get(aid,0),
            }
        }
        M[key]['yangi'] = db_new.get(aid,0)
        M[key]['qayta'] = E[key]['month']['fao']
        M[key]['qarz_plan'] = E[key]['month']['qarz_plan']
        M[key]['db_students'] = db_students.get(aid,0)
        wk_churn = {}
        for wk in week_keys:
            parts=churn_status.get(aid,{}).get(wk,{'frozen':0,'archive':0})
            wk_churn[wk]={'frozen':parts['frozen'],'archive':parts['archive'],
                          'count':parts['frozen']+parts['archive']}
        M[key]['db_churn'] = sum(x['count'] for x in wk_churn.values())

    H={'curators':{},'teams':{'A':{},'B':{}},'all':{}}
    C = {'curators':{},'teams':{},'all':{}}
    for key,(_,team,aid,_) in CUR.items():
        H['curators'][key]={}
        for wk in week_keys:
            p=churn_status.get(aid,{}).get(wk,{'frozen':0,'archive':0})
            H['curators'][key][wk]={'frozen':p['frozen'],'archive':p['archive'],'count':p['frozen']+p['archive']}
        frozen=sum(x['frozen'] for x in H['curators'][key].values())
        archive=sum(x['archive'] for x in H['curators'][key].values())
        cnt,base = frozen+archive,db_students.get(aid,0)
        # Kurator oy kesimi CRM Analitika tarkibi: Muzlatildi + Ketdi/Arxiv.
        # Faqat fakt ro'yxatlari: Muzlatildi + Ketdi. plan-card Fakt soni ayrim
        # curatorlarda ro'yxatda yo'q yozuvlarni qo'shadi, shuning uchun KPIga kirmaydi.
        frozen=int(M[key]['churn_frozen']); archive=int(M[key]['churn_archive'])
        cnt,base=frozen+archive,int(M[key]['b'])
        C['curators'][key] = {'count':cnt,'frozen':frozen,'archive':archive,'base':base,
                              'pct':round(cnt*100/base,2) if base else 0}
        for wk in week_keys:
            dst=H['teams'][team].setdefault(wk,{'count':0,'frozen':0,'archive':0})
            for f in ('count','frozen','archive'): dst[f]+=H['curators'][key][wk][f]
    # Team churn CRM'ning tarkib kartalaridan olinadi. Bu Team churn kartasidagi
    # ko'chgan o'quvchi dublikatlarini yo'qotadi: A + B = Umumiy.
    for team,d in (('A',ta),('B',tb)):
        frozen=int(d['churn_frozen']); archive=int(d['churn_archive'])
        cnt,base=frozen+archive,int(d['b'])
        C['teams'][team]={'count':cnt,'frozen':frozen,'archive':archive,
                          'base':base,'pct':round(cnt*100/base,2) if base else 0}
    official_frozen=int(alld['churn_frozen'])
    official_archive=int(alld['churn_archive'])
    official_count=official_frozen+official_archive
    official_base=int(alld['chu'][0] or alld['b']) if alld.get('chu') else int(alld['b'])
    C['all']={'count':official_count,'frozen':official_frozen,'archive':official_archive,
              'other':0,
              'base':official_base,'pct':round(official_count*100/official_base,2) if official_base else 0}
    for wk in week_keys:
        H['all'][wk]={f:H['teams']['A'][wk][f]+H['teams']['B'][wk][f] for f in ('count','frozen','archive')}

    admin_to_key = {aid:key for key,(_,_,aid,_) in CUR.items()}
    CL = {'curators':{key:[] for key in CUR}, 'teams':{'A':[],'B':[]}, 'all':[]}
    for (aid,_), row in churn_students.items():
        key = admin_to_key.get(aid)
        if not key:
            continue
        item = dict(row, k=key)
        CL['curators'][key].append(item)
        CL['teams'][CUR[key][1]].append(item)
        CL['all'].append(item)
    G = {'groups':[]}
    for r in attendance_groups:
        key = admin_to_key.get(r.pop('k_admin'))
        if key:
            r['k'] = key
            G['groups'].append(r)

    today = TASHKENT_NOW.strftime('%Y-%m-%d')
    snapshots = old_snapshots()
    snapshots[today] = {
        'total': sum(db_students.values()),
        'curators': {key:db_students.get(aid,0) for key,(_,_,aid,_) in CUR.items()},
        'group_attendance': {
            'groups': {str(g['id']):{'pct':g['totals']['pct'],'stage':g['stage']} for g in G['groups']}
        }
    }
    # Iyulning yo'qolgan boshlang'ich nuqtasini foydalanuvchi bergan raqamlar bilan tiklaymiz.
    snapshots.setdefault(JULY_BASELINE['date'], {
        'total': JULY_BASELINE['total'], 'curators': JULY_BASELINE['curators'],
        'manual': True, 'note': JULY_BASELINE['note']
    })
    # Avgustning haqiqiy oy-boshi umumiy soni foydalanuvchi tomonidan tasdiqlangan.
    # Kuratorlar kesimidagi tarixiy sonlar mavjud bo'lmagani uchun ular oyning eng
    # erta avtomatik snapshotidan olinadi; umumiy KPI esa aynan 2135 dan hisoblanadi.
    snapshots.setdefault(AUGUST_BASELINE['date'], {
        'total': AUGUST_BASELINE['total'],
        'manual': True, 'note': AUGUST_BASELINE['note']
    })

    payload = {'M':M, 'W':W, 'N':N, 'E':E, 'C':C, 'H':H, 'G':G, 'CL':CL, 'snapshots':snapshots, 'weeks':weeks_meta, 'all':alld, 'TA':ta, 'TB':tb,
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
    rating_tpl = (BASE/'kurator_rating_template.html').read_text()
    attendance_tpl = (BASE/'kurator_attendance_template.html').read_text()
    data = {k:d[k] for k in ('M','W','N','E','C','H','G','CL','snapshots','weeks','all','TA','TB')}
    data['snap'] = real_date()
    data['month'] = MONTH
    js = ("const __DATA__=" + json.dumps(data, ensure_ascii=False) + ";")
    # Reyting formati endi asosiy kurator sahifasi ham hisoblanadi.
    html = rating_tpl.replace('/*__DATA__*/', js)
    rating_html = rating_tpl.replace('/*__DATA__*/', js)
    attendance_html = attendance_tpl.replace('/*__DATA__*/', js)
    (BASE/'kurator.html').write_text(html)
    (BASE/'kurator-rating-test.html').write_text(rating_html)
    (BASE/'kurator-attendance-test.html').write_text(attendance_html)
    print('kurator.html yozildi:', len(html), 'belgi')
    print('kurator-rating-test.html yozildi:', len(rating_html), 'belgi')
    print('kurator-attendance-test.html yozildi:', len(attendance_html), 'belgi')
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
