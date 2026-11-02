#!/bin/sh
# Descarga los datos del Presupuesto General del Estado desde Gestión Transparente Panamá (MEF).
# Uso: sh descargar_mef.sh carpeta_destino
set -e
D=${1:-mef}; mkdir -p "$D"; B=https://gestiontransparentepanama.mef.gob.pa/api/ServiciosPresupuestoNew
for y in 2022 2023 2024 2025 2026; do
  curl -s "$B/GetComparativePerVersiones?filtro=1,2,3&anyo=$y&opcion=1" -o "$D/comp_$y.json"
done
curl -s "$B/GetRecursosPerNivel?anyo=2026" -o "$D/nivel_2026.json"
G="SERVICIOS NO PERSONALES,SERVICIOS PERSONALES,TRANSFERENCIAS CORRIENTES,MATERIALES Y SUMINISTROS,SERVICIOS DE LA DEUDA,SIN GRUPO DE GASTO,INVERSIÓN FINANCIERA,MAQUINARIA Y EQUIPO,TRANSFERENCIAS DE CAPITAL,CONSTRUCCIONES POR CONTRATO,ASIGNACIONES GLOBALES"
curl -s -G "$B/ObtenerInfoPerGrupoDeGasto" --data-urlencode "filtro_periodos=2022,2023,2024,2025,2026" --data-urlencode "anyo=2026" --data-urlencode "filtro_gasto=$G" -o "$D/grupo.json"
