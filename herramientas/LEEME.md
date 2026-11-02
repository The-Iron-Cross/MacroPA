# Herramientas para actualizar los datos

1. `python3 descargar_minerpa.py minerpa.json` descarga todas las tablas de MINERPA.
2. `python3 datos_minerpa.py minerpa.json panama.json` arma el resumen de indicadores.
3. `sh descargar_mef.sh mef` descarga el presupuesto desde Gestión Transparente Panamá.
4. `python3 datos_mef.py mef mef.json` arma el resumen del presupuesto.
5. `python3 armar_pagina.py plantilla.html panama.json mef.json "fecha" vista.html ../index.html` genera el sitio.
