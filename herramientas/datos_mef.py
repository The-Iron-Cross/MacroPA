import json,sys,collections,os
src=sys.argv[1]; out=sys.argv[2]
Y=list(range(2022,2027))
def L(f): return json.load(open(os.path.join(src,f)))
tot={}; sect={}
for y in Y:
    g=L(f"comp_{y}.json")["infoGrafica"]
    t=collections.defaultdict(float); s=collections.defaultdict(dict)
    for r in g:
        t[r["labelGroup"]]+=r["rawValue"]; s[r["label"].strip()][r["labelGroup"]]=r["rawValue"]
    tot[y]=[round(t["Aprobado"],1),round(t["Modificado"],1),round(t["Ejecutado"],1)]
    sect[y]=sorted([[k,round(v.get("Aprobado",0),1),round(v.get("Modificado",0),1),round(v.get("Ejecutado",0),1)] for k,v in s.items()],key=lambda r:-r[2])
gr=collections.defaultdict(lambda: collections.defaultdict(dict))
for r in L("grupo.json")["infoRecursos"]:
    gr[r["labelGroup"]][int(r["periodo"])][r["label"]]=r["rawValue"]
grupos={k:{str(y):[round(v[y].get("Modificado",0),1),round(v[y].get("Ejecutado",0),1)] for y in Y} for k,v in gr.items()}
n=L("nivel_2026.json")["infoRecursos"]
inst=sorted([[r["label"].strip(),r["labelGroup"],round(r["rawValueDouble"]/1e6,1)] for r in n],key=lambda r:-r[2])
D={"years":Y,"tot":tot,"sect":sect,"grupos":grupos,"inst":inst[:15],"n_inst":len(n)}
json.dump(D,open(out,"w"),ensure_ascii=False,separators=(",",":"))
print(tot); print(sect[2026][:6]); print(list(grupos)[:12]); print(inst[:8])
