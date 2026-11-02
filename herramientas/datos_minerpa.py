import json,sys
d=json.load(open(sys.argv[1]))
def T(slug,i=0):
    for u,v in d.items():
        if u.rstrip("/").endswith(slug): return v["tables"][i]
    raise KeyError(slug)
def num(s):
    s=(s or "").replace(",","").strip()
    try: return float(s)
    except: return None
def long(slug,i,ycol,cols):
    t=T(slug,i); out=[]
    for r in t["rows"]:
        y=r[ycol]; vals=[num(r[c]) for c in cols]
        if any(v is not None for v in vals): out.append([y]+vals)
    return out
def wide(slug,i,labels):
    t=T(slug,i); years=t["headers"][2:]; res={}
    rows={r[1].strip():r for r in t["rows"]}
    for key,lab in labels.items():
        r=next(rr for name,rr in rows.items() if name.lower().startswith(lab.lower()))
        res[key]=[num(x) for x in r[2:]]
    # trim years with all None
    keep=[k for k,y in enumerate(years) if any(res[c][k] is not None for c in res)]
    return {"years":[years[k] for k in keep], **{c:[res[c][k] for k in keep] for c in res}}
D={}
# MACRO
g=long("producto-interno-bruto-real-y-nominal",0,1,[9,10,8])
D["pib_growth"]=[[int(r[0]),r[1],r[2]] for r in g if r[1] is not None]
D["pib_nom"]=[[int(r[0]),r[3]] for r in g if r[3] is not None]
D["pib_pc"]=[[int(r[0]),r[1],r[3]] for r in long("producto-interno-bruto-per-capita",0,1,[2,3,4,5])]
D["imae"]=[[r[0],r[2]] for r in long("indice-mensual-de-actividad-economica-base-2007",0,1,[2,3])]
D["inflacion"]=[[int(r[0]),r[1],r[2]] for r in long("tasa-de-inflacion-3",0,1,[2,3])]
t=T("producto-interno-bruto-real-segun-enfoque-de-oferta-base-2018",0)
sec=[]
for r in t["rows"]:
    name=r[1].strip(); a,b=num(r[-2]),num(r[-1])
    if name.startswith("PRODUCTO") or a is None: continue
    sec.append([name,b,round((b/a-1)*100,1) if a else None])
D["sectores"]={"year":t["headers"][-1],"rows":sec}
# FISCAL
D["spnf"]=wide("balance-del-sector-publico-no-financiero",1,{"ing":"Ingresos totales","gas":"Gastos totales","int":"Pago de intereses","prim":"Balance primario","tot":"Balance total"})
D["gc"]=wide("balance-del-gobierno-central",1,{"trib":"Tributarios","dir":"Directos","ind":"Indirectos","notrib":"No Tributarios","tot":"Balance Total"})
D["deuda"]=[[int(r[0])]+r[1:] for r in long("deuda-del-sector-publico",0,1,[2,3])]
comp=long("composicion-de-la-deuda-publica",0,1,[3,4])
D["deuda_comp"]=[[int(r[0])]+r[1:] for r in comp]
D["impuestos"]=wide("ingresos-impositivos",0,{"tot":"INGRESOS IMPOSITIVOS TOTALES","renta":"Imp. sobre la renta","consumo":"Impuestos generales sobre el consumo","selectivos":"Impuestos selectivos","comext":"Impuestos sobre el comercio exterior","prop":"Impuestos sobre la propiedad"})
D["acp"]=[[int(a[0]),a[1],b[1]] for a,b in zip(long("transferencias-de-la-autoridad-del-canal-al-estado",0,1,[2]),long("transferencias-de-la-autoridad-del-canal-al-estado",1,1,[2]))]
# EXTERNO
D["cc"]=wide("saldo-de-la-cuenta-corriente",1,{"cc":"Cuenta corriente"})
D["com"]=wide("saldo-de-la-cuenta-comercial",1,{"x":"Exportaciones de bienes y servicios","m":"Importaciones de bienes y servicios","bienes":"A. Saldo de bienes","serv":"B. Saldo de servicios"})
D["ied"]=wide("inversion-extranjera-directa",0,{"rec":"B. Inversión directa en la economía"})
D["ied_pct"]=wide("inversion-extranjera-directa",1,{"rec":"B. Inversión directa en la economía"})
D["xserv"]=wide("exportaciones-de-bienes-y-servicios",1,{"zlc":"1. Reexportaciones de la Zona Libre","nac":"2. Exportaciones nacionales","transp":"3. Transporte","viajes":"5. Viajes","fin":"8. Servicios financieros"})
# LABORAL
part=long("tasa-de-participacion",1,1,[4]); ocu=long("tasa-de-ocupacion",1,1,[4]); inf=long("tasa-de-informalidad",1,1,[4])
D["participacion"]=[[int(r[0]),r[1]] for r in part]
D["desempleo"]=[[int(r[0]),round(100-r[1],1)] for r in ocu]
D["informalidad"]=[[int(r[0]),r[1]] for r in inf]
t=T("mediana-salarial-por-actividad-economica",0)
D["salario"]={"years":t["headers"][2:],"total":[num(x) for x in t["rows"][0][2:]],
  "act":[[r[1].strip(),num(r[-1])] for r in t["rows"][1:]],"year":t["headers"][-1]}
# SOCIAL
D["pobreza"]=[[int(r[0])]+r[1:] for r in long("pobreza-general-y-pobreza-extrema",0,1,[2,3,4,5])]
D["gini"]=[[int(r[0]),r[1]] for r in long("coeficiente-de-gini",0,1,[2])]
def prov(slug):
    t=T(slug,0); return {"year":t["headers"][-1],"rows":[[r[1].strip(),num(r[-1])] for r in t["rows"]]}
D["pob_prov"]=prov("pobreza-general-por-provincias")
D["pobx_prov"]=prov("pobreza-extrema-por-provincias")
D["pobm_prov"]=prov("pobreza-multidimensional-por-provincias")
D["ing_prov"]=prov("ingreso-mensual-promedio-de-los-hogares-por-provincias")
# DEMOGRAFIA
D["poblacion"]=[[int(r[0]),r[1]] for r in long("serie-historica-de-poblacion-censo-2023",0,1,[2])]
dem=long("indicadores-demograficos",0,1,[3,9,11])
D["demo"]=[[int(r[0])]+r[1:] for r in dem]
D["edad"]=[[int(r[0]),r[1]] for r in long("indicadores-demograficos",1,1,[4])]
json.dump(D,open(sys.argv[2],"w"),ensure_ascii=False,separators=(",",":"))
for k,v in D.items():
    if isinstance(v,list): print(k,len(v),v[0],v[-1])
    else: print(k,{kk:(vv[-3:] if isinstance(vv,list) else vv) for kk,vv in v.items()})
