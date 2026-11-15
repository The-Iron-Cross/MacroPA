#!/usr/bin/env python3
"""Falla si la validación candidata introduce atípicos que no tenía la base."""
import json
import sys

if len(sys.argv) != 3:
    raise SystemExit("uso: bloquear_atipicos_nuevos.py base.json candidato.json")

base = json.load(open(sys.argv[1], encoding="utf-8"))
candidato = json.load(open(sys.argv[2], encoding="utf-8"))
firma = lambda x: (x.get("donde", ""), x.get("msg", ""))
conocidos = {firma(x) for x in base.get("atipicos", [])}
nuevos = [x for x in candidato.get("atipicos", []) if firma(x) not in conocidos]
if nuevos:
    for x in nuevos:
        print(f"::error title=Anomalía nueva::{x.get('donde')}: {x.get('msg')}")
    print(f"Se bloquearon {len(nuevos)} anomalías nuevas; se requiere revisión.")
    raise SystemExit(1)
print(f"Sin anomalías nuevas ({len(conocidos)} atípicos históricos ya conocidos).")
