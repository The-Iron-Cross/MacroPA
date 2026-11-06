"""Copia datos/deuda/resumen.json dentro de index.html (constante DMEF).

Solo reemplaza el texto entre /*DEUDA_MEF*/ y /*FIN_DEUDA_MEF*/; el resto de la
página no se toca. Uso: python3 embeber_deuda.py ../datos/deuda/resumen.json ../index.html
"""
import json, re, sys

MES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]
MESL = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
        "septiembre", "octubre", "noviembre", "diciembre"]

r = json.load(open(sys.argv[1], encoding="utf-8"))
f = r["filas"]
etq = lambda p: f"{MES[int(p[5:]) - 1]}-{p[2:4]}"
larga = lambda p: f"{MESL[int(p[5:]) - 1]} {p[:4]}"
d = {"p": [x[0] for x in f], "x": [etq(x[0]) for x in f], "xl": [larga(x[0]) for x in f]}
for i, k in enumerate(["tot", "ext", "int", "des", "amo", "intr", "cpp", "cppe", "cppi"], start=1):
    d[k] = [x[i] for x in f]
d["est"] = [x[10] for x in f]
d["act"] = r["actualizado"]
txt = json.dumps(d, ensure_ascii=False, separators=(",", ":"))

p = open(sys.argv[2], encoding="utf-8").read()
nuevo, n = re.subn(r"/\*DEUDA_MEF\*/.*?/\*FIN_DEUDA_MEF\*/",
                   lambda m: "/*DEUDA_MEF*/" + txt + "/*FIN_DEUDA_MEF*/", p, flags=re.S)
if n != 1:
    sys.exit("no encontré las marcas /*DEUDA_MEF*/ en index.html")
if nuevo != p:
    open(sys.argv[2], "w", encoding="utf-8").write(nuevo)
    print("index.html actualizado:", d["p"][-1])
else:
    print("index.html ya estaba al día")
