# Herramientas para actualizar los datos

1. `python3 descargar_minerpa.py minerpa.json` descarga todas las tablas de MINERPA.
2. `python3 datos_minerpa.py minerpa.json panama.json` arma el resumen de indicadores.
3. `sh descargar_mef.sh mef` descarga el presupuesto desde Gestión Transparente Panamá.
4. `python3 datos_mef.py mef mef.json` arma el resumen del presupuesto.
5. `python3 armar_pagina.py plantilla.html panama.json mef.json "fecha" vista.html ../index.html` genera el sitio.

## Deuda pública mensual (MEF, Dirección de Financiamiento Público)

`python3 deuda_mef.py ../datos/deuda` baja los meses nuevos y vuelve a revisar los últimos 13 (agrega `--completo` para toda la historia desde dic-2015). Escribe en `datos/deuda/`:

- `saldos_mensuales.json`: saldo inicial, desembolsos, amortización, intereses, comisiones, variación cambiaria, ajustes y saldo final; por deuda externa/interna, tipo de crédito y fuente. Cada mes trae su URL de origen, fecha de corte, validaciones y estado.
- `cpp_mensual.json`: costo promedio ponderado oficial (total, por tipo de deuda, por cartera y por rango de tasa).
- `resumen.json`: tabla compacta para la app.
- `revisiones.json`: cada cambio que el MEF haga a un mes ya publicado (valor antes y después).
- `registro.json`: cada ejecución, con meses nuevos, revisados y errores.
- `originales/`: el XML exportado tal cual por el MEF.

`python3 embeber_deuda.py ../datos/deuda/resumen.json ../index.html` copia el resumen dentro de la página (constante `DMEF`). Ambos pasos los corre todos los días el flujo `.github/workflows/deuda-mef.yml`.
