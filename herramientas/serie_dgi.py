"""Arma la serie de recaudación DGI a partir de los JSON de cada edición.

Regla (decidida con Olmedo el 2026-11-06): para cada dato se muestra la cifra
de la edición más reciente que lo publica, con su condición:
  "o" oficial      (informe oficial anual, o mes marcado "oficial" en la nota)
  "r" revisada     (columna "año anterior" de un informe posterior)
  "p" preliminar   (el informe de ese mismo mes)
Nada se calcula ni se rellena: si ninguna edición trae el dato, queda vacío.
Las celdas que no cuadraron dentro de su informe (celdas_dudosas) no se usan.

Uso: python3 serie_dgi.py ../datos/dgi/ediciones ../datos/dgi
"""
import glob
import json
import os
import sys

CODIGOS = {
    "total_corrientes": "Ingresos corrientes",
    "tributarios": "Ingresos tributarios",
    "directos": "Impuestos directos",
    "indirectos": "Impuestos indirectos",
    "no_tributarios": "Ingresos no tributarios",
    "otros_corrientes": "Otros ingresos corrientes",
    "renta": "Impuesto sobre la renta",
    "renta_juridica": "Renta jurídica",
    "renta_natural": "Renta natural",
    "renta_dividendos_complementario": "Dividendos y complementario",
    "renta_otros": "Renta, otros",
    "inmuebles": "Inmuebles",
    "aviso_operacion": "Aviso de operación",
    "seguro_educativo": "Seguro educativo",
    "itbms_ventas": "ITBMS ventas",
    "itbms_importacion": "ITBMS importación",
    "importacion": "Impuesto de importación",
    "consumo_combustible": "Consumo de combustible",
    "selectivo_consumo": "Selectivo al consumo",
    "indirectos_otros": "Indirectos, otros",
    "tasas_derechos_otros": "Tasas, derechos y otros",
    "canal_peajes_servicios": "Peajes y servicios del Canal",
    "utilidades_aportes": "Participación de utilidades y aportes",
    "dividendos_canal": "Dividendos del Canal",
}
TIPO = {c: "resumen" for c in ("total_corrientes", "tributarios", "directos", "indirectos",
                                 "no_tributarios", "otros_corrientes")}
NOTRIB = ("tasas_derechos_otros", "canal_peajes_servicios", "utilidades_aportes", "dividendos_canal")


def cuadro(codigo, alcance):
    tipo = TIPO.get(codigo) or ("no_tributarios" if codigo in NOTRIB else "tributarios")
    return f"{tipo}_{alcance}_efectivo"


def rango(ed):
    """Orden de publicación aproximado de una edición: (año, mes, oficial)."""
    a, m = ed["anio"], ed["mes"]
    return (a + 1, 9.5, 1) if ed["condicion"] == "oficial" else (a, m, 0)


def valor(ed, codigo, alcance, columna):
    al = "mes" if ed["mes"] == 1 else alcance        # en enero mes = acumulado
    c = ed["cuadros"].get(cuadro(codigo, al))
    if not c:
        return None
    dud = {tuple(x) for x in ed.get("celdas_dudosas", [])}
    for f in c["filas"]:
        if f["codigo"] == codigo:
            if (cuadro(codigo, al), codigo, columna) in dud:
                return None
            return f[columna]
    return None


def main():
    src, dst = sys.argv[1], sys.argv[2]
    eds = [json.load(open(p, encoding="utf-8")) for p in sorted(glob.glob(os.path.join(src, "20*_*.json")))]
    eds = [e for e in eds if e.get("publicable") and e.get("periodo")]
    eds.sort(key=rango)
    periodos = sorted({e["periodo"] for e in eds} |
                      {f"{e['anio'] - 1}-{e['mes']:02d}" for e in eds if e["condicion"] != "oficial"})
    serie = {"unidad": "millones de balboas", "periodos": periodos, "codigos": CODIGOS,
             "regla": "cifra de la edición más reciente; o=oficial, r=revisada, p=preliminar",
             "datos": {}, "documentos_fiscales": {}, "presupuesto": {}}
    for p in periodos:
        a, m = map(int, p.split("-"))
        fila = {}
        for alc in ("mes", "acumulado"):
            for cod in CODIGOS:
                mejor = None      # (rango, valor, condición, edición)
                for e in eds:
                    if e["condicion"] == "oficial":
                        if (e["anio"], m) == (a, 12) and alc == "acumulado" and e["mes"] == 12:
                            v = valor(e, cod, "acumulado", "efectivo")
                            if v is not None and (mejor is None or rango(e) > mejor[0]):
                                mejor = (rango(e), v, "o", e["edicion"])
                        continue
                    if (e["anio"], e["mes"]) == (a, m):
                        v = valor(e, cod, alc, "efectivo")
                        cond = "p"
                    elif (e["anio"], e["mes"]) == (a + 1, m):
                        v = valor(e, cod, alc, "anio_anterior")
                        cond = "r"
                    else:
                        continue
                    if v is not None and (mejor is None or rango(e) > mejor[0]):
                        mejor = (rango(e), v, cond, e["edicion"])
                # total del mes: también lo trae el cuadro mensual de informes posteriores
                if cod == "total_corrientes" and alc == "mes":
                    for e in eds:
                        if e["anio"] != a:
                            continue
                        for x in (e.get("mensual") or {}).get("meses", []):
                            if x["mes"] == m and (mejor is None or rango(e) > mejor[0]):
                                cpm = (e.get("condicion_por_mes") or {}).get(str(m)) or \
                                    (e.get("condicion_por_mes") or {}).get(m)
                                cond = "o" if e["condicion"] == "oficial" or cpm == "oficial" else "p"
                                mejor = (rango(e), x["efectivo"], cond, e["edicion"])
                if mejor:
                    fila.setdefault(alc, {})[cod] = {"v": mejor[1], "c": mejor[2], "ed": mejor[3]}
        # documentos fiscales del mes: cuadro mensual más reciente del mismo año
        doc = None
        for e in eds:
            if e["anio"] == a:
                for x in (e.get("mensual") or {}).get("meses", []):
                    if x["mes"] == m and (doc is None or rango(e) > doc[0]):
                        doc = (rango(e), x["documentos_fiscales"], e["edicion"])
        if doc:
            serie["documentos_fiscales"][p] = {"v": doc[1], "ed": doc[2]}
        # presupuesto: solo del informe de ese mes
        own = [e for e in eds if (e["anio"], e["mes"]) == (a, m) and e["condicion"] != "oficial"]
        if own:
            e = own[-1]
            serie["presupuesto"][p] = {alc: {cod: valor(e, cod, alc, "presupuesto") for cod in CODIGOS
                                            if valor(e, cod, alc, "presupuesto") is not None}
                                       for alc in ("mes", "acumulado")}
            serie["presupuesto"][p]["ed"] = e["edicion"]
        serie["datos"][p] = fila
    serie["ediciones"] = [{"edicion": e["edicion"], "archivo": e["fuente"]["archivo"],
                           "url": e["fuente"]["url"], "sha256": e["fuente"]["sha256"],
                           "pdf_creado": e["fuente"]["pdf_creado"]} for e in eds]
    os.makedirs(dst, exist_ok=True)
    with open(os.path.join(dst, "serie.json"), "w", encoding="utf-8") as f:
        json.dump(serie, f, ensure_ascii=False, indent=1)
    # resumen compacto para index.html
    P = periodos
    res = {"p": P, "act": max(e["periodo"] for e in eds if e["condicion"] != "oficial"),
           "lab": {k: v for k, v in CODIGOS.items()}}
    for alc, k in (("mes", "m"), ("acumulado", "a")):
        res[k] = {cod: [serie["datos"][p].get(alc, {}).get(cod, {}).get("v") for p in P] for cod in CODIGOS}
        res[k + "c"] = [serie["datos"][p].get(alc, {}).get("tributarios", {}).get("c") for p in P]
    for alc, k in (("mes", "pm"), ("acumulado", "pa")):
        res[k] = {cod: [serie["presupuesto"].get(p, {}).get(alc, {}).get(cod) for p in P]
                  for cod in ("total_corrientes", "tributarios", "directos", "indirectos", "no_tributarios")}
    res["doc"] = [serie["documentos_fiscales"].get(p, {}).get("v") for p in P]
    with open(os.path.join(dst, "resumen.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, separators=(",", ":"))
    print(f"serie: {P[0]} a {P[-1]}, {len(eds)} ediciones")


if __name__ == "__main__":
    main()
