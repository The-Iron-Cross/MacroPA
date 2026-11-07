#!/usr/bin/env node
/* Agrega una línea al historial de actualizaciones automáticas y escribe el mensaje del commit.
   Uso: node historial.js informe.json "Nombre del flujo" URL_de_la_ejecución historial.md mensaje.txt
   Solo se llama cuando la validación aprobó y hay datos nuevos que guardar. */
"use strict";
const fs = require("fs");
const [informe, flujo, url, hist, msg] = process.argv.slice(2);
const inf = JSON.parse(fs.readFileSync(informe, "utf8"));
const c = inf.cambios || { nuevos: [], revisados: [], total_nuevos: 0, total_revisados: 0 };
const corto = g => g.map(x => x.serie + (x.periodos.length ? " " + (x.periodos.length > 2 ? x.periodos[0] + "…" + x.periodos.slice(-1)[0] : x.periodos.join(", ")) : "")).join("; ");
const ahora = new Date(), fecha = ahora.toISOString().slice(0, 16).replace("T", " ") + " UTC";
const que = [c.nuevos.length ? `nuevos: ${corto(c.nuevos)}` : "", c.revisados.length ? `revisados: ${corto(c.revisados)}` : ""].filter(Boolean).join(" · ") || "solo archivos de respaldo (sin cambios en la página)";

if (!fs.existsSync(hist)) fs.writeFileSync(hist,
  "# Historial de actualizaciones automáticas\n\n" +
  "Cada fila es una actualización que pasó la validación y se guardó. Las corridas sin datos nuevos no se anotan\n" +
  "(no generan commit) y las bloqueadas por la validación quedan en rojo en la pestaña Actions del repositorio,\n" +
  "con el motivo en su resumen.\n\n" +
  "Nombres de las series: DMEF = deuda pública mensual del MEF (tot total, ext externa, int interna, des desembolsos,\n" +
  "amo amortización, intr intereses, cpp costo promedio); DGI = recaudación de la DGI (m mensual, a acumulado).\n\n" +
  "| Fecha | Flujo | Qué cambió | Atípicos | Ejecución |\n|---|---|---|---|---|\n");
fs.appendFileSync(hist, `| ${fecha} | ${flujo} | ${que.replace(/\|/g, "/")} | ${inf.atipicos.length} | [ver](${url}) |\n`);
fs.writeFileSync(msg, `${flujo}: actualización automática ${ahora.toISOString().slice(0, 10)}\n\n` +
  `Validación: aprobada (${inf.errores.length} errores, ${inf.atipicos.length} atípicos).\n` +
  `Cifras nuevas: ${c.total_nuevos}. Cifras revisadas por la fuente: ${c.total_revisados}.\n` +
  (c.nuevos.length ? `Nuevos: ${corto(c.nuevos)}\n` : "") + (c.revisados.length ? `Revisados: ${corto(c.revisados)}\n` : "") +
  `Ejecución: ${url}\n`);
