#!/usr/bin/env node
/* Valida los datos de index.html sin abrir un navegador (lo usa GitHub Actions).

   Uso:
     node validar_macropa.js index.html [--base anterior.html] [--borrados error|aviso]
                            [--informe informe.json] [--resumen resumen.md]

   1. Lee las constantes de datos (D, M, B, DM, PM, L, DMEF, DGI) y la función
      validarMacroPA() de la propia página, y la ejecuta en Node. Así la regla es
      una sola: la misma que se ve con ?validar en el navegador.
   2. Con --base compara contra la versión anterior de la página:
      - un número que existía y ahora falta, es null o pasó a 0  -> "borrado"
      - un período que existía y ya no está                         -> "borrado"
      - números nuevos y números cambiados se listan como cambios.
      Con --borrados error (por omisión) un borrado bloquea la publicación; así una
      descarga fallida o incompleta nunca reemplaza cifras por ceros o vacíos.
   3. Escribe un informe JSON y un resumen en Markdown (para la página de la ejecución).

   Sale con código 1 si hay errores (bloquea) y 0 si no los hay. Los atípicos se
   informan pero no bloquean: validarMacroPA() ya separa "error" (inconsistencia
   confirmada) de "atipico" (valor raro que puede ser real). Nunca modifica archivos
   de datos. */
"use strict";
const fs = require("fs"), vm = require("vm");

const args = process.argv.slice(2), opt = {};
const pos = [];
for (let i = 0; i < args.length; i++) {
  if (args[i].startsWith("--")) opt[args[i].slice(2)] = args[++i]; else pos.push(args[i]);
}
if (!pos[0]) { console.error("Uso: node validar_macropa.js index.html [--base anterior.html] ..."); process.exit(2); }
const BORRADOS = opt.borrados || "error";
const NOMBRES = ["D", "M", "B", "DM", "PM", "L", "DMEF", "DGI"];

/* ---------- leer la página ---------- */
function cargar(ruta) {
  const html = fs.readFileSync(ruta, "utf8");
  const datos = {}, faltan = [];
  for (const n of NOMBRES) {
    const m = html.match(new RegExp("^const " + n + " = (.*);[ \\t]*(//.*)?$", "m"));
    if (!m) { faltan.push(n); continue; }
    const txt = m[1].replace(/^\/\*\w+\*\//, "").replace(/\/\*FIN_\w+\*\/$/, "");
    try { datos[n] = JSON.parse(txt); } catch (e) { faltan.push(n + " (no se puede leer: " + e.message + ")"); }
  }
  const ini = html.indexOf("function validarMacroPA(){");
  const fin = ini < 0 ? -1 : html.indexOf("\n}\n", ini);
  return { datos, faltan, funcion: ini < 0 || fin < 0 ? null : html.slice(ini, fin + 2) };
}

const R = [];   // {nivel: error|atipico|aviso, donde, msg}
const add = (nivel, donde, msg) => R.push({ nivel, donde, msg });
const pag = cargar(pos[0]);
pag.faltan.forEach(n => add("error", "index.html", `falta o está dañada la constante ${n}`));
if (!pag.funcion) add("error", "index.html", "no se encontró la función validarMacroPA()");

/* ---------- 1. reglas de la página ---------- */
if (!pag.faltan.length && pag.funcion) {
  try {
    const ctx = vm.createContext({ ...pag.datos, console });
    const res = vm.runInContext(pag.funcion + "\nvalidarMacroPA();", ctx, { timeout: 20000 });
    res.forEach(r => add(r.nivel, r.donde, r.msg));
  } catch (e) { add("error", "validarMacroPA", "la validación se interrumpió: " + e.message); }
}
// Reglas mínimas de estructura para las series que se actualizan solas
const d = pag.datos;
if (d.DMEF) {
  if (!Array.isArray(d.DMEF.p) || d.DMEF.p.length < 12) add("error", "DMEF", "la serie de deuda está vacía o incompleta");
  else if (d.DMEF.tot.slice(-1)[0] == null) add("error", "DMEF", `el último mes (${d.DMEF.p.slice(-1)[0]}) no trae saldo total`);
}
if (d.DGI) {
  if (!Array.isArray(d.DGI.p) || d.DGI.p.length < 12) add("error", "DGI", "la serie de recaudación está vacía o incompleta");
  else if (!d.DGI.a || !Array.isArray(d.DGI.a.tributarios)) add("error", "DGI", "falta la serie de ingresos tributarios");
}

/* ---------- 2. comparación con la versión anterior ---------- */
const esPeriodo = v => typeof v === "number" ? Number.isInteger(v) && v > 1900 && v < 2200
  : typeof v === "string" && /^(\d{4}(-\d{2})?|[a-z]{3}-\d{2})$/.test(v);
// Convierte los datos en {ruta: valor}, usando el período (y no la posición) como clave,
// para que agregar meses al principio o al final no se confunda con un cambio.
function aplanar(o, ruta, per, out) {
  if (Array.isArray(o)) {
    if (o.length && o.every(r => Array.isArray(r) && r.length && esPeriodo(r[0]))) {
      o.forEach(r => r.slice(1).forEach((v, j) => aplanar(v, `${ruta}[${r[0]}][${j + 1}]`, null, out)));
      out.__periodos[ruta] = o.map(r => String(r[0]));
    } else if (per && o.length === per.length && o.every(v => v == null || typeof v !== "object")) {
      o.forEach((v, i) => aplanar(v, `${ruta}[${per[i]}]`, null, out));
    } else o.forEach((v, i) => aplanar(v, `${ruta}[#${i}]`, per, out));
  } else if (o && typeof o === "object") {
    const p = Array.isArray(o.p) && o.p.every(esPeriodo) ? o.p : Array.isArray(o.years) && o.years.every(esPeriodo) ? o.years : per;
    if (p !== per) out.__periodos[ruta] = p.map(String);
    for (const [k, v] of Object.entries(o)) if (!(v === p && (k === "p" || k === "years"))) aplanar(v, `${ruta}.${k}`, p, out);
  } else out[ruta] = o;
  return out;
}
const cambios = { nuevos: [], revisados: [], borrados: [] };
if (opt.base) {
  let base = null;
  try { base = cargar(opt.base); } catch (e) { add("aviso", "comparación", "no se pudo leer la versión anterior: " + e.message); }
  if (base) for (const n of NOMBRES) {
    if (!base.datos[n] || !d[n]) continue;
    const A = aplanar(base.datos[n], n, null, { __periodos: {} }), N = aplanar(d[n], n, null, { __periodos: {} });
    for (const [ruta, ps] of Object.entries(A.__periodos)) {
      const ahora = new Set(N.__periodos[ruta] || []);
      const perdidos = ps.filter(x => !ahora.has(x));
      if (perdidos.length) cambios.borrados.push({ ruta, antes: perdidos.join(", "), ahora: "período eliminado" });
    }
    for (const [ruta, v] of Object.entries(A)) {
      if (ruta === "__periodos" || typeof v !== "number") continue;
      const w = N[ruta];
      if (!(ruta in N) || w == null) cambios.borrados.push({ ruta, antes: v, ahora: w === undefined ? "falta" : "vacío" });
      else if (w === 0 && v !== 0) cambios.borrados.push({ ruta, antes: v, ahora: 0 });
      else if (typeof w === "number" && w !== v) cambios.revisados.push({ ruta, antes: v, ahora: w });
    }
    for (const [ruta, w] of Object.entries(N))
      if (ruta !== "__periodos" && typeof w === "number" && typeof A[ruta] !== "number") cambios.nuevos.push({ ruta, ahora: w });
  }
  cambios.borrados.forEach(b => add(BORRADOS === "error" ? "error" : "aviso", b.ruta,
    `dato existente reemplazado (antes ${b.antes}, ahora ${b.ahora}): no se aceptan ceros, vacíos ni datos faltantes en lugar de cifras publicadas`));
}

/* ---------- 3. informe ---------- */
// Agrupa los cambios por serie: "DMEF.tot: 2026-09, 2026-10"
const serieDe = r => r.replace(/\[[^\]]*\](?=(\[\d+\])?$)/, "").replace(/\[(\d+)\]$/, " col $1");
const perDe = r => (r.match(/\[([^\]#]+)\](?:\[\d+\])?$/) || [])[1] || "";
const agrupar = lista => {
  const g = {};
  lista.forEach(c => { const s = serieDe(c.ruta); (g[s] = g[s] || new Set()).add(perDe(c.ruta)); });
  return Object.entries(g).map(([s, ps]) => ({ serie: s, periodos: [...ps].filter(Boolean) }));
};
const er = R.filter(r => r.nivel === "error"), at = R.filter(r => r.nivel === "atipico"), av = R.filter(r => r.nivel === "aviso");
const informe = {
  archivo: pos[0], fecha_utc: new Date().toISOString(), resultado: er.length ? "BLOQUEADO" : "APROBADO",
  errores: er, atipicos: at, avisos: av,
  cambios: opt.base ? { nuevos: agrupar(cambios.nuevos), revisados: agrupar(cambios.revisados), borrados: cambios.borrados,
    total_nuevos: cambios.nuevos.length, total_revisados: cambios.revisados.length } : null,
};
if (opt.informe) fs.writeFileSync(opt.informe, JSON.stringify(informe, null, 1));

const lista = (g, max = 15) => g.slice(0, max).map(x => `- ${x.serie}${x.periodos.length ? ": " + (x.periodos.length > 6 ? x.periodos.slice(0, 3).join(", ") + " … " + x.periodos.slice(-2).join(", ") + ` (${x.periodos.length} períodos)` : x.periodos.join(", ")) : ""}`).join("\n") + (g.length > max ? `\n- … y ${g.length - max} series más` : "");
const filas = (xs, max = 40) => xs.slice(0, max).map(r => `| ${r.donde} | ${String(r.msg).replace(/\|/g, "/")} |`).join("\n") + (xs.length > max ? `\n| … | y ${xs.length - max} más (ver informe) |` : "");
let md = `## Validación de datos de MacroPA: ${er.length ? "❌ BLOQUEADO" : "✅ APROBADO"}\n\n`;
md += `${er.length} errores · ${at.length} valores atípicos · ${av.length} avisos\n\n`;
if (er.length) md += `### Errores (impiden guardar y publicar)\n| Dónde | Motivo |\n|---|---|\n${filas(er)}\n\n`;
if (informe.cambios) {
  const c = informe.cambios;
  md += `### Cambios frente a la versión anterior\n`;
  md += c.nuevos.length || c.revisados.length || c.borrados.length ? "" : "Sin cambios en los datos.\n";
  if (c.nuevos.length) md += `**Datos nuevos (${c.total_nuevos} cifras):**\n${lista(c.nuevos)}\n\n`;
  if (c.revisados.length) md += `**Cifras revisadas por la fuente (${c.total_revisados}):**\n${lista(c.revisados)}\n\n`;
  if (c.borrados.length) md += `**Cifras que se perderían (${c.borrados.length}):** ver errores.\n\n`;
}
if (at.length) md += `### Valores atípicos (se registran, no bloquean)\n| Dónde | Detalle |\n|---|---|\n${filas(at)}\n\n`;
if (av.length) md += `### Avisos\n| Dónde | Detalle |\n|---|---|\n${filas(av)}\n\n`;
if (opt.resumen) fs.writeFileSync(opt.resumen, md);
console.log(md);
// Anotaciones visibles en la lista de ejecuciones de GitHub
if (process.env.GITHUB_ACTIONS) {
  er.slice(0, 10).forEach(r => console.log(`::error title=Validación MacroPA::${r.donde}: ${r.msg}`));
  if (at.length) console.log(`::notice title=Validación MacroPA::${at.length} valores atípicos registrados (no bloquean)`);
}
process.exit(er.length ? 1 : 0);
