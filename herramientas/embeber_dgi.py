"""Copia datos/dgi/resumen.json dentro de index.html (constante DGI).

Solo reemplaza el texto entre /*DGI_RECAUDACION*/ y /*FIN_DGI_RECAUDACION*/.
Uso: python3 embeber_dgi.py ../datos/dgi/resumen.json ../index.html
"""
import json, re, sys

r = json.load(open(sys.argv[1], encoding="utf-8"))
txt = json.dumps(r, ensure_ascii=False, separators=(",", ":"))
p = open(sys.argv[2], encoding="utf-8").read()
nuevo, n = re.subn(r"/\*DGI_RECAUDACION\*/.*?/\*FIN_DGI_RECAUDACION\*/",
                   lambda m: "/*DGI_RECAUDACION*/" + txt + "/*FIN_DGI_RECAUDACION*/", p, flags=re.S)
if n != 1:
    sys.exit("no encontré las marcas /*DGI_RECAUDACION*/ en index.html")
if nuevo != p:
    open(sys.argv[2], "w", encoding="utf-8").write(nuevo)
    print("index.html actualizado:", r["act"])
else:
    print("index.html ya estaba al día")
