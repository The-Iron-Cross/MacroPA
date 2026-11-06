"""Descarga y valida la deuda pública mensual publicada por la Dirección de
Financiamiento Público (DFP) del MEF de Panamá.

Fuente: la sección "Base de Datos" de https://fpublico.mef.gob.pa, que muestra
reportes de SQL Server Reporting Services. Cada reporte se pide en
fpweb.mef.gob.pa/Views/Reporte/ReportViewer.aspx y luego se exporta en XML con
la URL "ExportUrlBase" que trae la misma página (el botón Exportar del sitio).

Reportes que usa este prototipo:
  * Rpt_SaldoDeudaPublica (ReporteId 4): saldo inicial, desembolsos,
    amortización, intereses, comisiones, variación cambiaria, ajustes y saldo
    final, por tipo de deuda (E/I), tipo de crédito y fuente.
  * Rpt_CostoPromedioPonderadoMensual (ReporteId 44): costo promedio ponderado
    total, por tipo de deuda, por cartera y por rango de tasa.

Uso:
  python3 deuda_mef.py ../datos/deuda            # meses nuevos + últimos 13
  python3 deuda_mef.py ../datos/deuda --completo # toda la historia

Reglas: no se interpola ni se inventa nada. Si un reporte no se puede leer o
no cuadra por dentro (componentes que no suman al total), se conserva el
último dato válido y se anota el error en registro.json.
"""
import concurrent.futures as cf
import datetime as dt
import http.cookiejar
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

API = "https://fpapi.mef.gob.pa/api"
WEB = "https://fpweb.mef.gob.pa"
SITIO = "https://fpublico.mef.gob.pa/es/Paginas/base-datos.aspx"
UA = "MacroPA-datos/1.0 (+https://the-iron-cross.github.io/MacroPA/)"
TOL = 0.01          # millones de USD, para sumas y flujos
TOL_CPP = 0.0005    # 0.05 puntos porcentuales
HOY = dt.date.today().isoformat()

SALDOS = {"id": 4, "nombre": "Rpt_SaldoDeudaPublica",
          "titulo": "Deuda Pública Total (Mensual)"}
CPP = {"id": 44, "nombre": "Rpt_CostoPromedioPonderadoMensual",
       "titulo": "Indicadores de Costo Promedio Ponderado"}

UNIVERSO = ("Deuda pública total registrada por la DFP-MEF: Gobierno Central más "
            "instituciones descentralizadas con deuda registrada (el reporte "
            "'Estado de la Deuda Pública Total por Sector Público' la separa). "
            "No es la deuda del SPNF ni del Sector Público consolidado.")

# Campos de cada nivel del reporte de saldos (los nombres los fija el RDL del MEF).
CAMPOS = ["saldo_inicial", "desembolsos", "amortizacion", "intereses", "prima",
          "comisiones", "pagos_total", "variacion_cambiaria", "ajustes_descuentos",
          "saldo_final"]
NIVELES = {
    "TIPO_DEUDA": ("DESCRIPCION_DEUDA", ["SALDO_ANTERIOR2", "DESEMBOLSOS2", "P_CAPITAL2",
                   "P_INTERESES2", "P_PRIMA1", "P_COMISIONES2", "Textbox50", "VARIACION2",
                   "DESCUENTO_PRECIO2", "MONTO1"]),
    "TIPOCREDITO_ID": ("TIPO_CREDITO", ["SALDO_ANTERIOR1", "DESEMBOLSOS1", "P_CAPITAL1",
                       "P_INTERESES1", "Textbox9", "P_COMISIONES1", "Textbox51", "VARIACION1",
                       "DESCUENTO_PRECIO1", "MONTO2"]),
    "Details": ("FUENTE_FINANCIAMIENTO", ["SALDO_ANTERIOR", "DESEMBOLSOS", "P_CAPITAL",
                "P_INTERESES", "P_PRIMA", "P_COMISIONES", "Textbox52", "VARIACION",
                "DESCUENTO_PRECIO", "SALDO"]),
}


class ErrorLectura(Exception):
    pass


# ---------------------------------------------------------------- descarga
def _abrir():
    op = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    op.addheaders = [("User-Agent", UA)]
    return op


def _get(op, url, intentos=4):
    for i in range(intentos):
        try:
            return op.open(url, timeout=120).read()
        except Exception:
            if i == intentos - 1:
                raise
            time.sleep(2 ** (i + 1))


def api_json(ruta):
    return json.loads(_get(_abrir(), f"{API}/{ruta}"))


def url_reporte(rep, params):
    q = "&".join(f"{k}={urllib.parse.quote(str(v))}" for k, v in params.items())
    return f"{WEB}/Views/Reporte/ReportViewer.aspx?reporte={rep['nombre']}&{q}&IDIOMAS=E"


def bajar_xml(rep, params, intentos=3):
    for i in range(intentos):
        try:
            return _bajar_xml(rep, params)
        except ErrorLectura:
            if i == intentos - 1:
                raise
            time.sleep(5 * (i + 1))


def _bajar_xml(rep, params):
    op = _abrir()
    url = url_reporte(rep, params)
    html = _get(op, url).decode("utf-8", "replace")
    m = re.search(r'"ExportUrlBase":"([^"]*)"', html)
    if not m:
        raise ErrorLectura("la página del reporte no trae ExportUrlBase")
    xml = _get(op, WEB + m.group(1).replace("\\u0026", "&") + "XML")
    return url, xml


def meses_disponibles(rep_id):
    out = []
    for y in api_json(f"Parametros/ParamANIOS?ReporteID={rep_id}"):
        for m in api_json(f"Parametros/ParamMesesDeuda?reporteID={rep_id}&anios={y}&idioma=e"):
            out.append(f"{int(y)}-{int(m['Val']):02d}")
    return sorted(set(out))


# ---------------------------------------------------------------- lectura
def _sin_ns(root):
    for el in root.iter():
        if "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]
    return root


def _num(a, k):
    if k not in a:
        raise ErrorLectura(f"falta el campo {k}")
    return float(a[k])


def _fecha(s):
    d, m, y = s.strip().split("/")
    return f"{y}-{m}-{d}"


def _fila(el, nivel):
    nom, ks = NIVELES[nivel]
    a = el.attrib
    f = {"nombre": a.get(nom, "").strip()}
    for c, k in zip(CAMPOS, ks):
        f[c] = round(_num(a, k), 6)
    return f


def leer_saldos(xml):
    r = _sin_ns(ET.fromstring(xml))
    t = r.find("Tablix1")
    if t is None:
        raise ErrorLectura("no hay tabla Tablix1")
    td = t.findall("./TIPO_DEUDA_Collection/TIPO_DEUDA")
    if len(td) != 1:
        raise ErrorLectura(f"se esperaba 1 tipo de deuda y hay {len(td)}")
    out = _fila(td[0], "TIPO_DEUDA")
    out["fecha_inicial"] = _fecha(t.attrib["Textbox29"])
    out["fecha_corte"] = _fecha(t.attrib["Textbox32"])
    out["componentes"] = []
    for tc in td[0].findall("./TIPOCREDITO_ID_Collection/TIPOCREDITO_ID"):
        c = _fila(tc, "TIPOCREDITO_ID")
        c["fuentes"] = [_fila(d, "Details") for d in tc.findall("./Details_Collection/Details")]
        out["componentes"].append(c)
    nota = (r.attrib.get("Textbox21") or "").strip()
    if nota and nota.lower() not in ("prueba",):
        out["nota_mef"] = re.sub(r"\s+", " ", nota)
    return out


def leer_cpp(xml):
    r = _sin_ns(ET.fromstring(xml))
    t = r.find("Tablix1")
    if t is None:
        raise ErrorLectura("no hay tabla Tablix1")
    a = t.attrib
    out = {"fecha_corte": _fecha(a["Textbox31"]), "saldo_total": round(_num(a, "Textbox56"), 6),
           "cpp_total": _num(a, "Textbox57") * 100, "categorias": []}
    for c in t.findall("./CATEGORIA_ID_Collection/CATEGORIA_ID"):
        out["categorias"].append({
            "nombre": c.attrib["CATEGORIA"].strip(),
            "saldo": round(_num(c.attrib, "SALDO"), 6),
            "cpp": _num(c.attrib, "PROMEDIO") * 100,
            "detalle": [{"nombre": d.attrib["FUENTE_FINANCIAMIENTO"].strip(),
                         "saldo": round(_num(d.attrib, "SALDO1"), 6),
                         "cpp": _num(d.attrib, "PROMEDIO1") * 100}
                        for d in c.findall("./Details_Collection/Details")]})
    if not out["categorias"]:
        raise ErrorLectura("el reporte no trae categorías")
    return out


# ---------------------------------------------------------------- validaciones
def _chk(nombre, esperado, obtenido, tol, dura):
    dif = obtenido - esperado
    return {"prueba": nombre, "esperado": round(esperado, 6), "obtenido": round(obtenido, 6),
            "diferencia": round(dif, 6), "ok": abs(dif) <= tol, "obligatoria": dura}


def _flujo(f):
    return (f["saldo_inicial"] + f["desembolsos"] - f["amortizacion"]
            + f["variacion_cambiaria"] + f["ajustes_descuentos"])


def validar_tipo(t, etiqueta, carga_inicial):
    v = []
    for campo in ("saldo_inicial", "saldo_final", "desembolsos", "amortizacion", "intereses"):
        v.append(_chk(f"{etiqueta}: {campo} = suma por tipo de crédito",
                      t[campo], sum(c[campo] for c in t["componentes"]), TOL, True))
    for c in t["componentes"]:
        v.append(_chk(f"{etiqueta}: {c['nombre']} saldo = suma por fuente",
                      c["saldo_final"], sum(x["saldo_final"] for x in c["fuentes"]), TOL, True))
    v.append(_chk(f"{etiqueta}: pagos = amortización + intereses + prima + comisiones",
                  t["pagos_total"], t["amortizacion"] + t["intereses"] + t["prima"] + t["comisiones"],
                  TOL, False))
    if not carga_inicial:
        v.append(_chk(f"{etiqueta}: saldo final = inicial + desembolsos − amortización "
                      "+ variación cambiaria + ajustes", t["saldo_final"], _flujo(t), TOL, False))
    return v


def validar_periodo(p, previo):
    v = []
    ini = p["externa"]["saldo_inicial"] == 0 and p["interna"]["saldo_inicial"] == 0
    for k in ("externa", "interna"):
        v += validar_tipo(p[k], k, ini)
        if previo and previo.get(k):
            v.append(_chk(f"{k}: saldo inicial = saldo final del mes anterior",
                          previo[k]["saldo_final"], p[k]["saldo_inicial"], TOL, False))
    tot = p["externa"]["saldo_final"] + p["interna"]["saldo_final"]
    for k in ("externa", "interna"):
        p["participacion_pct_" + k] = round(100 * p[k]["saldo_final"] / tot, 4)
    v.append(_chk("participación externa + interna = 100%",
                  100.0, p["participacion_pct_externa"] + p["participacion_pct_interna"], 0.01, False))
    if ini:
        p["notas"] = ["Primer mes del sistema de la DFP: el saldo inicial viene en 0 y el saldo "
                      "de apertura aparece en la columna de variación/ajustes. No se usa para flujos."]
    return v


def validar_cpp(c, saldos):
    v = []
    for cat in c["categorias"]:
        if cat["detalle"]:
            s = sum(d["saldo"] for d in cat["detalle"])
            v.append(_chk(f"CPP {cat['nombre']}: saldo = suma del detalle", cat["saldo"], s, TOL, True))
            if s > 0:
                w = sum(d["saldo"] * d["cpp"] for d in cat["detalle"]) / s
                v.append(_chk(f"CPP {cat['nombre']}: promedio = ponderado del detalle",
                              cat["cpp"], w, TOL_CPP * 100, False))
    tipos = [d["cpp"] for x in c["categorias"] if x["nombre"].lower().endswith("tipo de deuda")
             for d in x["detalle"] if d["saldo"] > 0]
    if tipos:
        lo, hi = min(tipos), max(tipos)
        dentro = lo - TOL_CPP * 100 <= c["cpp_total"] <= hi + TOL_CPP * 100
        v.append({"prueba": "CPP total entre el CPP externo y el interno", "esperado": [lo, hi],
                  "obtenido": c["cpp_total"], "diferencia": None, "ok": dentro, "obligatoria": False,
                  "clave": "cpp_total_en_rango"})
    bandas = [x for x in c["categorias"] if x["nombre"].lower().startswith("tasa")]
    if bandas:
        v.append(_chk("CPP: saldo total = suma de rangos de tasa", c["saldo_total"],
                      sum(b["saldo"] for b in bandas), TOL, False))
    if saldos:
        tot = saldos["externa"]["saldo_final"] + saldos["interna"]["saldo_final"]
        v.append(_chk("CPP: saldo total = deuda externa + interna del reporte de saldos",
                      tot, c["saldo_total"], 1.0, False))
    return v


# ---------------------------------------------------------------- almacenamiento
def cargar(ruta, defecto):
    try:
        with open(ruta, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return defecto


def guardar(ruta, obj):
    tmp = ruta + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
        f.write("\n")
    os.replace(tmp, ruta)


def comparar(viejo, nuevo, ruta=""):
    """Lista de cambios numéricos entre dos versiones del mismo período."""
    cambios = []
    if isinstance(viejo, dict) and isinstance(nuevo, dict):
        for k in set(viejo) | set(nuevo):
            if k in ("validaciones", "estado", "primera_deteccion", "ultima_descarga", "url"):
                continue
            cambios += comparar(viejo.get(k), nuevo.get(k), f"{ruta}.{k}" if ruta else k)
    elif isinstance(viejo, list) and isinstance(nuevo, list):
        idx = lambda L: {(x.get("nombre") if isinstance(x, dict) else i): x for i, x in enumerate(L)}
        a, b = idx(viejo), idx(nuevo)
        for k in set(a) | set(b):
            cambios += comparar(a.get(k), b.get(k), f"{ruta}[{k}]")
    elif isinstance(viejo, (int, float)) and isinstance(nuevo, (int, float)):
        if abs(viejo - nuevo) > 1e-6:
            cambios.append({"campo": ruta, "antes": viejo, "despues": nuevo})
    elif viejo != nuevo:
        cambios.append({"campo": ruta, "antes": viejo, "despues": nuevo})
    return cambios


def guardar_original(base, rep, periodo, sufijo, xml):
    d = os.path.join(base, "originales", rep["nombre"])
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, f"{periodo}{sufijo}.xml"), "wb") as f:
        f.write(xml)


def _cpp_total_confiable(c):
    """CPP total para la app; None si el propio MEF lo publica fuera de rango."""
    if not c:
        return None
    for v in c["validaciones"]:
        if v.get("clave") == "cpp_total_en_rango" and not v["ok"]:
            return None
    return round(c["cpp_total"], 3)


def _cod(x):
    return "o" if "observ" in x["estado"] else "v"


def _cpp_tipo(c, tipo):
    if not c:
        return None
    for cat in c["categorias"]:
        if cat["nombre"].lower().endswith("tipo de deuda"):
            for d in cat["detalle"]:
                if d["nombre"].lower().endswith(tipo):
                    return round(d["cpp"], 3)
    return None


# ---------------------------------------------------------------- proceso
def bajar_periodo_saldos(periodo):
    y, m = periodo.split("-")
    res, urls, xmls = {}, {}, {}
    for td, k in (("E", "externa"), ("I", "interna")):
        url, xml = bajar_xml(SALDOS, {"TIPO_DEUDA": td, "ANIOS": int(y), "MES": int(m)})
        res[k] = leer_saldos(xml)
        urls[k] = url
        xmls[td] = xml
    return res, urls, xmls


def bajar_periodo_cpp(periodo):
    y, m = periodo.split("-")
    url, xml = bajar_xml(CPP, {"ANIOS": int(y), "MES": int(m)})
    return leer_cpp(xml), url, xml


def main():
    base = sys.argv[1]
    completo = "--completo" in sys.argv
    os.makedirs(base, exist_ok=True)
    f_sal, f_cpp = os.path.join(base, "saldos_mensuales.json"), os.path.join(base, "cpp_mensual.json")
    f_rev, f_reg = os.path.join(base, "revisiones.json"), os.path.join(base, "registro.json")
    sal = cargar(f_sal, {"periodos": {}})
    cpp = cargar(f_cpp, {"periodos": {}})
    revisiones = cargar(f_rev, [])
    registro = cargar(f_reg, {"ejecuciones": []})
    errores, nuevos, revisados = [], [], []

    disp_s, disp_c = meses_disponibles(SALDOS["id"]), meses_disponibles(CPP["id"])

    def elegir(disp, guardados):
        if completo:
            return disp
        recientes = disp[-13:]
        return sorted(set(recientes) | {p for p in disp if p not in guardados})

    pedir_s, pedir_c = elegir(disp_s, sal["periodos"]), elegir(disp_c, cpp["periodos"])

    with cf.ThreadPoolExecutor(4) as ex:
        fut_s = {ex.submit(bajar_periodo_saldos, p): p for p in pedir_s}
        fut_c = {ex.submit(bajar_periodo_cpp, p): p for p in pedir_c}
        res_s, res_c = {}, {}
        for f in cf.as_completed(list(fut_s) + list(fut_c)):
            p = fut_s.get(f) or fut_c.get(f)
            tipo = "saldos" if f in fut_s else "cpp"
            try:
                (res_s if tipo == "saldos" else res_c)[p] = f.result()
            except Exception as e:  # se conserva el dato anterior
                errores.append({"reporte": tipo, "periodo": p, "error": f"{type(e).__name__}: {e}"})

    # Saldos, en orden, para poder comparar con el mes anterior.
    for p in sorted(res_s):
        data, urls, xmls = res_s[p]
        previo_p = max([q for q in set(sal["periodos"]) | set(res_s) if q < p], default=None)
        previo = sal["periodos"].get(previo_p) if previo_p else None
        if previo_p in res_s:
            previo = {"externa": res_s[previo_p][0]["externa"], "interna": res_s[previo_p][0]["interna"]}
        if data["externa"]["fecha_corte"] != data["interna"]["fecha_corte"]:
            errores.append({"reporte": "saldos", "periodo": p, "error": "fechas de corte distintas E/I"})
            continue
        nuevo = {"periodo": p, "fecha_corte": data["externa"]["fecha_corte"],
                 "externa": data["externa"], "interna": data["interna"], "url": urls}
        nuevo["total"] = {"saldo_final": round(data["externa"]["saldo_final"] + data["interna"]["saldo_final"], 6),
                          "metodo": "externa + interna del mismo reporte"}
        v = validar_periodo(nuevo, previo)
        malas = [x for x in v if x["obligatoria"] and not x["ok"]]
        if malas:
            errores.append({"reporte": "saldos", "periodo": p,
                            "error": "no cuadra por dentro; se conserva el dato anterior",
                            "pruebas": malas})
            continue
        nuevo["validaciones"] = v
        nuevo["estado"] = "validado" if all(x["ok"] for x in v) else "validado_con_observaciones"
        viejo = sal["periodos"].get(p)
        nuevo["primera_deteccion"] = viejo["primera_deteccion"] if viejo else HOY
        nuevo["ultima_descarga"] = HOY
        if viejo:
            ch = comparar(viejo, nuevo)
            if ch:
                revisiones.append({"reporte": SALDOS["nombre"], "periodo": p, "detectado": HOY,
                                   "cambios": ch})
                revisados.append(p)
        else:
            nuevos.append(p)
        sal["periodos"][p] = nuevo
        for td, xml in xmls.items():
            guardar_original(base, SALDOS, p, "_" + td, xml)

    for p in sorted(res_c):
        data, url, xml = res_c[p]
        v = validar_cpp(data, sal["periodos"].get(p))
        malas = [x for x in v if x["obligatoria"] and not x["ok"]]
        if malas:
            errores.append({"reporte": "cpp", "periodo": p,
                            "error": "no cuadra por dentro; se conserva el dato anterior", "pruebas": malas})
            continue
        nuevo = dict(data, periodo=p, url=url, validaciones=v,
                     estado="validado" if all(x["ok"] for x in v) else "validado_con_observaciones")
        viejo = cpp["periodos"].get(p)
        nuevo["primera_deteccion"] = viejo["primera_deteccion"] if viejo else HOY
        nuevo["ultima_descarga"] = HOY
        if viejo:
            ch = comparar(viejo, nuevo)
            if ch:
                revisiones.append({"reporte": CPP["nombre"], "periodo": p, "detectado": HOY, "cambios": ch})
                revisados.append("cpp " + p)
        else:
            nuevos.append("cpp " + p)
        cpp["periodos"][p] = nuevo
        guardar_original(base, CPP, p, "", xml)

    meta = {"fuente": "Ministerio de Economía y Finanzas, Dirección de Financiamiento Público",
            "sitio": SITIO, "unidad": "millones de USD", "universo": UNIVERSO,
            "fecha_publicacion": "El MEF no publica la fecha de publicación de cada mes; "
                                 "'primera_deteccion' es el día en que este proceso lo vio por primera vez.",
            "actualizado": HOY}
    sal.update(meta, reporte=SALDOS,
               signos="saldo_final = saldo_inicial + desembolsos − amortizacion + variacion_cambiaria + ajustes_descuentos",
               periodos=dict(sorted(sal["periodos"].items())))
    cpp.update(meta, reporte=CPP, unidad_cpp="porcentaje anual",
               nota="El costo promedio ponderado lo calcula el MEF; MacroPA no lo recalcula ni "
                    "lo usa para estimar intereses.",
               periodos=dict(sorted(cpp["periodos"].items())))
    guardar(f_sal, sal)
    guardar(f_cpp, cpp)
    guardar(f_rev, revisiones)

    # Resumen compacto para la app.
    filas = []
    for p, d in sal["periodos"].items():
        c = cpp["periodos"].get(p)
        ini = d["externa"]["saldo_inicial"] == 0 and d["interna"]["saldo_inicial"] == 0
        fl = lambda k: None if ini else round(d["externa"][k] + d["interna"][k], 2)
        filas.append([p, round(d["total"]["saldo_final"], 2), round(d["externa"]["saldo_final"], 2),
                      round(d["interna"]["saldo_final"], 2), fl("desembolsos"), fl("amortizacion"),
                      fl("intereses"), _cpp_total_confiable(c),
                      _cpp_tipo(c, "externa"), _cpp_tipo(c, "interna"),
                      _cod(d) + (_cod(c) if c else "-")])
    cpp_solo = [[p, round(c["cpp_total"], 3)] for p, c in cpp["periodos"].items() if p not in sal["periodos"]]
    guardar(os.path.join(base, "resumen.json"), {
        "fuente": meta["fuente"], "sitio": SITIO, "unidad": "millones de USD; CPP en % anual",
        "universo": UNIVERSO, "actualizado": HOY,
        "columnas": ["periodo", "total", "externa", "interna", "desembolsos", "amortizacion",
                     "intereses", "cpp_total", "cpp_externa", "cpp_interna",
                     "estado saldos+cpp (v=validado, o=con observaciones, -=sin dato)"],
        "filas": filas, "cpp_sin_saldo": cpp_solo})

    registro["ejecuciones"] = (registro["ejecuciones"] + [{
        "fecha": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "modo": "completo" if completo else "incremental",
        "pedidos": {"saldos": len(pedir_s), "cpp": len(pedir_c)},
        "nuevos": nuevos, "revisados": revisados, "errores": errores}])[-60:]
    registro["ultimo_periodo"] = {"saldos": max(sal["periodos"], default=None),
                                  "cpp": max(cpp["periodos"], default=None)}
    guardar(f_reg, registro)
    print(f"saldos: {len(sal['periodos'])} meses (último {registro['ultimo_periodo']['saldos']}); "
          f"cpp: {len(cpp['periodos'])} meses; nuevos {len(nuevos)}; revisados {len(revisados)}; "
          f"errores {len(errores)}")
    for e in errores:
        print("ERROR", e["reporte"], e["periodo"], e["error"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
