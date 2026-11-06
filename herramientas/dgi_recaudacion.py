"""Extrae y valida la recaudación publicada por la Dirección General de Ingresos
(DGI) de Panamá en sus "Informes Preliminares de Recaudación" mensuales.

Fuente: https://dgi.mef.gob.pa/Transparencia/InformesT (PDF en /Transparencia/IFP/).

Qué se extrae de cada PDF (todo en millones de balboas):
  * Cuadros de 7 columnas (en miles de B/.): mes y acumulado, en efectivo:
      - resumen de ingresos corrientes (tributarios, no tributarios, otros)
      - detalle de ingresos tributarios (ISR, ITBMS, importación, ISC...)
      - detalle de ingresos no tributarios
    Columnas: efectivo del año, presupuesto, diferencia, %, mismo periodo del año
    anterior, diferencia y %.
  * Los mismos cuadros "con documentos fiscales" (acumulado), cuando el informe
    los trae. Se guardan aparte: nunca se suman a los de efectivo.
  * El cuadro mensual "Efectiva / Documentos fiscales / Total" del año en curso.
  * El cuadro de recaudación con documentos fiscales (en balboas).

Reglas:
  * No se inventa ni se interpola nada. Si un cuadro no se encuentra o no cuadra
    por dentro, se anota un error y ese informe no se publica.
  * Algunos PDF traen texto viejo escondido detrás de los cuadros (de 2018 y
    2019). Se descarta todo carácter tapado por un rectángulo o imagen dibujado
    encima, y cada renglón se arma solo con letras del mismo tamaño.
  * El periodo se lee del propio PDF, no del nombre del archivo ni del enlace:
    la página oficial enlaza informes de 2025 en meses de 2026 que aún no salen.

Uso:
  python3 dgi_recaudacion.py SALIDA [--pdf ARCHIVO ...] [--desde AÑO] [--todos]
     sin --pdf: lee la página oficial y procesa los informes nuevos.
"""
import argparse
import datetime as dt
import glob
import hashlib
import html
import json
import os
import re
import sys
import unicodedata
import urllib.parse
import urllib.request

import pdfplumber
from pdfplumber.utils.text import extract_words
from pdfminer.layout import LTChar, LTCurve, LTImage

PAGINA = "https://dgi.mef.gob.pa/Transparencia/InformesT"
BASE = "https://dgi.mef.gob.pa/Transparencia/"
UA = "MacroPA-datos/1.0 (+https://the-iron-cross.github.io/MacroPA/)"

MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
         "septiembre", "octubre", "noviembre", "diciembre"]
ABREV = {"ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6, "jul": 7,
         "ago": 8, "sep": 9, "sept": 9, "set": 9, "oct": 10, "nov": 11, "dic": 12}

COLS7 = ["efectivo", "presupuesto", "dif_presupuesto", "pct_presupuesto",
         "anio_anterior", "dif_anio_anterior", "pct_anio_anterior"]


def norm(s):
    s = unicodedata.normalize("NFKD", s.lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]", "", s)


# Renglones conocidos. Si aparece uno nuevo, se anota como cambio de formato.
RENGLONES = {
    "totalingresoscorrientes": "total_corrientes",
    "iingresostributarios": "tributarios",
    "impuestosdirectos": "directos",
    "impuestosindirectos": "indirectos",
    "iiingresosnotributarios": "no_tributarios",
    "tasasderechosyotros": "tasas_derechos_otros",
    "participaciondeutilidadesyaportes": "utilidades_aportes",
    "partdeutilidadesyaportes": "utilidades_aportes",
    "iiiotrosingresoscorrientes": "otros_corrientes",
    "renta": "renta",
    "juridica": "renta_juridica",
    "natural": "renta_natural",
    "dividendosycomplementario": "renta_dividendos_complementario",
    "dividendosycomplementarios": "renta_dividendos_complementario",
    "inmuebles": "inmuebles",
    "avisosdeoperaciondeempresas": "aviso_operacion",
    "seguroeducativo": "seguro_educativo",
    "itbmsventas": "itbms_ventas",
    "itbmsimportacion": "itbms_importacion",
    "importacion": "importacion",
    "consumodecombustible": "consumo_combustible",
    "impuestosselectivosalconsumo": "selectivo_consumo",
    "peajesyservdelcanal": "canal_peajes_servicios",
    "tasaanualsociedadesanonimas": "tasa_anual_sa",
    "concesiones": "concesiones",
    "dividendosdelcanal": "dividendos_canal",
    "dividendosdeempresasconcapitalmixto": "dividendos_capital_mixto",
    "otrasentidadesgubernamentales": "otras_entidades",
}
# "Otros" depende del renglón padre.
OTROS_SEGUN_PADRE = {"renta": "renta_otros", "indirectos": "indirectos_otros",
                     "tasas_derechos_otros": "tasas_otros"}
PADRE_DE = {
    "renta_juridica": "renta", "renta_natural": "renta",
    "renta_dividendos_complementario": "renta", "renta": "directos",
    "inmuebles": "directos", "aviso_operacion": "directos",
    "seguro_educativo": "directos",
    "itbms_ventas": "indirectos", "itbms_importacion": "indirectos",
    "importacion": "indirectos", "consumo_combustible": "indirectos",
    "selectivo_consumo": "indirectos",
    "canal_peajes_servicios": "tasas_derechos_otros",
    "tasa_anual_sa": "tasas_derechos_otros", "concesiones": "tasas_derechos_otros",
}

# Sumas que deben cumplirse (padre = suma de hijos).
SUMAS = {
    "total_corrientes": ["tributarios", "no_tributarios", "otros_corrientes"],
    "tributarios": ["directos", "indirectos"],
    "no_tributarios": ["tasas_derechos_otros", "utilidades_aportes"],
    "directos": ["renta", "inmuebles", "aviso_operacion", "seguro_educativo"],
    "renta": ["renta_juridica", "renta_natural", "renta_dividendos_complementario",
              "renta_otros"],
    "indirectos": ["itbms_ventas", "itbms_importacion", "importacion",
                   "consumo_combustible", "selectivo_consumo", "indirectos_otros"],
    "tasas_derechos_otros": ["canal_peajes_servicios", "tasa_anual_sa",
                             "concesiones", "tasas_otros"],
    "utilidades_aportes": ["dividendos_canal", "dividendos_capital_mixto",
                           "otras_entidades"],
}
FIN_DE_CUADRO = {"resumen": "otros_corrientes", "tributarios": "indirectos_otros",
                 "no_tributarios": "otros_corrientes"}


# ------------------------------------------------------------ lectura del PDF
def _aplanar(o):
    if isinstance(o, (LTChar, LTCurve, LTImage)):
        yield o
        return
    for c in getattr(o, "_objs", []) or []:
        yield from _aplanar(c)


def pagina_limpia(page):
    """Quita los caracteres tapados por figuras rellenas dibujadas después."""
    objs = list(_aplanar(page.layout))
    tapas = [(i, o) for i, o in enumerate(objs)
             if isinstance(o, LTImage) or (isinstance(o, LTCurve) and o.fill)]
    H = page.height
    ocultos = set()
    for i, o in enumerate(objs):
        if not isinstance(o, LTChar):
            continue
        cx, cy = (o.x0 + o.x1) / 2, (o.y0 + o.y1) / 2
        for j, t in tapas:
            if j > i and t.x0 <= cx <= t.x1 and t.y0 <= cy <= t.y1 \
                    and (t.x1 - t.x0) * (t.y1 - t.y0) > 4:
                ocultos.add((round(o.x0, 1), round(H - o.y1, 1), o.get_text()))
                break
    return page.filter(lambda c: not (
        c.get("object_type") == "char"
        and (round(c["x0"], 1), round(c["top"], 1), c["text"]) in ocultos))


NUM_RE = re.compile(r"^\(?-?[\d,]*\.?\d+\)?$")


def _es_num(t):
    return t in ("-", "∞", "—") or bool(NUM_RE.match(t))


def renglones(page):
    """Agrupa las palabras visibles en renglones del mismo tamaño de letra."""
    cp = pagina_limpia(page)
    # palabras por tamaño de letra: así el texto escondido de otro tamaño no se
    # mezcla, letra por letra, con las cifras del cuadro
    ws = []
    chars = cp.chars
    for t in sorted({round(c["size"], 1) for c in chars}):
        sub = [c for c in chars if round(c["size"], 1) == t]
        grupo = extract_words(sub, x_tolerance=2.5, y_tolerance=2, extra_attrs=["size"])
        grupo.sort(key=lambda w: (round(w["top"]), w["x0"]))
        # une pedazos de una misma palabra o cifra (espaciado raro de algunos
        # PDF: "1 7,439,486", "R e c a u d a c i ó n")
        out = []
        for w in grupo:
            if out and abs(w["top"] - out[-1]["top"]) < 1.5 \
                    and -1.5 < w["x0"] - out[-1]["x1"] < 0.9:
                prev = dict(out[-1])
                prev["text"] += w["text"]
                prev["x1"] = w["x1"]
                out[-1] = prev
            else:
                out.append(w)
        ws += out
    ws.sort(key=lambda w: (round(w["top"]), w["x0"]))
    lineas = []
    for w in ws:
        mid = (w["top"] + w["bottom"]) / 2
        for L in lineas:
            if abs(L["mid"] - mid) <= 2.5:
                L["w"].append(w)
                break
        else:
            lineas.append({"mid": mid, "size": w["size"], "w": [w]})
    for L in lineas:
        L["w"].sort(key=lambda w: w["x0"])
        L["texto"] = " ".join(w["text"] for w in L["w"])
    lineas.sort(key=lambda L: (L["mid"], L["w"][0]["x0"]))
    return lineas


def de_tamano(words, tamanos):
    """Solo las palabras con alguno de los tamaños de letra del cuadro."""
    return [w for w in words if any(abs(w["size"] - t) < 0.3 for t in tamanos)]


def a_numero(t):
    if t in ("-", "—"):
        return 0.0
    if t == "∞":
        return None
    neg = t.startswith("(") and t.endswith(")")
    t = t.strip("()").replace(",", "")
    v = float(t)
    return -v if neg else v


def separar(words):
    """Devuelve (palabras de la etiqueta, palabras numéricas al final)."""
    k = len(words)
    while k > 0 and _es_num(words[k - 1]["text"]):
        k -= 1
    # un "-" pegado a la etiqueta (I.T.B.M.S. - Ventas) no es número
    return words[:k], words[k:]


# ------------------------------------------------------------ cuadros 7 col.
def leer_cuadros7(pdf, errores, avisos):
    cuadros = []
    for pn, page in enumerate(pdf.pages):
        lineas = renglones(page)
        for i, L in enumerate(lineas):
            if "Miles)" not in L["texto"] or "(En" not in L["texto"]:
                continue
            # periodo ("Agosto" o "Acumulado Agosto"), mismo renglón o siguientes
            per = None
            for M in lineas[i:i + 4]:
                t = norm(M["texto"])
                for m in MESES:
                    if m in t:
                        per = ("acumulado" if "acumulado" in t else "mes", m)
                        break
                if per:
                    break
            # encabezado de columnas
            enc = None
            for M in lineas[i:i + 6]:
                if "Presup" in M["texto"] and "%" in M["texto"]:
                    enc = M
                    break
            if not enc:
                # rótulo suelto (a veces viene de texto escondido): se anota y se sigue
                avisos.append(f"p{pn+1}: rótulo '(En B/. Miles)' sin cuadro debajo")
                continue
            c = leer_filas7(lineas, enc, pn, errores)
            if c is None:
                continue
            c["pagina"] = pn + 1
            c["periodo_texto"] = per
            c["encabezado"] = [w["text"] for w in enc["w"] if w["x0"] > c["x_etiqueta"]]
            cuadros.append(c)
    return cuadros


def _etiqueta_contigua(words, hasta_x):
    """Palabras de la etiqueta: la cadena continua que termina antes de las cifras
    (así se deja fuera el texto de la columna lateral)."""
    ws = [w for w in words if w["x1"] <= hasta_x and w["size"] < 9.4]
    cadena = []
    for w in reversed(ws):
        if cadena and cadena[0]["x0"] - w["x1"] > 12:
            break
        cadena.insert(0, w)
    return cadena


def leer_filas7(lineas, enc, pn, errores):
    idx = lineas.index(enc)
    x_etq = anclas = tam_num = None
    filas_num, trozos = [], []      # renglones con cifras y pedazos de etiqueta
    for L in lineas[idx + 1: idx + 70]:
        if L["mid"] - enc["mid"] > 330 or "Miles)" in L["texto"]:
            break
        if anclas is None:
            _, nums = separar([w for w in L["w"] if w["size"] < 9.4])
            if len(nums) < 7:
                continue
            nums = nums[-7:]
            etq = _etiqueta_contigua(L["w"], nums[0]["x0"] - 5)
            if not etq:
                continue
            anclas = [w["x1"] for w in nums]
            tam_num = nums[0]["size"]
            x_etq = etq[0]["x0"]
        else:
            nums = [w for w in L["w"] if _es_num(w["text"]) and abs(w["size"] - tam_num) < 1.1
                    and w["x0"] > anclas[0] - 60]
            limite = nums[0]["x0"] - 5 if nums else anclas[0] - 25
            etq = [w for w in _etiqueta_contigua(L["w"], limite) if w["x0"] >= x_etq - 10]
        if nums:
            filas_num.append({"mid": L["mid"], "nums": nums, "trozos": []})
        if etq:
            trozos.append({"mid": L["mid"], "texto": " ".join(w["text"] for w in etq),
                           "propio": bool(nums)})
    if not filas_num:
        return None
    # cada pedazo de etiqueta va con el renglón de cifras más cercano
    for t in trozos:
        if t["propio"]:
            f = next(f for f in filas_num if f["mid"] == t["mid"])
        else:
            cerca = [f for f in filas_num if abs(f["mid"] - t["mid"]) <= 10.5]
            if not cerca:
                continue
            # un pedazo suelto completa la etiqueta del renglón de arriba si esta
            # quedó cortada ("Participación de Utilidades y" / "Aportes")
            cortada = [f for f in cerca if f["mid"] < t["mid"] and f["trozos"] and
                       re.search(r"\b(y|de|del)$", f["trozos"][-1]["texto"])]
            f = cortada[-1] if cortada else min(cerca, key=lambda f: abs(f["mid"] - t["mid"]))
        f["trozos"].append(t)
    filas, padre_actual, tipo = [], None, None
    for f in filas_num:
        etiqueta = " ".join(t["texto"] for t in sorted(f["trozos"], key=lambda t: t["mid"])).strip()
        valores = asignar(f["nums"], anclas)
        if not etiqueta and valores is not None and sum(v is not None for v in valores) < 3:
            continue        # restos de texto escondido entre renglones
        if valores is None:
            errores.append(f"p{pn+1}: renglón '{etiqueta}' con cifras que no caen en columnas: "
                           + " ".join(w["text"] for w in f["nums"]))
            return None
        cod = codigo(etiqueta, padre_actual)
        if cod in ("directos", "indirectos", "renta", "tasas_derechos_otros",
                   "utilidades_aportes"):
            padre_actual = cod
        elif cod in ("inmuebles", "aviso_operacion", "seguro_educativo"):
            padre_actual = "directos"
        if tipo is None:
            tipo = {"total_corrientes": "resumen", "tributarios": "tributarios",
                    "no_tributarios": "no_tributarios"}.get(cod)
            if tipo is None:
                errores.append(f"p{pn+1}: cuadro que empieza con renglón desconocido '{etiqueta}'")
                return None
        filas.append({"etiqueta": etiqueta, "codigo": cod, "miles": valores})
        if cod == FIN_DE_CUADRO[tipo]:
            break
    else:
        errores.append(f"p{pn+1}: cuadro {tipo} sin su último renglón ({FIN_DE_CUADRO[tipo]})")
    return {"tipo": tipo, "filas": filas, "x_etiqueta": x_etq}


def codigo(etiqueta, padre):
    n = norm(etiqueta)
    if n == "otros":
        return OTROS_SEGUN_PADRE.get(padre, "otros_sin_padre")
    return RENGLONES.get(n, "desconocido:" + n)


def asignar(nums, anclas):
    """Pone cada cifra en la columna cuyo borde derecho le queda más cerca.
    Las columnas de montos llevan enteros (miles) y las de % un decimal; una
    cifra que no calza con su columna se descarta (suele ser texto escondido)."""
    if len(nums) == len(anclas):
        return [a_numero(w["text"]) for w in nums]
    vals = [None] * len(anclas)
    for w in nums:
        k = min(range(len(anclas)), key=lambda j: abs(anclas[j] - w["x1"]))
        if abs(anclas[k] - w["x1"]) > 15:
            continue
        es_pct = len(anclas) == 7 and k in (3, 6)
        t = w["text"].strip("()")
        if len(anclas) == 7 and t not in ("-", "∞", "—") and (("." in t) != es_pct):
            continue
        if vals[k] is not None:
            return None
        vals[k] = a_numero(w["text"])
    return vals


# ------------------------------------------------------- cuadro mensual
def leer_mensual(pdf, errores):
    """Cuadro 'Recaudación de Ingresos Corrientes y Documentos Fiscales' por mes."""
    for pn, page in enumerate(pdf.pages):
        lineas = renglones(page)
        filas, total, nota, tam = {}, None, None, None
        for L in lineas:
            cand = [" ".join(w["text"] for w in de_tamano(L["w"], {sz}))
                    for sz in sorted({round(w["size"], 1) for w in L["w"]})]
            t = next((c for c in cand if re.match(r"^(Enero|Febrero|Marzo|Abril|Mayo|Junio|Julio|Agosto|"
                                                   r"Septiembre|Setiembre|Octubre|Noviembre|Diciembre|"
                                                   r"Total(?: Acumulado)?)\s+[\d,]+\s+[\d,]+\s+[\d,]+$", c)),
                     cand[0] if cand else "")
            m = re.match(r"^(Enero|Febrero|Marzo|Abril|Mayo|Junio|Julio|Agosto|Septiembre|"
                         r"Setiembre|Octubre|Noviembre|Diciembre)\s+([\d,]+)\s+([\d,]+)\s+([\d,]+)$", t)
            if m:
                mes = MESES.index(m.group(1).lower().replace("setiembre", "septiembre")) + 1
                if mes in filas:
                    errores.append(f"p{pn+1}: mes {mes} repetido en el cuadro mensual")
                filas[mes] = [a_numero(m.group(k)) for k in (2, 3, 4)]
                tam = L["size"]
                continue
            m = re.match(r"^Total(?: Acumulado)?\s+([\d,]+)\s+([\d,]+)\s+([\d,]+)$", t)
            if m:
                total = [a_numero(m.group(k)) for k in (1, 2, 3)]
                continue
            if filas and t.lower().startswith("nota"):
                nota = t
        if filas:
            if total is None and max(filas) > 1:
                errores.append(f"p{pn+1}: cuadro mensual sin 'Total Acumulado'")
            return {"pagina": pn + 1, "filas": filas, "total": total, "nota": nota}
    errores.append("no se encontró el cuadro mensual de efectivo y documentos fiscales")
    return None


# ------------------------------------------------- documentos fiscales (B/.)
DOC_COLS = ["creditos_fiscales", "certificados", "total_mes", "mes_anio_anterior",
            "acumulado", "acumulado_anio_anterior"]


def leer_documentos(pdf, errores):
    for pn, page in enumerate(pdf.pages):
        lineas = renglones(page)
        enc = next((L for L in lineas if "Balboas)" in L["texto"]), None)
        if not enc:
            continue
        filas, anclas, tam, x_etq = [], None, None, None
        for L in lineas[lineas.index(enc) + 1:]:
            ws = L["w"] if tam is None else de_tamano(L["w"], tam)
            if not ws:
                continue
            if ws[0]["text"].startswith("*"):
                break
            etq, nums = separar(ws)
            texto = " ".join(w["text"] for w in etq)
            if tam is None:
                if norm(texto).endswith("ingresoscorrientes") and len(nums) in (4, 6):
                    tam, x_etq = {etq[-1]["size"], nums[0]["size"]}, etq[0]["x0"]
                    anclas = [w["x1"] for w in nums]
                else:
                    continue
            if not etq:
                continue
            n = len(anclas)        # en enero el cuadro no trae columnas de acumulado
            vals = [None] * n
            ok = True
            for w in nums:
                k = min(range(n), key=lambda j: abs(anclas[j] - w["x1"]))
                if abs(anclas[k] - w["x1"]) > 15 or vals[k] is not None:
                    ok = False
                vals[k] = a_numero(w["text"])
            if not ok:
                errores.append(f"p{pn+1}: documentos fiscales, renglón '{texto}' no cae en columnas")
                return None
            filas.append({"etiqueta": texto, "balboas": vals + [None] * (6 - n)})
        if filas:
            return {"pagina": pn + 1, "filas": filas}
    errores.append("no se encontró el cuadro de documentos fiscales en balboas")
    return None


# ------------------------------------------------------------------ informe
def periodo_del_pdf(pdf):
    t = (pdf.pages[0].extract_text() or "").lower()
    m = re.search(r"para el año (20\d\d)", t)
    if "informe oficial" in t and m:          # informe anual oficial (cierra en diciembre)
        return int(m.group(1)), 12
    for page in pdf.pages[:3]:
        txt = page.extract_text() or ""
        for linea in txt.splitlines()[:6]:
            m = re.search(r"\b(" + "|".join(MESES) + r"|setiembre)\s+(20\d\d)\b", linea.lower())
            if m:
                mes = m.group(1).replace("setiembre", "septiembre")
                return int(m.group(2)), MESES.index(mes) + 1
    return None


def mill(v, div=1000.0):
    return None if v is None else round(v / div, 6)


def procesar_pdf(ruta, meta=None):
    errores, avisos = [], []
    with open(ruta, "rb") as f:
        crudo = f.read()
    pdf = pdfplumber.open(ruta)
    info = pdf.metadata or {}
    per = periodo_del_pdf(pdf)
    if not per:
        errores.append("no se pudo leer el mes y año del informe en el PDF")
        anio = mes = None
    else:
        anio, mes = per
    texto1 = " ".join((p.extract_text() or "") for p in pdf.pages[:2]).lower()
    oficial = "oficial" in (meta or {}).get("etiqueta", "").lower() or \
        "informe oficial" in texto1
    inf = {
        "fuente": {
            "pagina": PAGINA,
            "url": (meta or {}).get("url"),
            "etiqueta_en_pagina": (meta or {}).get("etiqueta"),
            "seccion_en_pagina": (meta or {}).get("seccion"),
            "archivo": os.path.basename(ruta),
            "sha256": hashlib.sha256(crudo).hexdigest(),
            "bytes": len(crudo),
            "paginas": len(pdf.pages),
            "pdf_creado": info.get("CreationDate"),
            "pdf_modificado": info.get("ModDate"),
            "pdf_programa": info.get("Creator"),
        },
        "anio": anio, "mes": mes,
        "periodo": f"{anio}-{mes:02d}" if per else None,
        "condicion": "oficial" if oficial else "preliminar",
        "unidad": "millones de balboas",
    }
    if meta and meta.get("seccion") and per:
        if str(anio) != str(meta["seccion"]) or (meta.get("mes") and meta["mes"] != mes):
            avisos.append(f"el enlace '{meta.get('etiqueta')}' de la sección {meta['seccion']} "
                          f"abre el informe de {MESES[mes-1]} {anio}")

    # ---- cuadros de 7 columnas
    cuadros = leer_cuadros7(pdf, errores, avisos)
    # El mes de las columnas ("Oct24 Presup. ...") manda sobre la portada, que a
    # veces trae el mes equivocado (IFP2024Oct dice "Septiembre 2024" arriba).
    meses_col = []
    for c in cuadros:
        m0 = re.match(r"^([a-z]+)(\d\d)$", norm(c["encabezado"][0]) if c["encabezado"] else "")
        if m0 and m0.group(1) in ABREV:
            meses_col.append((2000 + int(m0.group(2)), ABREV[m0.group(1)]))
    if meses_col and per:
        comun = max(set(meses_col), key=meses_col.count)
        if comun != (anio, mes) and meses_col.count(comun) >= len(meses_col) - 1:
            avisos.append(f"la portada dice {MESES[mes-1]} {anio}, pero los cuadros son de "
                          f"{MESES[comun[1]-1]} {comun[0]}; se usa el de los cuadros")
            anio, mes = comun
            per = comun
            inf["anio"], inf["mes"], inf["periodo"] = anio, mes, f"{anio}-{mes:02d}"
    vistos = {}
    salida = {}
    for c in cuadros:
        alcance = c["periodo_texto"][0] if c["periodo_texto"] else None
        if c["periodo_texto"] and per and MESES.index(c["periodo_texto"][1]) + 1 != mes:
            # el rótulo de arriba contradice al informe; si las columnas sí dicen
            # el mes correcto (se revisa abajo) es solo un error de rótulo
            avisos.append(f"p{c['pagina']}: el rótulo del cuadro dice '{c['periodo_texto'][1]}' "
                          f"pero sus columnas y el informe son de {MESES[mes-1]}")
        if alcance is None:
            # sin rótulo: se deduce por orden (mes antes que acumulado)
            alcance = "acumulado" if (c["tipo"], "mes") in vistos else "mes"
            avisos.append(f"p{c['pagina']}: cuadro {c['tipo']} sin rótulo de periodo; se tomó como '{alcance}'")
        clave = (c["tipo"], alcance)
        vistos[clave] = vistos.get(clave, 0) + 1
        base = "efectivo" if vistos[clave] == 1 else "con_documentos"
        if vistos[clave] > 2:
            errores.append(f"p{c['pagina']}: cuadro {clave} aparece más de dos veces")
            continue
        nombre = f"{c['tipo']}_{alcance}_{base}"
        # encabezado: año del informe y año anterior
        enc = " ".join(c["encabezado"]).lower()
        if per:
            ab = [k for k, v in ABREV.items() if v == mes]
            ab = ab + ["año", "ano"] if oficial else ab
            esp_act = [f"{a}{anio % 100:02d}" for a in ab]
            esp_ant = [f"{a}{(anio - 1) % 100:02d}" for a in ab]
            if not any(e in enc.replace(" ", "") for e in esp_act) or \
                    not any(e in enc.replace(" ", "") for e in esp_ant):
                errores.append(f"p{c['pagina']}: encabezado inesperado en {nombre}: {' '.join(c['encabezado'])}")
        filas = []
        for f in c["filas"]:
            if f["codigo"].startswith("desconocido") or f["codigo"] == "otros_sin_padre":
                errores.append(f"p{c['pagina']}: renglón no reconocido en {nombre}: '{f['etiqueta']}'")
            d = {"codigo": f["codigo"], "etiqueta": f["etiqueta"]}
            for k, v in zip(COLS7, f["miles"]):
                d[k] = v if k.startswith("pct") else mill(v)
            filas.append(d)
        salida[nombre] = {"pagina": c["pagina"], "filas": filas}
    inf["cuadros"] = salida

    # ---- cuadro mensual
    err_aux = [] if oficial else errores      # el informe oficial no trae estos cuadros
    mm = leer_mensual(pdf, err_aux)
    if mm:
        inf["mensual"] = {
            "pagina": mm["pagina"],
            "nota": mm["nota"],
            "meses": [{"mes": k, "efectivo": mill(v[0]), "documentos_fiscales": mill(v[1]),
                       "total": mill(v[2])} for k, v in sorted(mm["filas"].items())],
            "total_acumulado": None if not mm["total"] else
            {"efectivo": mill(mm["total"][0]), "documentos_fiscales": mill(mm["total"][1]),
             "total": mill(mm["total"][2])},
        }
        inf["condicion_por_mes"] = condicion_por_mes(mm["nota"], mes)
    # ---- documentos fiscales en balboas
    if oficial and err_aux:
        avisos.extend(err_aux)
    # el detalle de documentos fiscales es complementario (el cuadro mensual ya
    # trae sus totales): si falta o no se lee, se avisa y se sigue
    dd = leer_documentos(pdf, avisos)
    if dd:
        inf["documentos_fiscales"] = {
            "pagina": dd["pagina"], "unidad_original": "balboas",
            "filas": [{"etiqueta": f["etiqueta"],
                       **{k: mill(v, 1e6) for k, v in zip(DOC_COLS, f["balboas"])}}
                      for f in dd["filas"]]}
    # ---- texto del resumen (solo para comparar)
    m = re.search(r"ingresos corrientes (?:en efectivo )?(?:correspondientes al mes de \w+ )?"
                  r"(?:reflejan un total de|totalizaron)\s*b/\.\s*([\d,\.]+)\s*millones", texto1)
    if m:
        inf["texto_resumen_corrientes_mes"] = float(m.group(1).replace(",", ""))
    inf["edicion"] = f"{inf['periodo']}_{inf['condicion']}" if per else None
    inf["validaciones"] = validar(inf, errores, avisos)
    inf["errores"] = errores
    inf["avisos"] = avisos
    fallas = [v for v in inf["validaciones"] if not v["ok"] and v["nivel"] == "error"]
    inf["celdas_dudosas"] = sorted({tuple(map(str, c)) for v in fallas for c in v["celdas"]})
    # Publicable: se leyó todo y cada falla quedó acotada a celdas concretas,
    # que no se publican. Cualquier otra falla detiene el informe completo.
    inf["publicable"] = not errores and all(v["celdas"] for v in fallas)
    return inf


def condicion_por_mes(nota, mes):
    """'Cifras Oficiales enero-febrero, preliminares marzo-diciembre' -> por mes."""
    res = {}
    if not nota:
        return res
    t = nota.lower()
    for cond in ("oficial", "preliminar"):
        for m in re.finditer(cond + r"[a-z]*\s+([a-z]+)(?:\s*-\s*([a-z]+))?", t):
            a = m.group(1)
            b = m.group(2) or a
            if a in MESES and b in MESES:
                for k in range(MESES.index(a) + 1, MESES.index(b) + 2):
                    res[k] = cond
    if not res and "preliminar" in t:
        res = {k: "preliminar" for k in range(1, (mes or 12) + 1)}
    return res


# --------------------------------------------------------------- validación
def validar(inf, errores, avisos):
    V = []

    def chk(regla, ok, detalle, nivel="error", celdas=None, dif=None):
        """celdas: [(cuadro, codigo, columna)] que quedan en duda si falla.
        Una diferencia de hasta B/.5 mil dentro del propio informe es redondeo
        del DGI: se anota como aviso, no como error."""
        if not ok and dif is not None and abs(dif) <= 0.005 and nivel == "error":
            nivel = "aviso"
            regla += " (diferencia menor en el informe)"
        V.append({"regla": regla, "ok": bool(ok), "nivel": nivel, "detalle": detalle,
                  "celdas": [] if ok else (celdas or [])})

    C = inf.get("cuadros", {})
    esperados = ["resumen_mes_efectivo", "resumen_acumulado_efectivo",
                 "tributarios_mes_efectivo", "tributarios_acumulado_efectivo",
                 "no_tributarios_mes_efectivo", "no_tributarios_acumulado_efectivo"]
    if inf.get("condicion") == "oficial":   # el informe anual solo trae acumulados
        esperados = [e for e in esperados if "_acumulado_" in e]
    if inf.get("mes") == 1:                 # en enero el mes es el acumulado
        esperados = [e for e in esperados if "_mes_" in e]
    for e in esperados:
        chk("cuadro presente", e in C, e)

    # 1) sumas internas en cada cuadro y columna
    for nombre, c in C.items():
        val = {f["codigo"]: f for f in c["filas"]}
        for padre, hijos in SUMAS.items():
            if padre not in val or not all(h in val for h in hijos):
                continue
            for col in ("efectivo", "presupuesto", "anio_anterior"):
                if val[padre][col] is None:
                    continue
                s = sum(val[h][col] or 0 for h in hijos)
                tol = 0.0006 * (len(hijos) + 1)
                d = s - (val[padre][col] or 0)
                chk(f"{padre} = suma de componentes", abs(d) <= tol,
                    f"{nombre}.{col}: {val[padre][col]:.3f} vs suma {s:.3f} (dif {d:+.3f})",
                    celdas=[(nombre, k, c2) for k in [padre] + hijos for c2 in
                            ([col] + (["dif_anio_anterior", "pct_anio_anterior"] if col == "anio_anterior"
                                      else ["dif_presupuesto", "pct_presupuesto"] if col == "presupuesto"
                                      else []))], dif=d)
        # columnas de diferencia (detecta cifras corridas de columna)
        for f in c["filas"]:
            for a, b, dcol, pcol in (("efectivo", "presupuesto", "dif_presupuesto", "pct_presupuesto"),
                                     ("efectivo", "anio_anterior", "dif_anio_anterior", "pct_anio_anterior")):
                if f[a] is None or f[b] is None or f[dcol] is None:
                    continue
                d = f[a] - f[b]
                ok = abs(d - f[dcol]) <= 0.0016
                pct_ok = True
                if f[b] and f[pcol] is not None:
                    pct_ok = abs(100 * d / f[b] - f[pcol]) <= 0.15 or abs(f[pcol]) > 999
                if not (ok and pct_ok):
                    chk("columnas de diferencia coherentes", False,
                        f"{nombre}/{f['codigo']}: {f[a]} - {f[b]} = {d:.3f}; informe {f[dcol]} ({f[pcol]}%)",
                        celdas=[(nombre, f["codigo"], b), (nombre, f["codigo"], dcol),
                                (nombre, f["codigo"], pcol)],
                        dif=(d - f[dcol]) if pct_ok else 1)
        chk("columnas de diferencia coherentes", True, nombre)

    # 2) el mismo renglón coincide entre cuadros resumen y detalle
    for alc in ("mes", "acumulado"):
        for base in ("efectivo", "con_documentos"):
            r = C.get(f"resumen_{alc}_{base}")
            if not r:
                continue
            rv = {f["codigo"]: f for f in r["filas"]}
            for det, cods in (("tributarios", ["tributarios", "directos", "indirectos"]),
                              ("no_tributarios", ["no_tributarios", "tasas_derechos_otros",
                                                  "utilidades_aportes", "otros_corrientes"])):
                d = C.get(f"{det}_{alc}_{base}")
                if not d:
                    continue
                dv = {f["codigo"]: f for f in d["filas"]}
                for k in cods:
                    if k in rv and k in dv:
                        for col in ("efectivo", "presupuesto", "anio_anterior"):
                            if rv[k][col] is None or dv[k][col] is None:
                                continue
                            chk("resumen = detalle", abs(rv[k][col] - dv[k][col]) <= 0.0015,
                                f"{alc}/{base}/{k}/{col}: {rv[k][col]} vs {dv[k][col]}")

    # 3) cuadro mensual: suma de meses = acumulado; efectivo+documentos = total
    M = inf.get("mensual")
    mes = inf.get("mes")
    if M:
        meses = M["meses"]
        chk("cuadro mensual completo", [x["mes"] for x in meses] == list(range(1, (mes or 0) + 1)),
            f"meses {[x['mes'] for x in meses]} para informe de mes {mes}")
        for x in meses:
            dd = x["efectivo"] + x["documentos_fiscales"] - x["total"]
            chk("efectivo + documentos = total (por mes)", abs(dd) <= 0.0016,
                f"mes {x['mes']}: {x['efectivo']} + {x['documentos_fiscales']} vs {x['total']}",
                celdas=[("mensual", x["mes"], "todo")], dif=dd)
        T = M["total_acumulado"]
        if T:
            for k in ("efectivo", "documentos_fiscales", "total"):
                s = sum(x[k] for x in meses)
                chk("suma de meses = acumulado", abs(s - T[k]) <= 0.0006 * (len(meses) + 1),
                    f"{k}: suma {s:.3f} vs total {T[k]:.3f}")
        rm = C.get("resumen_mes_efectivo")
        ra = C.get("resumen_acumulado_efectivo")
        if rm and meses and meses[-1]["mes"] == mes:
            v = next(f for f in rm["filas"] if f["codigo"] == "total_corrientes")["efectivo"]
            chk("mes del cuadro mensual = cuadro del mes", abs(meses[-1]["efectivo"] - v) <= 0.0015,
                f"{meses[-1]['efectivo']} vs {v}")
        if ra and T:
            v = next(f for f in ra["filas"] if f["codigo"] == "total_corrientes")["efectivo"]
            chk("acumulado del cuadro mensual = cuadro acumulado", abs(T["efectivo"] - v) <= 0.0015,
                dif=T["efectivo"] - v, celdas=[("mensual", "total", "efectivo")],
                detalle=
                f"{T['efectivo']} vs {v}")
        rd = C.get("resumen_acumulado_con_documentos")
        if rd and T and ra:
            v1 = next(f for f in rd["filas"] if f["codigo"] == "total_corrientes")["efectivo"]
            v0 = next(f for f in ra["filas"] if f["codigo"] == "total_corrientes")["efectivo"]
            chk("con documentos - efectivo = documentos fiscales",
                abs((v1 - v0) - T["documentos_fiscales"]) <= 0.003,
                dif=(v1 - v0) - T["documentos_fiscales"],
                celdas=[("resumen_acumulado_con_documentos", "total_corrientes", "efectivo")],
                detalle=
                f"{v1:.3f} - {v0:.3f} = {v1 - v0:.3f} vs {T['documentos_fiscales']:.3f}")
    # 4) documentos fiscales (balboas) contra el cuadro mensual
    D = inf.get("documentos_fiscales")
    if D and M and inf.get("condicion") != "oficial":
        tot = next((f for f in D["filas"] if norm(f["etiqueta"]) == "ingresoscorrientes"), None)
        if tot:
            chk("documentos fiscales del mes (B/.) = cuadro mensual",
                abs((tot["total_mes"] or 0) - M["meses"][-1]["documentos_fiscales"]) <= 0.0015,
                f"{tot['total_mes']} vs {M['meses'][-1]['documentos_fiscales']}", nivel="aviso")
            if M["total_acumulado"] and tot["acumulado"] is not None:
                chk("documentos fiscales acumulados (B/.) = cuadro mensual",
                    abs((tot["acumulado"] or 0) - M["total_acumulado"]["documentos_fiscales"]) <= 0.0015,
                    f"{tot['acumulado']} vs {M['total_acumulado']['documentos_fiscales']}", nivel="aviso")
            cr = (tot["creditos_fiscales"] or 0) + (tot["certificados"] or 0)
            chk("créditos + certificados = total del mes (documentos)",
                abs(cr - (tot["total_mes"] or 0)) <= 0.000002, f"{cr} vs {tot['total_mes']}", nivel="aviso")
    # 5) texto del resumen contra el cuadro (solo aviso)
    if "texto_resumen_corrientes_mes" in inf and C.get("resumen_mes_efectivo"):
        v = next(f for f in C["resumen_mes_efectivo"]["filas"] if f["codigo"] == "total_corrientes")["efectivo"]
        tx = inf["texto_resumen_corrientes_mes"]
        chk("texto del resumen = cuadro", abs(tx - v) <= 0.051,
            f"texto dice {tx} millones; el cuadro dice {v:.3f}", nivel="aviso")
    return V


# ------------------------------------------- conciliación entre ediciones
def conciliar(informes):
    """Compara lo que dicen distintas ediciones sobre un mismo dato:
      * la columna 'año anterior' de un informe contra el informe de ese mes
        del año anterior;
      * el informe oficial contra el preliminar del mismo periodo;
      * los meses repetidos en los cuadros mensuales de distintos informes."""
    R = []

    def comparar(inf, otro, col_inf, motivo):
        for nombre, c in inf["cuadros"].items():
            pc = otro["cuadros"].get(nombre)
            if not pc:
                continue
            pv = {f["codigo"]: f for f in pc["filas"]}
            for f in c["filas"]:
                if f["codigo"] in pv:
                    x, y = f[col_inf], pv[f["codigo"]]["efectivo"]
                    R.append({"motivo": motivo, "informe": inf["edicion"],
                              "comparado_con": otro["edicion"], "cuadro": nombre,
                              "codigo": f["codigo"], "dice_ahora": x, "dijo_antes": y,
                              "revision": None if x is None or y is None else round(x - y, 6)})

    for inf in informes:
        if not inf.get("periodo"):
            continue
        for otro in informes:
            if otro is inf or not otro.get("periodo"):
                continue
            if otro["periodo"] == f"{inf['anio'] - 1}-{inf['mes']:02d}":
                comparar(inf, otro, "anio_anterior", "columna año anterior vs informe de entonces")
            if otro["periodo"] == inf["periodo"] and inf["condicion"] == "oficial" \
                    and otro["condicion"] == "preliminar":
                comparar(inf, otro, "efectivo", "oficial vs preliminar")
    vistos = {}
    for inf in sorted(informes, key=lambda i: i.get("edicion") or ""):
        for x in (inf.get("mensual") or {}).get("meses", []):
            k = f"{inf['anio']}-{x['mes']:02d}"
            vistos.setdefault(k, []).append((inf["edicion"], x["efectivo"]))
    for k, lst in sorted(vistos.items()):
        for (p0, e0), (p1, e1) in zip(lst, lst[1:]):
            R.append({"motivo": "mes repetido en cuadros mensuales", "informe": p1,
                      "comparado_con": p0, "cuadro": "mensual", "codigo": f"efectivo {k}",
                      "dice_ahora": e1, "dijo_antes": e0, "revision": round(e1 - e0, 6)})
    return R


# --------------------------------------------------------------- página web
def listar_informes(html_txt):
    """Devuelve [{seccion, etiqueta, mes, url}] tal como aparecen en la página."""
    out, sec = [], None
    for tok in re.finditer(r"Resultado[s]?\s*-?\s*(\d{4})|<a href=\"(IFP/[^\"]+)\"[^>]*>\s*([^<]*)<",
                           html_txt):
        if tok.group(1):
            sec = int(tok.group(1))
            continue
        et = html.unescape(tok.group(3)).strip()
        mes = MESES.index(norm(et)) + 1 if norm(et) in MESES else None
        out.append({"seccion": sec, "etiqueta": et, "mes": mes,
                    "url": urllib.parse.urljoin(BASE, urllib.parse.quote(tok.group(2)))})
    return out


def bajar(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read()


def guardar(ruta, obj, compacto=False):
    with open(ruta, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=None if compacto else 1)


def modo_web(salida, desde, todos):
    """Lee la página oficial, baja cada PDF y procesa solo los que cambiaron (por SHA-256).

    datos/dgi/ediciones/<edición>.json   versión vigente de cada edición
    datos/dgi/ediciones/historial/       versiones anteriores, si la DGI cambia un PDF
    datos/dgi/registro.json              qué se revisó, cuándo y con qué resultado
    Un informe con errores no se publica: queda en el registro y se avisa.
    """
    ed_dir = os.path.join(salida, "ediciones")
    hist = os.path.join(ed_dir, "historial")
    os.makedirs(hist, exist_ok=True)
    reg_p = os.path.join(salida, "registro.json")
    reg = json.load(open(reg_p, encoding="utf-8")) if os.path.exists(reg_p) else {"pdfs": {}}
    ahora = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    reg["ultima_revision"], reg["corrida"] = ahora, []
    try:
        pag = bajar(PAGINA).decode("utf-8", "replace")
    except Exception as e:
        reg["corrida"].append({"error": f"no se pudo leer la página: {e}"})
        guardar(reg_p, reg)
        print("ERROR no se pudo leer la página:", e)
        return
    lista = sorted((x for x in listar_informes(pag) if (x["seccion"] or 0) >= desde), key=lambda x: x["seccion"])
    if not lista:
        reg["corrida"].append({"error": "la página no trae enlaces de informes; ¿cambió el formato?"})
        print("ERROR la página no trae enlaces de informes")
    vistos = set()
    tmp = os.path.join(salida, ".tmp.pdf")
    for meta in lista:
        url = meta["url"]
        try:
            crudo = bajar(url)
        except Exception as e:
            reg["corrida"].append({"url": url, "error": f"no se pudo bajar: {e}"})
            print("ERROR no se pudo bajar", url, e)
            continue
        sha = hashlib.sha256(crudo).hexdigest()
        previo = reg["pdfs"].get(url, {})
        if sha in vistos:            # mismo PDF enlazado dos veces (p. ej. meses que aún no salen)
            reg["pdfs"][url] = {**previo, "sha256": sha, "revisado": ahora, "nota": "mismo PDF que otro enlace"}
            continue
        vistos.add(sha)
        if not todos and previo.get("sha256") == sha and previo.get("edicion") and \
                os.path.exists(os.path.join(ed_dir, previo["edicion"] + ".json")):
            previo["revisado"] = ahora
            continue
        with open(tmp, "wb") as f:
            f.write(crudo)
        try:
            inf = procesar_pdf(tmp, meta)
        except Exception as e:
            inf = {"publicable": False, "errores": [f"falló la lectura del PDF: {e}"], "edicion": None}
        if inf.get("fuente"):
            inf["fuente"]["archivo"] = os.path.basename(urllib.parse.unquote(url))
        ed = inf.get("edicion")
        reg["pdfs"][url] = {"etiqueta": meta["etiqueta"], "seccion": meta["seccion"], "sha256": sha,
                            "bytes": len(crudo), "revisado": ahora, "edicion": ed,
                            "publicable": inf["publicable"], "errores": inf.get("errores", [])}
        if not inf["publicable"] or not ed:
            reg["corrida"].append({"url": url, "error": "; ".join(inf.get("errores", []))[:500]})
            print("ERROR", url, inf.get("errores"))
            continue
        destino = os.path.join(ed_dir, ed + ".json")
        if os.path.exists(destino):
            viejo = json.load(open(destino, encoding="utf-8"))
            vsha = viejo["fuente"]["sha256"]
            if vsha == sha:
                continue
            if (viejo["fuente"].get("pdf_creado") or "") > (inf["fuente"].get("pdf_creado") or ""):
                reg["corrida"].append({"url": url, "aviso": f"{ed}: ya hay una versión más nueva; se ignora este PDF"})
                continue
            os.replace(destino, os.path.join(hist, f"{ed}__{vsha[:12]}.json"))
            reg["corrida"].append({"url": url, "cambio": f"{ed}: la DGI cambió el PDF; la versión anterior pasa a historial"})
        guardar(destino, inf)
        reg["corrida"].append({"url": url, "nuevo": ed})
        print("OK  ", ed, url)
    if os.path.exists(tmp):
        os.remove(tmp)
    informes = [json.load(open(p, encoding="utf-8")) for p in sorted(glob.glob(os.path.join(ed_dir, "20*_*.json")))]
    guardar(os.path.join(salida, "conciliacion.json"), conciliar(informes))
    guardar(reg_p, reg)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("salida")
    ap.add_argument("--pdf", nargs="*", help="PDF locales (pruebas)")
    ap.add_argument("--desde", type=int, default=2024, help="primer año de la página a revisar")
    ap.add_argument("--todos", action="store_true", help="volver a procesar aunque el PDF no cambie")
    args = ap.parse_args()
    os.makedirs(args.salida, exist_ok=True)
    if args.pdf is None:
        return modo_web(args.salida, args.desde, args.todos)
    informes = []
    for ruta in args.pdf or []:
        inf = procesar_pdf(ruta)
        informes.append(inf)
        print(("OK   " if inf["publicable"] else "ERROR"), ruta, inf["periodo"],
              f"{sum(not v['ok'] for v in inf['validaciones'])} validaciones fallidas")
        with open(os.path.join(args.salida, f"{inf['edicion'] or os.path.basename(ruta)}.json"),
                  "w", encoding="utf-8") as f:
            json.dump(inf, f, ensure_ascii=False, indent=1)
    with open(os.path.join(args.salida, "conciliacion.json"), "w", encoding="utf-8") as f:
        json.dump(conciliar(informes), f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
