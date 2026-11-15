#!/usr/bin/env python3
"""Extracción y transformación verificable de las tablas de MINERPA.

El módulo no escribe en ``index.html``. Descarga un catálogo cerrado, comprueba la
estructura mínima de cada tabla y devuelve las constantes D, B y DM. El orquestador
``actualizar_minerpa.py`` decide qué series se pueden incorporar y valida el candidato.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import html
import http.cookiejar
import json
import re
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://minerpa.com.pa"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0 Safari/537.36 MacroPA-datos/2.0"
OPENER = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

# Las cuatro series se mantienen fuera de la actualización MINERPA porque MacroPA
# las complementa con publicaciones directas más recientes del INEC.
D_PROTEGIDAS = {"pib_growth", "pib_nom", "pib_pc", "imae"}
D_AUTOMATICAS = {
    "inflacion", "sectores", "spnf", "gc", "deuda", "deuda_comp",
    "impuestos", "acp", "cc", "com", "ied", "ied_pct", "xserv",
    "participacion", "desempleo", "informalidad", "salario", "pobreza",
    "gini", "pob_prov", "pobx_prov", "pobm_prov", "ing_prov",
    "poblacion", "demo", "edad",
}

# slug -> cantidad exacta de tablas, mínimo de filas por tabla y segundo encabezado.
# Los mínimos admiten años nuevos, pero bloquean páginas vacías o incompletas.
CATALOGO = {
    "indice-mensual-de-actividad-economica-base-2007": (1, [90], "Mes"),
    "producto-interno-bruto-real-y-nominal": (1, [70], "Año"),
    "producto-interno-bruto-per-capita": (1, [60], "Año"),
    "producto-interno-bruto-real-segun-enfoque-de-oferta-base-2018": (2, [20, 90], None),
    "tasa-de-inflacion-3": (1, [40], None),
    "balance-del-sector-publico-no-financiero": (2, [15, 15], None),
    "balance-del-gobierno-central": (2, [15, 15], None),
    "deuda-del-sector-publico": (1, [30], "Año"),
    "composicion-de-la-deuda-publica": (1, [30], "Año"),
    "ingresos-impositivos": (1, [30], None),
    "transferencias-de-la-autoridad-del-canal-al-estado": (2, [20, 20], None),
    "saldo-de-la-cuenta-corriente": (2, [8, 8], None),
    "saldo-de-la-cuenta-comercial": (2, [8, 8], None),
    "inversion-extranjera-directa": (2, [15, 15], None),
    "exportaciones-de-bienes-y-servicios": (2, [20, 20], None),
    "tasa-de-participacion": (2, [50, 50], "Año"),
    "tasa-de-ocupacion": (2, [50, 50], "Año"),
    "tasa-de-informalidad": (2, [18, 18], "Año"),
    "mediana-salarial-por-actividad-economica": (1, [20], None),
    "pobreza-general-y-pobreza-extrema": (1, [20], "Año"),
    "coeficiente-de-gini": (1, [18], "Año"),
    "pobreza-general-por-provincias": (1, [12], None),
    "pobreza-extrema-por-provincias": (1, [12], None),
    "pobreza-multidimensional-por-provincias": (1, [12], None),
    "ingreso-mensual-promedio-de-los-hogares-por-provincias": (1, [12], None),
    "serie-historica-de-poblacion-censo-2023": (1, [90], "Año"),
    "indicadores-demograficos": (2, [65, 90], "Año"),
    "producto-interno-bruto-real-segun-enfoque-de-demanda-base-2018": (2, [7, 30], None),
    "producto-interno-bruto-nominal-segun-enfoque-de-demanda-base-2018": (2, [7, 30], None),
    "saldo-de-la-cartera-de-credito-segun-destino": (1, [3], None),
    "saldo-de-la-cartera-de-credito-segun-sector-economico": (1, [14], None),
    "saldo-de-los-depositos": (1, [17], None),
    "tasa-de-interes-sobre-credito": (1, [10], None),
    "tasa-de-interes-sobre-depositos": (1, [5], None),
    "tasa-de-inflacion-por-grupos": (2, [10, 13], None),
}


class ErrorMinerpa(RuntimeError):
    pass


def _texto(s):
    return html.unescape(re.sub(r"<[^>]+>", "", s or "")).replace("\xa0", " ").strip()


def _normalizar(s):
    s = unicodedata.normalize("NFKD", str(s or ""))
    return re.sub(r"\s+", " ", "".join(c for c in s if not unicodedata.combining(c))).strip().lower()


def _get(url, data=None, intentos=4, referer=None):
    ultimo = None
    for intento in range(intentos):
        try:
            headers = {"User-Agent": UA, "Accept": "application/json,text/html;q=0.9,*/*;q=0.8"}
            if data is not None:
                headers.update({
                    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                    "Origin": BASE,
                    "Referer": referer or BASE + "/",
                    "X-Requested-With": "XMLHttpRequest",
                })
            req = urllib.request.Request(url, data=data, headers=headers)
            with OPENER.open(req, timeout=90) as r:
                tipo = r.headers.get_content_type()
                crudo = r.read()
            if tipo not in {"text/html", "application/json", "text/plain"}:
                raise ErrorMinerpa(f"tipo de contenido inesperado {tipo} en {url}")
            return crudo.decode("utf-8", "replace")
        except (OSError, urllib.error.URLError, ErrorMinerpa) as e:
            ultimo = e
            if intento + 1 < intentos:
                time.sleep(2 ** intento)
    raise ErrorMinerpa(f"no se pudo descargar {url}: {ultimo}")


def _get_json(url, data, contexto, referer, intentos=4):
    ultimo = None
    for intento in range(intentos):
        try:
            texto = _get(url, data, intentos=2, referer=referer)
            if not texto.strip():
                raise ErrorMinerpa("respuesta vacía")
            valor = json.loads(texto)
            if not isinstance(valor, dict):
                raise ErrorMinerpa("la respuesta JSON no es un objeto")
            return valor
        except (json.JSONDecodeError, ErrorMinerpa) as e:
            ultimo = e
            if intento + 1 < intentos:
                time.sleep(2 ** intento)
    raise ErrorMinerpa(f"{contexto}: respuesta inválida del endpoint de tablas: {ultimo}")


def _fuente(pagina):
    m = re.search(r"Fuente\s*:(.*?)(?:</p>|</div>|<footer)", pagina, re.I | re.S)
    return _texto(m.group(1)) if m else ""


def _filas_html(tabla):
    cuerpo = re.search(r"<tbody[^>]*>(.*?)</tbody>", tabla, re.I | re.S)
    if not cuerpo:
        return []
    filas = []
    for fila in re.findall(r"<tr[^>]*>(.*?)</tr>", cuerpo.group(1), re.I | re.S):
        celdas = [_texto(celda) for celda in re.findall(r"<td[^>]*>(.*?)</td>", fila, re.I | re.S)]
        if celdas:
            filas.append(celdas)
    return filas


def extraer_pagina(slug):
    url = f"{BASE}/{slug}/"
    pagina = _get(url)
    nonce = re.search(r'name="wdtNonceFrontendEdit" value="([^"]*)', pagina)
    tablas = []
    for indice, m in enumerate(re.finditer(r'<table[^>]*id="(table_\d+)"[^>]*data-wpdatatable_id="(\d+)"[^>]*>(.*?)</table>', pagina, re.S)):
        tabla_id = m.group(2)
        encabezados = [_texto(x) for x in re.findall(r"<th[^>]*>(.*?)</th>", m.group(3), re.S)]
        filas = _filas_html(m.group(3))
        minimos = CATALOGO.get(slug, (0, [], None))[1]
        minimo = minimos[indice] if indice < len(minimos) else 1
        if len(filas) < minimo:
            payload = urllib.parse.urlencode({
                "draw": 1, "start": 0, "length": -1,
                "wdtNonce": nonce.group(1) if nonce else "",
            }).encode()
            respuesta = _get_json(
                f"{BASE}/wp-admin/admin-ajax.php?action=get_wdtable&table_id={tabla_id}",
                payload, f"{slug}, tabla {tabla_id}", url)
            filas = respuesta.get("data")
            if not isinstance(filas, list):
                raise ErrorMinerpa(f"{slug}: la tabla {tabla_id} no devolvió una lista de filas")
        tablas.append({"id": tabla_id, "headers": encabezados, "rows": filas})
    titulo = re.search(r"<h2[^>]*>(.*?)</h2>", pagina, re.S)
    unidad = re.search(r"Las cantidades son expresadas en\s*(.*?)(?:</p>|</div>)", pagina, re.I | re.S)
    return {
        "url": url,
        "title": _texto(titulo.group(1)) if titulo else slug,
        "units": _texto(unidad.group(1)) if unidad else "",
        "fuente": _fuente(pagina),
        "tables": tablas,
    }


def validar_crudo(crudo):
    errores = []
    for slug, (cantidad, minimos, segundo) in CATALOGO.items():
        pagina = crudo.get(slug)
        if not pagina:
            errores.append(f"falta la página obligatoria {slug}")
            continue
        tablas = pagina.get("tables", [])
        if len(tablas) != cantidad:
            errores.append(f"{slug}: se esperaban {cantidad} tablas y llegaron {len(tablas)}")
            continue
        for i, tabla in enumerate(tablas):
            h = tabla.get("headers", [])
            r = tabla.get("rows", [])
            if len(h) < 3:
                errores.append(f"{slug} tabla {i}: menos de tres encabezados")
            if len(r) < minimos[i]:
                errores.append(f"{slug} tabla {i}: {len(r)} filas; mínimo {minimos[i]}")
            if segundo and len(h) > 1 and _normalizar(h[1]) != _normalizar(segundo):
                errores.append(f"{slug} tabla {i}: segundo encabezado inesperado {h[1]!r}")
            ancho = len(h)
            malas = [n for n, fila in enumerate(r, 1) if len(fila) != ancho]
            if malas:
                errores.append(f"{slug} tabla {i}: filas con ancho distinto: {malas[:5]}")
    if errores:
        raise ErrorMinerpa("; ".join(errores))


def descargar_todo():
    paginas = {}
    for slug in CATALOGO:
        paginas[slug] = extraer_pagina(slug)
    validar_crudo(paginas)
    return {
        "meta": {"fuente": "MINERPA", "comprobado_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")},
        "paginas": paginas,
    }


def _paginas(crudo):
    # Compatibilidad con la salida histórica de descargar_minerpa.py.
    if "paginas" in crudo:
        return crudo["paginas"]
    return {k.rstrip("/").rsplit("/", 1)[-1]: v for k, v in crudo.items()}


def _num(s):
    s = str(s or "").replace(",", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


def _tabla(p, slug, i=0):
    try:
        return p[slug]["tables"][i]
    except (KeyError, IndexError) as e:
        raise ErrorMinerpa(f"no se encontró {slug}, tabla {i}") from e


def _long(p, slug, i, ycol, cols):
    out = []
    for r in _tabla(p, slug, i)["rows"]:
        vals = [_num(r[c]) for c in cols]
        if any(v is not None for v in vals):
            out.append([r[ycol]] + vals)
    return out


def _wide(p, slug, i, etiquetas):
    t = _tabla(p, slug, i)
    years = t["headers"][2:]
    filas = {_normalizar(r[1]): r for r in t["rows"]}
    res = {}
    for clave, prefijo in etiquetas.items():
        candidatos = [r for nombre, r in filas.items() if nombre.startswith(_normalizar(prefijo))]
        if not candidatos:
            raise ErrorMinerpa(f"{slug}: no se encontró la fila {prefijo!r}")
        res[clave] = [_num(x) for x in candidatos[0][2:]]
    keep = [k for k in range(len(years)) if any(res[c][k] is not None for c in res)]
    return {"years": [years[k] for k in keep], **{c: [res[c][k] for k in keep] for c in res}}


def transformar_d(crudo):
    p = _paginas(crudo)
    d = {}
    g = _long(p, "producto-interno-bruto-real-y-nominal", 0, 1, [9, 10, 8])
    d["pib_growth"] = [[int(r[0]), r[1], r[2]] for r in g if r[1] is not None]
    d["pib_nom"] = [[int(r[0]), r[3]] for r in g if r[3] is not None]
    d["pib_pc"] = [[int(r[0]), r[1], r[3]] for r in _long(p, "producto-interno-bruto-per-capita", 0, 1, [2, 3, 4, 5])]
    d["imae"] = [[r[0], r[2]] for r in _long(p, "indice-mensual-de-actividad-economica-base-2007", 0, 1, [2, 3])]
    d["inflacion"] = [[int(r[0]), r[1], r[2]] for r in _long(p, "tasa-de-inflacion-3", 0, 1, [2, 3])]
    t = _tabla(p, "producto-interno-bruto-real-segun-enfoque-de-oferta-base-2018")
    sectores = []
    for r in t["rows"]:
        nombre, antes, ahora = r[1].strip(), _num(r[-2]), _num(r[-1])
        if nombre.startswith("PRODUCTO") or antes is None:
            continue
        sectores.append([nombre, ahora, round((ahora / antes - 1) * 100, 1) if antes else None])
    d["sectores"] = {"year": t["headers"][-1], "rows": sectores}
    d["spnf"] = _wide(p, "balance-del-sector-publico-no-financiero", 1, {"ing": "Ingresos totales", "gas": "Gastos totales", "int": "Pago de intereses", "prim": "Balance primario", "tot": "Balance total"})
    d["gc"] = _wide(p, "balance-del-gobierno-central", 1, {"trib": "Tributarios", "dir": "Directos", "ind": "Indirectos", "notrib": "No Tributarios", "tot": "Balance Total"})
    d["deuda"] = [[int(r[0])] + r[1:] for r in _long(p, "deuda-del-sector-publico", 0, 1, [2, 3])]
    d["deuda_comp"] = [[int(r[0])] + r[1:] for r in _long(p, "composicion-de-la-deuda-publica", 0, 1, [3, 4])]
    d["impuestos"] = _wide(p, "ingresos-impositivos", 0, {"tot": "INGRESOS IMPOSITIVOS TOTALES", "renta": "Imp. sobre la renta", "consumo": "Impuestos generales sobre el consumo", "selectivos": "Impuestos selectivos", "comext": "Impuestos sobre el comercio exterior", "prop": "Impuestos sobre la propiedad"})
    a = _long(p, "transferencias-de-la-autoridad-del-canal-al-estado", 0, 1, [2])
    b = _long(p, "transferencias-de-la-autoridad-del-canal-al-estado", 1, 1, [2])
    d["acp"] = [[int(x[0]), x[1], y[1]] for x, y in zip(a, b)]
    d["cc"] = _wide(p, "saldo-de-la-cuenta-corriente", 1, {"cc": "Cuenta corriente"})
    d["com"] = _wide(p, "saldo-de-la-cuenta-comercial", 1, {"x": "Exportaciones de bienes y servicios", "m": "Importaciones de bienes y servicios", "bienes": "A. Saldo de bienes", "serv": "B. Saldo de servicios"})
    d["ied"] = _wide(p, "inversion-extranjera-directa", 0, {"rec": "B. Inversión directa en la economía"})
    d["ied_pct"] = _wide(p, "inversion-extranjera-directa", 1, {"rec": "B. Inversión directa en la economía"})
    d["xserv"] = _wide(p, "exportaciones-de-bienes-y-servicios", 1, {"zlc": "1. Reexportaciones de la Zona Libre", "nac": "2. Exportaciones nacionales", "transp": "3. Transporte", "viajes": "5. Viajes", "fin": "8. Servicios financieros"})
    d["participacion"] = [[int(r[0]), r[1]] for r in _long(p, "tasa-de-participacion", 1, 1, [4])]
    d["desempleo"] = [[int(r[0]), round(100 - r[1], 1)] for r in _long(p, "tasa-de-ocupacion", 1, 1, [4])]
    d["informalidad"] = [[int(r[0]), r[1]] for r in _long(p, "tasa-de-informalidad", 1, 1, [4])]
    t = _tabla(p, "mediana-salarial-por-actividad-economica")
    d["salario"] = {"years": t["headers"][2:], "total": [_num(x) for x in t["rows"][0][2:]], "act": [[r[1].strip(), _num(r[-1])] for r in t["rows"][1:]], "year": t["headers"][-1]}
    d["pobreza"] = [[int(r[0])] + r[1:] for r in _long(p, "pobreza-general-y-pobreza-extrema", 0, 1, [2, 3, 4, 5])]
    d["gini"] = [[int(r[0]), r[1]] for r in _long(p, "coeficiente-de-gini", 0, 1, [2])]
    def prov(slug):
        q = _tabla(p, slug)
        return {"year": q["headers"][-1], "rows": [[r[1].strip(), _num(r[-1])] for r in q["rows"]]}
    d["pob_prov"], d["pobx_prov"] = prov("pobreza-general-por-provincias"), prov("pobreza-extrema-por-provincias")
    d["pobm_prov"], d["ing_prov"] = prov("pobreza-multidimensional-por-provincias"), prov("ingreso-mensual-promedio-de-los-hogares-por-provincias")
    d["poblacion"] = [[int(r[0]), r[1]] for r in _long(p, "serie-historica-de-poblacion-censo-2023", 0, 1, [2])]
    d["demo"] = [[int(r[0])] + r[1:] for r in _long(p, "indicadores-demograficos", 0, 1, [3, 9, 11])]
    d["edad"] = [[int(r[0]), r[1]] for r in _long(p, "indicadores-demograficos", 1, 1, [4])]
    return d


def _wide_filas(p, slug, tabla=0):
    t = _tabla(p, slug, tabla)
    years = [int(x) for x in t["headers"][2:]]
    return years, {str(r[1]).strip(): [_num(x) for x in r[2:]] for r in t["rows"] if r[1] not in (None, "")}


def _fila(filas, prefijo):
    n = _normalizar(prefijo)
    for nombre, valores in filas.items():
        if _normalizar(nombre).startswith(n):
            return valores
    raise ErrorMinerpa(f"no se encontró la fila {prefijo!r}")


def transformar_b(crudo):
    p = _paginas(crudo)
    years, f = _wide_filas(p, "saldo-de-la-cartera-de-credito-segun-destino")
    b = {"cred": {"years": years, "tot": _fila(f, "Cartera Crediticia Total"), "int": _fila(f, "Cartera Interna"), "ext": _fila(f, "Cartera Externa")}}
    years, f = _wide_filas(p, "saldo-de-la-cartera-de-credito-segun-sector-economico")
    nombres = ["Créditos Internos", "Sector Público", "Sector Privado", "Instituciones Financieras", "Agricultura", "Ganadería", "Pesca", "Minería", "Comercio", "Industria"]
    b["credsec"] = {"years": years, "rows": [[n[0].upper() + n[1:].lower(), _fila(f, n)] for n in nombres]}
    # Restaurar la capitalización usada por la interfaz.
    b["credsec"]["rows"] = [[nombres[i].capitalize(), r[1]] for i, r in enumerate(b["credsec"]["rows"])]
    years, f = _wide_filas(p, "saldo-de-los-depositos")
    b["dep"] = {"years": years, "tot": _fila(f, "DEPÓSITOS"), "int": _fila(f, "Depósitos Internos"), "ofi": _fila(f, "Depósitos Internos Oficiales"), "vista": _fila(f, "Depósitos Internos de Particulares A la Vista"), "plazo": _fila(f, "Depósitos Internos de Particulares A Plazo"), "ahorro": _fila(f, "Depósitos Internos de Particulares De Ahorros")}
    years, f = _wide_filas(p, "tasa-de-interes-sobre-credito")
    b["tcred"] = {"years": years, "mayor": _fila(f, "Tasa sobre créditos al por mayor"), "menor": _fila(f, "Tasa sobre créditos al por menor"), "tarjeta": _fila(f, "Tasa sobre tarjetas"), "personal": _fila(f, "Tasa sobre préstamos personales"), "autos": _fila(f, "Tasa sobre autos"), "vivienda": [None if x == 0 else x for x in _fila(f, "Tasa sobre vivienda")], "agri": _fila(f, "Tasa sobre agricultura"), "gana": _fila(f, "Tasa sobre ganadería"), "constr": _fila(f, "Tasa sobre construcción"), "ind": _fila(f, "Tasa sobre industria")}
    years, f = _wide_filas(p, "tasa-de-interes-sobre-depositos")
    b["tdep"] = {"years": years, "m1": _fila(f, "Tasa sobre depósitos a plazo fijo 1 mes"), "m3": _fila(f, "Tasa sobre depósitos a plazo fijo 3 meses"), "m6": _fila(f, "Tasa sobre depósitos a plazo fijo 6 meses"), "a1": _fila(f, "Tasa sobre depósitos a plazo fijo 1 Año"), "ahorro": _fila(f, "Tasa sobre depósitos de ahorro")}
    years, f = _wide_filas(p, "tasa-de-inflacion-por-grupos", 1)
    etiquetas = {
        "TOTAL": "Total", "Alimentos y Bebidas": "Alimentos y bebidas", "Bebidas Alcohólicas": "Alcohol y tabaco",
        "Prendas de Vestir": "Ropa y calzado", "Vivienda, Agua": "Vivienda, agua y luz", "Muebles, Artículos": "Muebles y hogar",
        "Salud": "Salud", "Transporte": "Transporte", "Comunicaciones": "Comunicaciones", "Recreación": "Recreación y cultura",
    }
    b["infg"] = {"years": years, "rows": [[salida, _fila(f, entrada)] for entrada, salida in etiquetas.items()]}
    return b


def transformar_dm(crudo):
    p = _paginas(crudo)
    out = {}
    years_final = None
    for tipo, slug in (("real", "producto-interno-bruto-real-segun-enfoque-de-demanda-base-2018"), ("nom", "producto-interno-bruto-nominal-segun-enfoque-de-demanda-base-2018")):
        years, filas = _wide_filas(p, slug)
        years_final = years_final or years
        if years != years_final:
            raise ErrorMinerpa("los años del PIB real y nominal por demanda no coinciden")
        out[tipo] = {
            "gob": _fila(filas, "Gasto de consumo final del Gobierno General"),
            "priv": _fila(filas, "Gasto de consumo final de los hogares" if tipo == "real" else "Gasto de consumo final privado"),
            "fbkf": _fila(filas, "Formación bruta de capital fijo"),
            "exist": _fila(filas, "Variación de las existencias"),
            "x": _fila(filas, "Exportaciones de bienes y servicios"),
            "m": _fila(filas, "Menos:  Importaciones de bienes y servicios" if tipo == "real" else "Importaciones de bienes y servicios"),
            "pib": _fila(filas, "PRODUCTO INTERNO BRUTO"),
        }
    return {"years": years_final, **out}


def transformar(crudo):
    p = _paginas(crudo)
    validar_crudo(p)
    return {"D": transformar_d(p), "B": transformar_b(p), "DM": transformar_dm(p)}


def sha256_json(obj):
    raw = json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def periodo(obj):
    if isinstance(obj, list) and obj:
        return str(obj[-1][0]) if isinstance(obj[-1], list) and obj[-1] else None
    if isinstance(obj, dict):
        if obj.get("years"):
            return str(obj["years"][-1])
        if obj.get("year") is not None:
            return str(obj["year"])
    return None
