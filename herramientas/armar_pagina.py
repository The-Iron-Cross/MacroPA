import sys
tpl,data,mef,updated,artifact_out,site_out=sys.argv[1:7]
p=open(tpl).read()
p=p.replace("/*DATA*/null",open(data).read()).replace("/*MEF*/null",open(mef).read()).replace("/*UPDATED*/",updated)
open(artifact_out,"w").write(p)
head='<!doctype html>\n<html lang="es">\n<head>\n<meta charset="utf-8">\n<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n<meta name="theme-color" content="#0b4f8a">\n'
i=p.index('</style>')+len('</style>')
site=head+p[:i]+'\n<style>:root{padding-top:env(safe-area-inset-top);padding-bottom:env(safe-area-inset-bottom)}body{margin:0}img{max-width:100%}[hidden]{display:none!important}</style>\n</head>\n<body>\n'+p[i:]+'\n</body>\n</html>\n'
open(site_out,"w").write(site)
