#!/usr/bin/env python3
"""Genera y valida un candidato de actualización MINERPA para MacroPA.

No publica ni hace commits. Sólo escribe ``index.html`` y ``datos/minerpa`` después
de completar la extracción, la transformación y las comprobaciones de pérdida.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import tempfile
from pathlib import Path

from minerpa_pipeline import (
    BASE, D_AUTOMATICAS, D_PROTEGIDAS, ErrorMinerpa, descargar_todo,
    periodo, sha256_json, transformar,
)

FUENTES_D = {
    "inflacion": ("tasa-de-inflacion-3", "INEC, Contraloría General"),
    "sectores": ("producto-interno-bruto-real-segun-enfoque-de-oferta-base-2018", "INEC, Contraloría General"),
    "spnf": ("balance-del-sector-publico-no-financiero", "Ministerio de Economía y Finanzas"),
    "gc": ("balance-del-gobierno-central", "Ministerio de Economía y Finanzas"),
    "deuda": ("deuda-del-sector-publico", "Ministerio de Economía y Finanzas"),
    "deuda_comp": ("composicion-de-la-deuda-publica", "Ministerio de Economía y Finanzas"),
    "impuestos": ("ingresos-impositivos", "MEF / Dirección General de Ingresos"),
    "acp": ("transferencias-de-la-autoridad-del-canal-al-estado", "Autoridad del Canal de Panamá"),
    "cc": ("saldo-de-la-cuenta-corriente", "INEC, Contraloría General"),
    "com": ("saldo-de-la-cuenta-comercial", "INEC, Contraloría General"),
    "ied": ("inversion-extranjera-directa", "INEC, Contraloría General"),
    "ied_pct": ("inversion-extranjera-directa", "INEC, Contraloría General"),
    "xserv": ("exportaciones-de-bienes-y-servicios", "INEC, Contraloría General"),
    "participacion": ("tasa-de-participacion", "INEC, Contraloría General"),
    "desempleo": ("tasa-de-ocupacion", "INEC, Contraloría General"),
    "informalidad": ("tasa-de-informalidad", "INEC, Contraloría General"),
    "salario": ("mediana-salarial-por-actividad-economica", "INEC, Contraloría General"),
    "pobreza": ("pobreza-general-y-pobreza-extrema", "MEF / INEC"),
    "gini": ("coeficiente-de-gini", "MEF / INEC"),
    "pob_prov": ("pobreza-general-por-provincias", "MEF / INEC"),
    "pobx_prov": ("pobreza-extrema-por-provincias", "MEF / INEC"),
    "pobm_prov": ("pobreza-multidimensional-por-provincias", "MEF / INEC"),
    "ing_prov": ("ingreso-mensual-promedio-de-los-hogares-por-provincias", "INEC, Contraloría General"),
    "poblacion": ("serie-historica-de-poblacion-censo-2023", "INEC, Contraloría General"),
    "demo": ("indicadores-demograficos", "INEC, Contraloría General"),
    "edad": ("indicadores-demograficos", "INEC, Contraloría General"),
}

FUENTES_GRUPO = {
    "B": ("Superintendencia de Bancos de Panamá e INEC", [
        "saldo-de-la-cartera-de-credito-segun-destino",
        "saldo-de-la-cartera-de-credito-segun-sector-economico",
        "saldo-de-los-depositos", "tasa-de-interes-sobre-credito",
        "tasa-de-interes-sobre-depositos", "tasa-de-inflacion-por-grupos",
    ]),
    "DM": ("INEC, Contraloría General", [
        "producto-interno-bruto-real-segun-enfoque-de-demanda-base-2018",
        "producto-interno-bruto-nominal-segun-enfoque-de-demanda-base-2018",
    ]),
}


def cargar_constantes(html):
    out = {}
    for nombre in ("D", "B", "DM"):
        m = re.search(rf"^const {nombre} = (.*);[ \t]*(?://.*)?$", html, re.M)
        if not m:
            raise ErrorMinerpa(f"no se encontró la constante {nombre} en index.html")
        out[nombre] = json.loads(m.group(1))
    return out


def reemplazar_constante(html, nombre, valor):
    nuevo = json.dumps(valor, ensure_ascii=False, separators=(",", ":"))
    patron = re.compile(rf"^const {nombre} = .*;([ \t]*(?://.*)?)$", re.M)
    html2, n = patron.subn(lambda m: f"const {nombre} = {nuevo};{m.group(1)}", html, count=1)
    if n != 1:
        raise ErrorMinerpa(f"no se pudo reemplazar la constante {nombre}")
    return html2


def _es_periodo(x):
    return isinstance(x, int) and 1900 < x < 2200 or isinstance(x, str) and re.match(r"^(\d{4}(-\d{2})?|[a-z]{3}-\d{2})$", x)


def aplanar(obj, ruta="", periodos=None, out=None):
    out = out if out is not None else {}
    if isinstance(obj, list):
        if obj and all(isinstance(r, list) and r and _es_periodo(r[0]) for r in obj):
            for r in obj:
                for j, v in enumerate(r[1:], 1):
                    aplanar(v, f"{ruta}[{r[0]}][{j}]", None, out)
        elif periodos and len(obj) == len(periodos) and all(not isinstance(v, (list, dict)) for v in obj):
            for p, v in zip(periodos, obj):
                aplanar(v, f"{ruta}[{p}]", None, out)
        else:
            for i, v in enumerate(obj):
                aplanar(v, f"{ruta}[#{i}]", periodos, out)
    elif isinstance(obj, dict):
        ps = obj.get("years") if isinstance(obj.get("years"), list) else periodos
        for k, v in obj.items():
            if k != "years":
                aplanar(v, f"{ruta}.{k}" if ruta else k, ps, out)
    else:
        out[ruta] = obj
    return out


def comparar(actual, candidato):
    antes, ahora = aplanar(actual), aplanar(candidato)
    borrados, revisados, nuevos, mayores, estructura = [], [], [], [], []
    for ruta, valor in antes.items():
        if isinstance(valor, str):
            if ruta not in ahora or ahora[ruta] != valor:
                estructura.append({"ruta": ruta, "antes": valor, "ahora": ahora.get(ruta, "__FALTA__")})
            continue
        if not isinstance(valor, (int, float)) or isinstance(valor, bool):
            continue
        nuevo = ahora.get(ruta, "__FALTA__")
        if nuevo == "__FALTA__" or nuevo is None or (nuevo == 0 and valor != 0):
            borrados.append({"ruta": ruta, "antes": valor, "ahora": nuevo})
        elif isinstance(nuevo, (int, float)) and nuevo != valor:
            cambio = abs(nuevo - valor)
            relativo = cambio / max(abs(valor), 1)
            item = {"ruta": ruta, "antes": valor, "ahora": nuevo, "cambio_relativo": relativo}
            revisados.append(item)
            if cambio > 10 and relativo > 0.5:
                mayores.append(item)
    for ruta, valor in ahora.items():
        if isinstance(valor, (int, float)) and not isinstance(valor, bool) and not isinstance(antes.get(ruta), (int, float)):
            nuevos.append({"ruta": ruta, "ahora": valor})
    return {"nuevos": nuevos, "revisados": revisados, "borrados": borrados, "anomalias_mayores": mayores, "cambios_estructura": estructura}


def _escribir_atomico(path, contenido):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporal = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(contenido)
        os.replace(temporal, path)
    finally:
        if os.path.exists(temporal):
            os.unlink(temporal)


def _json(path, valor):
    _escribir_atomico(path, json.dumps(valor, ensure_ascii=False, indent=2) + "\n")


def _periodo_grupo(nombre, grupo):
    if nombre == "B":
        return str(grupo["cred"]["years"][-1])
    return periodo(grupo)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--index", default="../index.html")
    ap.add_argument("--datos", default="../datos/minerpa")
    ap.add_argument("--raw", help="usar una descarga existente en vez de consultar MINERPA")
    ap.add_argument("--raw-out", help="guardar la descarga como artefacto")
    ap.add_argument("--informe", required=True)
    ap.add_argument("--resumen", required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    comprobado = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    crudo = json.loads(Path(args.raw).read_text(encoding="utf-8")) if args.raw else descargar_todo()
    if args.raw_out:
        _json(Path(args.raw_out), crudo)
    extraido = transformar(crudo)

    index_path, datos_dir = Path(args.index), Path(args.datos)
    html = index_path.read_text(encoding="utf-8")
    viejo = cargar_constantes(html)
    candidato_d = dict(viejo["D"])
    for k in D_AUTOMATICAS:
        # Conservar la representación existente cuando sólo difieren ``1`` y
        # ``1.0``; evita diffs masivos sin cambio estadístico.
        candidato_d[k] = viejo["D"][k] if extraido["D"][k] == viejo["D"][k] else extraido["D"][k]
    candidato = {
        "D": candidato_d,
        "B": viejo["B"] if extraido["B"] == viejo["B"] else extraido["B"],
        "DM": viejo["DM"] if extraido["DM"] == viejo["DM"] else extraido["DM"],
    }

    cambios = comparar({"D": {k: viejo["D"][k] for k in D_AUTOMATICAS}, "B": viejo["B"], "DM": viejo["DM"]},
                       {"D": {k: candidato_d[k] for k in D_AUTOMATICAS}, "B": candidato["B"], "DM": candidato["DM"]})
    protegidas = [k for k in sorted(D_PROTEGIDAS) if extraido["D"].get(k) != viejo["D"].get(k)]
    errores = []
    if cambios["borrados"]:
        errores.append(f"{len(cambios['borrados'])} cifras o períodos se perderían")
    if cambios["anomalias_mayores"]:
        errores.append(f"{len(cambios['anomalias_mayores'])} revisiones superan el umbral de anomalía mayor")
    if cambios["cambios_estructura"]:
        errores.append(f"{len(cambios['cambios_estructura'])} etiquetas o categorías cambiaron")

    paginas = crudo.get("paginas") or {k.rstrip("/").rsplit("/", 1)[-1]: v for k, v in crudo.items()}
    anterior_manifest = {}
    manifest_path = datos_dir / "manifest.json"
    if manifest_path.exists():
        anterior_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest = {"fuente_intermedia": "MINERPA, Banco Interamericano de Desarrollo", "comprobado_utc": comprobado, "series": {}}
    for k in sorted(D_AUTOMATICAS):
        slug, fuente = FUENTES_D[k]
        valor = candidato_d[k]
        previo = anterior_manifest.get("series", {}).get(f"D.{k}", {})
        actualizado = comprobado if sha256_json(viejo["D"].get(k)) != sha256_json(valor) else previo.get("actualizado_utc", comprobado)
        manifest["series"][f"D.{k}"] = {"fuente": paginas.get(slug, {}).get("fuente") or fuente, "url": f"{BASE}/{slug}/", "periodo": periodo(valor), "comprobado_utc": comprobado, "actualizado_utc": actualizado, "sha256": sha256_json(valor)}
    for nombre in ("B", "DM"):
        fuente, slugs = FUENTES_GRUPO[nombre]
        previo = anterior_manifest.get("series", {}).get(nombre, {})
        actualizado = comprobado if sha256_json(viejo[nombre]) != sha256_json(candidato[nombre]) else previo.get("actualizado_utc", comprobado)
        manifest["series"][nombre] = {"fuente": fuente, "urls": [f"{BASE}/{s}/" for s in slugs], "periodo": _periodo_grupo(nombre, candidato[nombre]), "comprobado_utc": comprobado, "actualizado_utc": actualizado, "sha256": sha256_json(candidato[nombre])}

    informe = {"resultado": "BLOQUEADO" if errores else "APROBADO", "comprobado_utc": comprobado, "errores": errores, "cambios": cambios, "series_protegidas_ignoradas": protegidas, "sha256_crudo": sha256_json(crudo)}
    _json(Path(args.informe), informe)
    lineas = [f"## MINERPA: {'❌ BLOQUEADO' if errores else '✅ APROBADO'}", "", f"Comprobación: `{comprobado}`", "", f"Nuevas cifras: {len(cambios['nuevos'])} · revisadas: {len(cambios['revisados'])} · pérdidas: {len(cambios['borrados'])} · anomalías mayores: {len(cambios['anomalias_mayores'])} · cambios de estructura: {len(cambios['cambios_estructura'])}"]
    if protegidas:
        lineas += ["", "Series extraídas pero deliberadamente ignoradas: " + ", ".join(f"`D.{k}`" for k in protegidas) + "."]
    if errores:
        lineas += ["", "### Motivos del bloqueo"] + [f"- {e}" for e in errores]
    lineas += ["", "La publicación conserva la última versión válida mientras este informe no sea aprobado.", ""]
    _escribir_atomico(Path(args.resumen), "\n".join(lineas))
    if errores:
        raise ErrorMinerpa("; ".join(errores))

    contenido = html
    for nombre in ("D", "B", "DM"):
        contenido = reemplazar_constante(contenido, nombre, candidato[nombre])
    cambio_datos = contenido != html
    primera_carga = not (datos_dir / "series.json").exists()
    if not args.dry_run and (cambio_datos or primera_carga):
        if cambio_datos:
            _escribir_atomico(index_path, contenido)
        _json(datos_dir / "series.json", {"D": {k: candidato_d[k] for k in sorted(D_AUTOMATICAS)}, "B": candidato["B"], "DM": candidato["DM"]})
        _json(manifest_path, manifest)
        registro_path = datos_dir / "registro.json"
        registro = json.loads(registro_path.read_text(encoding="utf-8")) if registro_path.exists() else []
        registro.append({"comprobado_utc": comprobado, "nuevos": len(cambios["nuevos"]), "revisados": len(cambios["revisados"]), "sha256_crudo": informe["sha256_crudo"]})
        _json(registro_path, registro[-100:])
    print(json.dumps({"resultado": informe["resultado"], "cambio_datos": cambio_datos, "primera_carga": primera_carga, "nuevos": len(cambios["nuevos"]), "revisados": len(cambios["revisados"])}, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except ErrorMinerpa as e:
        print(f"ERROR MINERPA: {e}")
        raise SystemExit(1)
