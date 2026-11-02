import re, json, sys, time, html
import urllib.request, urllib.parse
from html.parser import HTMLParser
BASE="https://minerpa.com.pa"
def get(url, data=None):
    req=urllib.request.Request(url, data=data, headers={"User-Agent":"Mozilla/5.0"})
    return urllib.request.urlopen(req, timeout=60).read().decode("utf-8","replace")
home=get(BASE+"/")
# menu links (indicator pages) from nav
links=[]
for u in re.findall(r'href="(https://minerpa\.com\.pa/[a-z0-9\-]+/)"', home):
    if "/inicio/" in u or u.endswith("/contacto/") or u==BASE+"/": continue
    if u not in links: links.append(u)
print(len(links), file=sys.stderr)
out={}
for u in links:
    try:
        p=get(u)
    except Exception as e:
        print("ERR",u,e,file=sys.stderr); continue
    title=re.search(r'<h2[^>]*>(.*?)</h2>', p, re.S)
    nonce=re.search(r'name="wdtNonceFrontendEdit" value="([^"]*)', p)
    units=re.search(r'Las cantidades son expresadas en\s*(.*?)</p>', p, re.S)
    fuente=re.search(r'Fuente:</strong>\s*(.*?)</p>', p, re.S)
    tables=[]
    for m in re.finditer(r'<table[^>]*id="(table_\d+)"[^>]*data-wpdatatable_id="(\d+)"[^>]*>(.*?)</thead>', p, re.S):
        tid=m.group(2)
        heads=[html.unescape(re.sub('<[^>]+>','',h)).strip() for h in re.findall(r'<th[^>]*>(.*?)</th>', m.group(3), re.S)]
        # accordion section title preceding the table
        before=p[:m.start()]
        sec=re.findall(r'<h4[^>]*>\s*<a[^>]*>(.*?)</a>', before)
        try:
            d=json.loads(get(f"{BASE}/wp-admin/admin-ajax.php?action=get_wdtable&table_id={tid}",
                urllib.parse.urlencode({"draw":1,"start":0,"length":-1,"wdtNonce":nonce.group(1) if nonce else ""}).encode()))
            rows=d.get("data",[])
        except Exception as e:
            print("ERRT",u,tid,e,file=sys.stderr); rows=[]
        tables.append({"id":tid,"section":html.unescape(re.sub('<[^>]+>','',sec[-1])).strip() if sec else "","headers":heads,"rows":rows})
    clean=lambda s: html.unescape(re.sub(r'<[^>]+>','',s)).strip() if s else ""
    out[u]={"title":clean(title.group(1)) if title else "","units":clean(units.group(1)) if units else "","fuente":clean(fuente.group(1)) if fuente else "","tables":tables}
    print(u, len(tables), [len(t["rows"]) for t in tables], file=sys.stderr)
    time.sleep(0.3)
json.dump(out, open(sys.argv[1],"w"), ensure_ascii=False)
