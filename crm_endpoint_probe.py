#!/usr/bin/env python3
import os, re, urllib.request, urllib.parse, http.cookiejar

CRM = "https://crm.junior-it.uz"
cj = http.cookiejar.CookieJar()
op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
op.addheaders = [("User-Agent", "Mozilla/5.0"), ("X-Requested-With", "XMLHttpRequest")]
op.open(CRM + "/account/", timeout=40).read()
op.open(CRM + "/account/", urllib.parse.urlencode({
    "phone": os.environ["CRM_PHONE"], "pass": os.environ["CRM_PASS"]
}).encode(), timeout=40).read()

html = op.open(CRM + "/account/main_page/list", timeout=40).read().decode(errors="ignore")
links = sorted(set(re.findall(r'''(?:href|src)=["']([^"']+)["']''', html)))
for value in links:
    if value.startswith(("/account/", "account/", CRM + "/account/")):
        print(value)
